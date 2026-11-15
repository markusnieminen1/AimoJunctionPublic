"""Diet-safe, cut-off-feasible product substitution service."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.aimo.client import AimoClient
from app.aimo.normalise import check_products_live
from app.domain import valio_rules
from app.errors import AppError
from app.llm.decisions import (
    DecisionProvider,
    get_decision_provider,
    is_culinary_substitute_fit,
)
from app.models import Product
from app.schemas import QuoteLine, SubstituteCandidate, SubstitutesResponse, SubstituteTier
from app.services.catalog import (
    product_passes_diets,
    product_to_out,
    resolve_diet_keys,
)

_MAX_SHORTLIST = 24
_BLOCKING_FLAGS = frozenset({"NOT_DELIVERABLE", "CUTOFF_PASSED", "SALES_BLOCKED", "NO_PRICE"})
_INFERRED_DIETS = ("LACTOSE_FREE", "MILK_FREE", "GLUTEN_FREE_COELIAC", "VEGAN")
_CHILLED_CLASSES = frozenset({"chilled_strict", "chilled", "produce_cool"})
_TIER_RANK: dict[str, int] = {"BEST": 0, "BETTER": 1, "GOOD": 2}

_TEMP_CLASS_LABELS: dict[str, str] = {
    "chilled_strict": "Chilled strict",
    "chilled": "Chilled",
    "produce_cool": "Fresh produce",
    "cool_ambient": "Cool ambient",
    "frozen": "Frozen",
    "ambient": "Ambient",
    "unknown": "Unknown temp",
}


def _format_temperature(p: Product) -> str:
    cls_label = _TEMP_CLASS_LABELS.get(p.temperature_class or "unknown", "Unknown temp")
    band, _ = valio_rules.TEMPERATURE.get(p.temperature_condition, ("missing", "unknown"))
    if band and band != "missing":
        band_fmt = band.replace(" C", " °C")
        if p.temperature_condition == 8 and "uht" in (p.name or "").lower():
            return f"Ambient / UHT ({band_fmt})"
        return f"{cls_label} ({band_fmt})"
    return cls_label


def _is_better_fill_than_original(cand_line: QuoteLine, orig_line: QuoteLine) -> bool:
    if orig_line.delivery_status in ("LATE", "NOT_DELIVERABLE"):
        return True
    if "PARTIAL" in orig_line.flags:
        if "PARTIAL" not in cand_line.flags:
            return True
        if (
            cand_line.available_amount is not None
            and orig_line.available_amount is not None
            and cand_line.available_amount > orig_line.available_amount
        ):
            return True
    return False


def _is_feasible_substitute(cand_line: QuoteLine, orig_line: QuoteLine) -> bool:
    if any(flag in _BLOCKING_FLAGS for flag in cand_line.flags):
        return False
    if cand_line.delivery_status in ("NOT_DELIVERABLE", "LATE"):
        return False
    if cand_line.delivery_status == "ON_TIME":
        return True
    return _is_better_fill_than_original(cand_line, orig_line)


def _format_deposit_note(deposits: list[dict]) -> str | None:
    if not deposits:
        return None
    parts: list[str] = []
    for dep in deposits:
        dep_id = str(dep.get("depositId") or dep.get("id") or dep.get("product") or "").strip()
        eur = valio_rules.DEPOSIT_EUR.get(dep_id)
        if eur is not None:
            parts.append(f"€{eur:.2f}")
        elif dep_id:
            parts.append(f"code {dep_id}")
    if parts:
        return f"Deposit applies ({', '.join(parts)})"
    return "Deposit applies"


def _format_origin_label(raw_code: str | None) -> str:
    if not raw_code or not raw_code.strip():
        return "Unknown"
    code = raw_code.strip()
    odd_num = valio_rules.COUNTRY_NUMERIC_ODD.get(code.upper())
    if odd_num:
        return f"{code} ({odd_num})"
    odd_alpha = valio_rules.COUNTRY_ALPHA2_ODD.get(code.lower())
    if odd_alpha:
        return f"{code} ({odd_alpha})"
    return code.upper()


def _same_storage_band(orig: Product, cand: Product) -> bool:
    orig_uht = orig.temperature_condition == 8 and "uht" in (orig.name or "").lower()
    cand_uht = cand.temperature_condition == 8 and "uht" in (cand.name or "").lower()
    if orig_uht != cand_uht:
        return False
    orig_cls = orig.temperature_class or "unknown"
    cand_cls = cand.temperature_class or "unknown"
    if orig_cls in _CHILLED_CLASSES and cand_cls in _CHILLED_CLASSES:
        return True
    return orig_cls == cand_cls


def _compute_substitute_tier(
    orig: Product,
    cand: Product,
    cand_line: QuoteLine,
    explicit_sub_set: set[str],
) -> SubstituteTier:
    is_on_time_full = cand_line.delivery_status == "ON_TIME" and "PARTIAL" not in cand_line.flags
    if not is_on_time_full:
        return "GOOD"
    cand_subs = {str(sid) for sid in (cand.substitutions or [])}
    is_catalog_sub = (cand.id in explicit_sub_set) or (orig.id in cand_subs)
    is_valio = "valio" in (cand.brand or "").lower()
    cand_origin = (cand.country_of_origin or "").strip().upper()
    orig_origin = (orig.country_of_origin or "").strip().upper()
    good_origin = cand_origin == "FI" or (bool(cand_origin) and cand_origin == orig_origin)
    cand_is_chilled = (cand.temperature_class or "") in _CHILLED_CLASSES and not (
        cand.temperature_condition == 8 and "uht" in (cand.name or "").lower()
    )
    same_band = _same_storage_band(orig, cand)
    if (is_valio or is_catalog_sub) and good_origin and cand_is_chilled:
        return "BEST"
    if is_catalog_sub and same_band and good_origin:
        return "BEST"
    if (is_valio or is_catalog_sub) and good_origin:
        return "BETTER"
    if same_band and good_origin:
        return "BETTER"
    return "GOOD"


def _build_tradeoffs(
    orig: Product,
    cand: Product,
    orig_line: QuoteLine,
    cand_line: QuoteLine,
    price_delta_eur: float | None,
) -> list[str]:
    tradeoffs: list[str] = []

    cand_origin_lbl = _format_origin_label(cand.country_of_origin)
    orig_origin_lbl = _format_origin_label(orig.country_of_origin)
    if cand.country_of_origin and cand_origin_lbl != orig_origin_lbl:
        tradeoffs.append(f"Origin: {cand_origin_lbl} vs {orig_origin_lbl} (verified Synkka)")
    elif cand.country_of_origin:
        tradeoffs.append(f"Origin: {cand_origin_lbl} (verified Synkka)")

    if (
        cand.temperature_class != orig.temperature_class
        or cand.temperature_condition != orig.temperature_condition
    ):
        tradeoffs.append(f"Storage: {_format_temperature(cand)} vs {_format_temperature(orig)}")

    if cand.sales_unit != orig.sales_unit:
        tradeoffs.append(f"Sales unit: {cand.sales_unit} vs {orig.sales_unit}")

    if cand.allowed_lot_size is not None and cand.allowed_lot_size > 1:
        tradeoffs.append(f"Lot size multiple: {cand.allowed_lot_size:g} {cand.sales_unit}")
    elif (
        cand.allowed_lot_size is not None
        and orig.allowed_lot_size is not None
        and cand.allowed_lot_size != orig.allowed_lot_size
    ):
        tradeoffs.append(f"Allowed lot size: {cand.allowed_lot_size:g} {cand.sales_unit}")

    deposit_note = _format_deposit_note(cand.deposits or [])
    if deposit_note:
        tradeoffs.append(deposit_note)

    if price_delta_eur is not None:
        if cand.sales_unit == orig.sales_unit:
            if price_delta_eur > 0:
                tradeoffs.append(f"Price +€{price_delta_eur:.2f}/{cand.sales_unit}")
            elif price_delta_eur < 0:
                tradeoffs.append(f"Price -€{abs(price_delta_eur):.2f}/{cand.sales_unit}")
            else:
                tradeoffs.append("Same unit price")
        elif cand_line.unit_price_eur is not None and orig_line.unit_price_eur is not None:
            tradeoffs.append(
                f"Unit price €{cand_line.unit_price_eur:.2f}/{cand.sales_unit} "
                f"vs €{orig_line.unit_price_eur:.2f}/{orig.sales_unit}"
            )

    if (
        cand.co2_kg_per_kg is not None
        and orig.co2_kg_per_kg is not None
        and cand.co2_kg_per_kg != orig.co2_kg_per_kg
    ):
        co2_diff = cand.co2_kg_per_kg - orig.co2_kg_per_kg
        sign = "+" if co2_diff > 0 else "-"
        tradeoffs.append(f"CO2 {sign}{abs(co2_diff):.2f} kg/kg")

    return tradeoffs


def find_substitutes(
    db: Session,
    aimo_client: AimoClient,
    *,
    product_id: str,
    quantity: float,
    requested_delivery_date: str,
    diet: str | None = None,
    customer_id: str = "123",
    decision_provider: DecisionProvider | None = None,
) -> SubstitutesResponse:
    """Find, verify, and rank diet-safe and delivery-feasible substitutes."""
    orig = db.get(Product, product_id)
    if orig is None:
        raise AppError(
            "NOT_FOUND",
            404,
            f"Product {product_id} not found",
            details={"product_id": product_id},
        )

    explicit_diets = resolve_diet_keys(diet, None)
    inferred_diets = [dk for dk in _INFERRED_DIETS if product_passes_diets(orig, [dk])[0]]
    required_diets = list(dict.fromkeys([*explicit_diets, *inferred_diets]))

    # Gather candidate pool: catalog substitutions first, then same-category items
    explicit_sub_ids = [str(sid) for sid in (orig.substitutions or []) if str(sid) != orig.id]
    explicit_sub_set = set(explicit_sub_ids)

    pool_by_id: dict[str, Product] = {}
    ordered_candidates: list[Product] = []

    if explicit_sub_ids:
        sub_rows = db.scalars(select(Product).where(Product.id.in_(explicit_sub_ids))).all()
        sub_map = {p.id: p for p in sub_rows}
        for sid in explicit_sub_ids:
            if sid in sub_map and sid not in pool_by_id:
                pool_by_id[sid] = sub_map[sid]
                ordered_candidates.append(sub_map[sid])

    same_cat_rows = db.scalars(
        select(Product).where(Product.category == orig.category, Product.id != orig.id)
    ).all()
    same_cat_sorted = sorted(
        same_cat_rows,
        key=lambda p: (
            0 if (p.delivery_reliability is not None and p.co2_kg_per_kg is not None) else 1,
            -(float(p.delivery_reliability) if p.delivery_reliability is not None else 0.0),
            p.id,
        ),
    )
    for prod in same_cat_sorted:
        if prod.id not in pool_by_id:
            pool_by_id[prod.id] = prod
            ordered_candidates.append(prod)

    resolved_decision_provider = decision_provider or get_decision_provider()
    shortlisted: list[Product] = []
    remote_culinary_calls = 0
    for cand in ordered_candidates:
        passed, _ = product_passes_diets(cand, required_diets)
        if not passed:
            continue
        is_same_or_sub = cand.id in explicit_sub_set or (
            bool(cand.category) and cand.category == orig.category
        )
        effective_provider = resolved_decision_provider
        if not is_same_or_sub and remote_culinary_calls >= 2:
            effective_provider = None
        culinary_fit = is_culinary_substitute_fit(orig, cand, provider=effective_provider)
        if not is_same_or_sub and effective_provider is not None:
            remote_culinary_calls += 1
        if not culinary_fit.answer:
            continue
        shortlisted.append(cand)
        if len(shortlisted) >= _MAX_SHORTLIST:
            break

    if not shortlisted:
        return SubstitutesResponse(requested_product_id=product_id, diet=diet, items=[])

    check_ids = [orig.id, *[cand.id for cand in shortlisted]]
    qty_map = {pid: float(quantity) for pid in check_ids}
    checked_lines = check_products_live(
        db=db,
        aimo_client=aimo_client,
        product_ids=check_ids,
        quantities=qty_map,
        requested_delivery_date=requested_delivery_date,
        customer_id=customer_id,
    )
    orig_line = checked_lines[0]
    cand_lines = checked_lines[1:]

    from app.services.admin_ranking import (
        evaluate_aimo_campaign_score,
        evaluate_brand_preference_score,
        get_active_aimo_campaigns,
        get_active_brand_preferences,
    )

    active_brand_prefs = get_active_brand_preferences(db, customer_id)
    active_aimo_campaigns = get_active_aimo_campaigns(db)

    items: list[SubstituteCandidate] = []
    brand_pref_by_id: dict[str, float] = {}
    aimo_campaign_by_id: dict[str, float] = {}
    for cand, cand_line in zip(shortlisted, cand_lines, strict=True):
        if not _is_feasible_substitute(cand_line, orig_line):
            continue

        price_delta_eur: float | None = None
        if cand_line.unit_price_eur is not None and orig_line.unit_price_eur is not None:
            price_delta_eur = round(cand_line.unit_price_eur - orig_line.unit_price_eur, 2)

        tier = _compute_substitute_tier(orig, cand, cand_line, explicit_sub_set)
        tradeoffs = _build_tradeoffs(orig, cand, orig_line, cand_line, price_delta_eur)
        reason_parts = [
            "Catalog substitute" if cand.id in explicit_sub_set else "Same-category match"
        ]
        if required_diets:
            reason_parts.append(f"verified {', '.join(required_diets)}")
        fill_note = "full fill" if "PARTIAL" not in cand_line.flags else "partial fill"
        reason_parts.append(f"{cand_line.delivery_status} ({fill_note})")

        brand_pref_by_id[cand.id] = evaluate_brand_preference_score(
            brand=cand.brand,
            category=cand.category,
            preferences=active_brand_prefs,
        )
        aimo_campaign_by_id[cand.id] = evaluate_aimo_campaign_score(
            product_id=cand.id,
            brand=cand.brand,
            category=cand.category,
            campaigns=active_aimo_campaigns,
        )

        items.append(
            SubstituteCandidate(
                product=product_to_out(cand),
                quote_line=cand_line,
                tradeoffs=tradeoffs,
                reason="; ".join(reason_parts),
                price_delta_eur=price_delta_eur,
                tier=tier,
            )
        )

    items.sort(
        key=lambda item: (
            0 if item.quote_line.delivery_status == "ON_TIME" else 1,
            0 if "PARTIAL" not in item.quote_line.flags else 1,
            _TIER_RANK.get(item.tier or "GOOD", 2),
            -brand_pref_by_id.get(item.product.id, 0.0),
            -aimo_campaign_by_id.get(item.product.id, 0.0),
            item.price_delta_eur if item.price_delta_eur is not None else float("inf"),
            0 if (item.product.country_of_origin or "").strip().upper() == "FI" else 1,
            0 if "valio" in (item.product.brand or "").lower() else 1,
            -(item.quote_line.on_time_probability or 0.0),
            item.product.id,
        )
    )

    return SubstitutesResponse(
        requested_product_id=product_id,
        diet=diet,
        items=items,
    )
