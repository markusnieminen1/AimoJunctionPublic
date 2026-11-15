"""On-sale deals discovery (listPrice > catalogPrice) with diet & promotion scoring."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.aimo.client import AimoClient
from app.aimo.normalise import check_products_live
from app.errors import AppError
from app.llm.prep_list import default_delivery_date
from app.models import Product
from app.schemas import (
    DealItemOut,
    DealsResponse,
    Principal,
    QuoteLine,
    _parse_delivery_date_str,
)
from app.services.catalog import (
    is_food_category,
    product_passes_diets,
    product_to_out,
    resolve_diet_keys,
)
from app.services.promotions import evaluate_product_promotions, get_active_promotions

_MAX_LIVE_CHECK = 50
_SQL_BATCH_SIZE = 200
_MAX_SQL_SCAN = 1000


def extract_list_prices(
    aimo_client: AimoClient,
    product_ids: list[str],
    requested_delivery_date: str,
    customer_id: str,
) -> dict[str, float]:
    """Fetch raw price response (cached in AimoClient) and map productId -> listPrice.value."""
    if not product_ids:
        return {}
    raw = aimo_client.price(
        product_ids,
        requested_date=requested_delivery_date,
        customer=customer_id,
    )
    out: dict[str, float] = {}
    for item in raw.get("products") or []:
        if not isinstance(item, dict) or item.get("error"):
            continue
        pid = str(item.get("productId") or "")
        lp = item.get("listPrice")
        if pid and isinstance(lp, dict) and lp.get("value") is not None:
            out[pid] = float(lp["value"])
    return out


def compute_discount_metrics(
    line: QuoteLine,
    list_price_ex_vat: float | None,
) -> tuple[bool, float, float]:
    """Compute (on_sale, discount_pct, savings_per_unit) from listPrice vs catalogPrice."""
    if (
        list_price_ex_vat is None
        or line.unit_price_eur is None
        or list_price_ex_vat <= 0
        or list_price_ex_vat <= line.unit_price_eur + 1e-6
    ):
        return False, 0.0, 0.0
    savings = round(list_price_ex_vat - line.unit_price_eur, 2)
    pct = round(((list_price_ex_vat - line.unit_price_eur) / list_price_ex_vat) * 100.0, 2)
    return savings > 0, pct, savings


def fixture_priced_ids(aimo_client: AimoClient) -> set[str]:
    """Return product IDs present in fixture price.json when running in fixture mode."""
    if aimo_client.mode != "fixture":
        return set()
    price_file = aimo_client.fixture_dir / "price.json"
    if not price_file.is_file():
        return set()
    try:
        raw = json.loads(price_file.read_text(encoding="utf-8"))
        return {
            str(item["productId"])
            for item in (raw.get("products") or [])
            if isinstance(item, dict) and not item.get("error") and item.get("productId")
        }
    except Exception:
        return set()


def _is_valio_brand(product: Product) -> bool:
    brand = (product.brand or "").lower()
    vendor = (product.vendor or "").lower()
    return "valio" in brand or "oddlygood" in brand or "valio" in vendor


def find_deals(
    db: Session,
    aimo_client: AimoClient,
    principal: Principal,
    *,
    q: str | None = None,
    category: str | None = None,
    diet: str | None = None,
    valio_only: bool = False,
    on_sale_only: bool = True,
    requested_delivery_date: str | None = None,
    limit: int = 20,
) -> DealsResponse:
    """Discover diet-safe catalog deals (listPrice > catalogPrice) with promotion boosts."""
    diet_keys = resolve_diet_keys(diet, None)

    if requested_delivery_date and requested_delivery_date.strip():
        try:
            delivery_date = _parse_delivery_date_str(requested_delivery_date)
        except ValueError as exc:
            raise AppError("VALIDATION_ERROR", 422, str(exc)) from exc
    else:
        delivery_date = default_delivery_date()

    active_promos = get_active_promotions(db, principal.company_id)

    stmt = select(Product)
    if category and category.strip():
        stmt = stmt.where(Product.category.like(f"{category.strip()}%"))
    if valio_only:
        stmt = stmt.where(
            or_(
                func.lower(func.coalesce(Product.brand, "")).like("%valio%"),
                func.lower(func.coalesce(Product.brand, "")).like("%oddlygood%"),
                func.lower(func.coalesce(Product.vendor, "")).like("%valio%"),
            )
        )

    fixture_ids = fixture_priced_ids(aimo_client)
    q_clean = (q or "").strip().lower()
    if q_clean:
        tokens = [tok for tok in q_clean.split() if tok]
        for tok in tokens:
            escaped = tok.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            pattern = f"%{escaped}%"
            stmt = stmt.where(
                or_(
                    func.lower(Product.search_text).like(pattern, escape="\\"),
                    and_(
                        Product.search_text == "",
                        or_(
                            func.lower(Product.id).like(pattern, escape="\\"),
                            func.lower(Product.name).like(pattern, escape="\\"),
                            func.lower(func.coalesce(Product.name_fi, "")).like(
                                pattern, escape="\\"
                            ),
                            func.lower(func.coalesce(Product.name_en, "")).like(
                                pattern, escape="\\"
                            ),
                            func.lower(func.coalesce(Product.brand, "")).like(pattern, escape="\\"),
                        ),
                    ),
                )
            )

    stmt = stmt.order_by(Product.id.asc())

    def _passes_filters(prod: Product) -> bool:
        if valio_only and not _is_valio_brand(prod):
            return False
        if diet_keys:
            passed, _ = product_passes_diets(prod, diet_keys)
            if not passed:
                return False
        return True

    diet_safe: list[Product] = []
    if fixture_ids and not q_clean:
        # Prefer fixture-priced SKUs when in fixture mode so full DB queries stay fast & rich.
        fixture_rows = db.scalars(stmt.where(Product.id.in_(fixture_ids))).all()
        diet_safe.extend(p for p in fixture_rows if _passes_filters(p))
        if len(diet_safe) < _MAX_LIVE_CHECK:
            offset = 0
            max_scan = _MAX_SQL_SCAN if diet_keys else _SQL_BATCH_SIZE
            while len(diet_safe) < _MAX_LIVE_CHECK and offset < max_scan:
                batch = list(
                    db.scalars(
                        stmt.where(Product.id.not_in(fixture_ids))
                        .offset(offset)
                        .limit(_SQL_BATCH_SIZE)
                    ).all()
                )
                if not batch:
                    break
                diet_safe.extend(p for p in batch if _passes_filters(p))
                if len(batch) < _SQL_BATCH_SIZE:
                    break
                offset += _SQL_BATCH_SIZE
    else:
        offset = 0
        max_scan = _MAX_SQL_SCAN if diet_keys else _SQL_BATCH_SIZE
        while len(diet_safe) < _MAX_LIVE_CHECK and offset < max_scan:
            batch = list(db.scalars(stmt.offset(offset).limit(_SQL_BATCH_SIZE)).all())
            if not batch:
                break
            diet_safe.extend(p for p in batch if _passes_filters(p))
            if len(batch) < _SQL_BATCH_SIZE:
                break
            offset += _SQL_BATCH_SIZE

    # Order shortlist deterministically, putting food items and fixture-priced items first.
    diet_safe.sort(
        key=lambda p: (
            0 if p.id in fixture_ids else 1,
            0 if is_food_category(p.category) else 1,
            0 if _is_valio_brand(p) else 1,
            p.id,
        )
    )
    shortlist = diet_safe[:_MAX_LIVE_CHECK]
    if not shortlist:
        return DealsResponse(
            items=[],
            total=0,
            requested_delivery_date=delivery_date,
            promotions_applied=[],
        )

    shortlist_ids = [p.id for p in shortlist]
    prod_by_id = {p.id: p for p in shortlist}

    lines = check_products_live(
        db=db,
        aimo_client=aimo_client,
        product_ids=shortlist_ids,
        quantities={pid: 1.0 for pid in shortlist_ids},
        requested_delivery_date=delivery_date,
        customer_id=principal.company_id,
    )
    list_prices = extract_list_prices(
        aimo_client,
        shortlist_ids,
        delivery_date,
        principal.company_id,
    )

    scored_items: list[tuple[tuple[Any, ...], DealItemOut]] = []
    promotions_applied_set: dict[str, None] = {}
    from app.services.admin_ranking import (
        evaluate_aimo_campaign_score,
        evaluate_brand_preference_score,
        get_active_aimo_campaigns,
        get_active_brand_preferences,
    )

    active_brand_prefs = get_active_brand_preferences(db, principal.company_id)
    active_aimo_campaigns = get_active_aimo_campaigns(db)

    for line in lines:
        if "NO_PRICE" in line.flags or "SALES_BLOCKED" in line.flags:
            continue
        prod = prod_by_id[line.product_id]
        list_price = list_prices.get(line.product_id)
        on_sale, discount_pct, savings_per_unit = compute_discount_metrics(line, list_price)

        if on_sale_only and not on_sale:
            continue

        promoted, promo_reasons, promo_score = evaluate_product_promotions(
            product_id=prod.id,
            brand=prod.brand,
            category=prod.category,
            rules=active_promos,
            on_sale=on_sale,
        )
        for reason in promo_reasons:
            promotions_applied_set.setdefault(reason, None)

        brand_pref_score = evaluate_brand_preference_score(
            brand=prod.brand,
            category=prod.category,
            preferences=active_brand_prefs,
        )
        aimo_campaign_score = evaluate_aimo_campaign_score(
            product_id=prod.id,
            brand=prod.brand,
            category=prod.category,
            campaigns=active_aimo_campaigns,
        )

        deal_item = DealItemOut(
            product=product_to_out(prod),
            quote_line=line,
            list_price_ex_vat=round(list_price, 2) if list_price is not None else None,
            discount_pct=discount_pct,
            savings_per_unit=savings_per_unit,
            on_sale=on_sale,
            promoted=promoted,
            promotion_reasons=promo_reasons,
        )
        sort_key = (
            0 if line.delivery_status == "ON_TIME" else 1,
            0 if promoted else 1,
            -(promo_score + brand_pref_score),
            -aimo_campaign_score,
            -discount_pct,
            -savings_per_unit,
            prod.id,
        )
        scored_items.append((sort_key, deal_item))

    scored_items.sort(key=lambda pair: pair[0])
    all_deals = [pair[1] for pair in scored_items]
    sliced = all_deals[:limit]

    return DealsResponse(
        items=sliced,
        total=len(all_deals),
        requested_delivery_date=delivery_date,
        promotions_applied=list(promotions_applied_set.keys()),
    )
