"""Catalog search, product mapping, and deterministic Valio diet/allergen filtering."""

from __future__ import annotations

from collections.abc import Sequence
from functools import lru_cache
from typing import Any

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.domain import valio_rules
from app.errors import AppError
from app.models import Category, Product
from app.schemas import CategoryOut, ProductOut, ProductUnitOut

ALLERGEN_FREE_TO_DIET: dict[str, str] = {
    "lactose": "LACTOSE_FREE",
    "low_lactose": "LOW_LACTOSE",
    "milk": "MILK_FREE",
    "gluten": "GLUTEN_FREE_COELIAC",
    "egg": "EGG_FREE",
    "eggs": "EGG_FREE",
    "fish": "FISH_FREE",
    "soy": "SOY_FREE",
    "soya": "SOY_FREE",
    "peanut": "PEANUT_FREE",
    "peanuts": "PEANUT_FREE",
    "nut": "TREE_NUT_FREE",
    "nuts": "TREE_NUT_FREE",
    "tree_nut": "TREE_NUT_FREE",
    "tree_nuts": "TREE_NUT_FREE",
    "sesame": "SESAME_FREE",
    "celery": "CELERY_FREE",
    "mustard": "MUSTARD_FREE",
    "lupin": "LUPIN_FREE",
    "sulphite": "SULPHITE_FREE",
    "sulphites": "SULPHITE_FREE",
    "sulfite": "SULPHITE_FREE",
    "sulfites": "SULPHITE_FREE",
    "shellfish": "SHELLFISH_FREE",
    "crustaceans": "SHELLFISH_FREE",
    "molluscs": "SHELLFISH_FREE",
    "wheat": "WHEAT_ALLERGY",
    "rye": "RYE_ALLERGY",
    "barley": "BARLEY_ALLERGY",
    "oat": "OAT_ALLERGY",
    "oats": "OAT_ALLERGY",
}

_DIET_ALIASES: dict[str, str] = {
    "HEART": "HEART_LOW_SALT",
    "HEART_SYMBOL": "HEART_LOW_SALT",
}


@lru_cache(maxsize=1)
def _category_mapping() -> dict[str, dict[str, Any]]:
    return valio_rules.load_category_mapping()


def is_food_category(category_code: str | None) -> bool:
    """Return whether a category code is classified as food (default True if unknown)."""
    if not category_code:
        return True
    entry = _category_mapping().get(str(category_code))
    if entry is None:
        return True
    return bool(entry.get("food", True))


def product_to_out(p: Product) -> ProductOut:
    """Map an ORM Product row to the API contract ProductOut schema."""
    units = [
        ProductUnitOut(
            unit_id=str(u["unit_id"]),
            size_in_base_units=float(u["size_in_base_units"]),
        )
        for u in (p.units or [])
        if isinstance(u, dict)
        and u.get("unit_id") is not None
        and u.get("size_in_base_units") is not None
    ]
    names = {
        k: str(v)
        for k, v in (p.names or {}).items()
        if k in ("fi", "en", "sv") and isinstance(v, str) and v
    }
    return ProductOut(
        id=p.id,
        name=p.name,
        names=names,
        category=p.category,
        category_name=p.category_name,
        sales_unit=p.sales_unit,
        base_unit=p.base_unit,
        units=units,
        vendor=p.vendor,
        brand=p.brand,
        country_of_origin=p.country_of_origin,
        temperature_condition=p.temperature_condition,
        allergens=list(p.allergens or []),
        non_allergens=list(p.non_allergens or []),
        labels=list(p.labels or []),
        image_url=p.image_url,
        co2_kg_per_kg=p.co2_kg_per_kg,
        delivery_reliability=p.delivery_reliability,
    )


def resolve_diet_keys(
    diet_param: str | None,
    allergen_free_param: str | None,
) -> list[str]:
    """Resolve `diet` codes/keys and `allergen_free` tokens into valio_rules.DIETS keys."""
    resolved: list[str] = []

    if diet_param:
        for raw_tok in diet_param.split(","):
            tok = raw_tok.strip()
            if not tok:
                continue
            upper = tok.upper()
            if tok in valio_rules.MENU_CODES:
                resolved.append(valio_rules.MENU_CODES[tok])
            elif upper in valio_rules.MENU_CODES:
                resolved.append(valio_rules.MENU_CODES[upper])
            elif upper in _DIET_ALIASES:
                resolved.append(_DIET_ALIASES[upper])
            elif upper in valio_rules.DIETS:
                resolved.append(upper)
            else:
                raise AppError(
                    "VALIDATION_ERROR",
                    422,
                    f"Unknown diet code: {tok}",
                    details={"diet": tok},
                )

    if allergen_free_param:
        for raw_tok in allergen_free_param.split(","):
            tok = raw_tok.strip()
            if not tok:
                continue
            lower = tok.lower()
            upper = tok.upper()
            if lower in ALLERGEN_FREE_TO_DIET:
                resolved.append(ALLERGEN_FREE_TO_DIET[lower])
            elif upper in valio_rules.DIETS:
                resolved.append(upper)
            else:
                raise AppError(
                    "VALIDATION_ERROR",
                    422,
                    f"Unknown allergen_free filter: {tok}",
                    details={"allergen_free": tok},
                )

    return list(dict.fromkeys(resolved))


def _reconstruct_synkka_product(
    allergen_states: dict[str, str] | None,
    claims: Sequence[str] | None,
    labels: Sequence[str] | None,
    nutrients: dict[str, list[Any]] | None,
) -> dict[str, Any]:
    """Build a minimal Synkka-compatible dict from stored Product columns for valio_rules."""
    allergens: list[dict[str, str]] = []
    non_allergens: list[dict[str, str]] = []
    for code, state in (allergen_states or {}).items():
        if state == "PRESENT":
            allergens.append({"id": code, "unit": "CONTAINS"})
        elif state == "TRACE":
            allergens.append({"id": code, "unit": "MAY_CONTAIN"})
        elif state == "ABSENT":
            non_allergens.append({"id": code, "unit": "FREE_FROM"})
        elif state == "ABSENT_WEAK":
            non_allergens.append({"id": code, "unit": "UNDECLARED"})

    claims_list = list(claims or [])
    claims_set = set(claims_list)
    packaging_labels = [lb for lb in (labels or []) if lb not in claims_set]

    nutritional_content: list[dict[str, Any]] = []
    for nid, pair in (nutrients or {}).items():
        if not isinstance(pair, (list, tuple)) or not pair:
            continue
        val = float(pair[0])
        prec = str(pair[1]) if len(pair) > 1 and pair[1] else "APPROXIMATELY"
        nutritional_content.append(
            {
                "id": nid,
                "unit": "GRM",
                "value": val,
                "basisQuantity": 100.0,
                "basisQuantityUnit": "GRM",
                "unitPrecision": prec,
            }
        )

    return {
        "synkkaData": {
            "classifications": [
                {"name": "allergen", "values": allergens},
                {"name": "nonAllergen", "values": non_allergens},
                {"name": "nutritionalClaim", "values": [{"id": c} for c in claims_list]},
                {
                    "name": "packagingMarkedLabel",
                    "values": [{"id": lb} for lb in packaging_labels],
                },
            ],
            "nutritionalContent": nutritional_content,
        }
    }


def _can_possibly_pass_diet(
    diet_key: str,
    allergen_states: dict[str, str],
    claims_set: set[str],
    labels_set: set[str],
    nutrients: dict[str, Any],
) -> tuple[bool, list[str]]:
    """Fast sound pre-check before running ingredient regexes.

    Returns (True, []) if the product could potentially PASS valio_rules.evaluate,
    or (False, reasons) if it is guaranteed to return FAIL or REVIEW.
    """
    rule = valio_rules.DIETS[diet_key]
    if "block" in rule:
        for code, _ in rule["block"]:
            st = allergen_states.get(code, "UNKNOWN")
            if st != "ABSENT":
                return False, [f"{code}={st}"]
        return True, []

    sp = rule.get("special")
    if sp == "coeliac":
        if "FREE_FROM_GLUTEN" in claims_set:
            return True, []
        sub = [
            allergen_states.get(c, "UNKNOWN") for c in ("AW", "UW", "NR", "GB", "GO", "GS", "GK")
        ]
        if "PRESENT" in sub or "TRACE" in sub:
            return False, ["gluten cereal present or trace"]
        if allergen_states.get("AW", "UNKNOWN") != "ABSENT":
            return False, ["no gluten claim and AW not FREE_FROM"]
        return True, []

    if sp in ("lactose_free", "low_lactose"):
        if allergen_states.get("AM", "UNKNOWN") == "ABSENT":
            return True, []
        ok_claims = {"FREE_FROM_LACTOSE"} | ({"LOW_LACTOSE"} if sp == "low_lactose" else set())
        if "LACS" in nutrients or (ok_claims & claims_set):
            return True, []
        if allergen_states.get("AM", "UNKNOWN") in ("PRESENT", "UNKNOWN") or (
            allergen_states.get("ML", "UNKNOWN") == "PRESENT"
        ):
            return False, ["milk present/unknown and no lactose claim or value"]
        return True, []

    if sp == "vegan":
        if any(
            allergen_states.get(c, "UNKNOWN") == "PRESENT" for c in valio_rules.ANIMAL_ALLERGENS
        ):
            return False, ["animal allergen PRESENT"]
        if "VEGAN" not in claims_set:
            return False, ["no VEGAN claim; needs ingredient text"]
        return True, []

    if sp == "vegetarian":
        if any(
            allergen_states.get(c, "UNKNOWN") == "PRESENT" for c in valio_rules.MEAT_FISH_ALLERGENS
        ):
            return False, ["meat/fish PRESENT"]
        if not (claims_set & {"VEGAN", "VEGETARIAN"}):
            return False, ["no VEGAN/VEGETARIAN claim"]
        return True, []

    if sp in ("pork_free", "beef_free"):
        code = "PO" if sp == "pork_free" else "BF"
        if allergen_states.get(code, "UNKNOWN") == "PRESENT":
            return False, [f"{code}=PRESENT"]
        if not (claims_set & {"VEGAN", "VEGETARIAN"}):
            return False, ["needs ingredient text"]
        return True, []

    if sp in ("certification_required", "curated_texture"):
        return False, ["certification/curated texture required"]

    if sp == "sydanmerkki":
        if "FINNISH_HEART_SYMBOL" not in labels_set:
            return False, ["no FINNISH_HEART_SYMBOL label"]
        return True, []

    if sp == "not_worse_energy_protein":
        if not ("PRO-" in nutrients and "ENER-KJO" in nutrients):
            return False, ["missing PRO-/ENER-KJO nutrients"]
        return True, []

    return True, []


@lru_cache(maxsize=8192)
def _cached_parse_ingredients(key_ingredients_text: str) -> dict[str, str]:
    text_st, _ = valio_rules.parse_ingredients(key_ingredients_text)
    return text_st


def _evaluate_fields_against_diets(
    *,
    category: str | None,
    allergen_states: dict[str, str] | None,
    claims: Sequence[str] | None,
    labels: Sequence[str] | None,
    nutrients: dict[str, list[Any]] | None,
    key_ingredients_text: str | None,
    diet_keys: list[str],
) -> tuple[bool, list[str]]:
    if not diet_keys:
        return True, []

    cat_food = is_food_category(category)
    if not cat_food:
        return False, ["non-food category: diets not applicable"]

    a_states = allergen_states or {}
    claims_set = set(claims or [])
    labels_set = set(labels or [])
    nutr = nutrients or {}

    for diet_key in diet_keys:
        possible, pre_reasons = _can_possibly_pass_diet(
            diet_key, a_states, claims_set, labels_set, nutr
        )
        if not possible:
            return False, pre_reasons

    synkka = _reconstruct_synkka_product(a_states, claims, labels, nutr)
    text_st = _cached_parse_ingredients(key_ingredients_text) if key_ingredients_text else {}

    all_reasons: list[str] = []
    for diet_key in diet_keys:
        verdict, reasons = valio_rules.evaluate(
            synkka,
            diet_key,
            text_states=text_st,
            category_food=cat_food,
        )
        if verdict != "PASS":
            return False, reasons
        all_reasons.extend(reasons)
    return True, all_reasons


def product_passes_diets(p: Product, diet_keys: list[str]) -> tuple[bool, list[str]]:
    """Evaluate a Product against strict valio_rules diets; only PASS is accepted."""
    return _evaluate_fields_against_diets(
        category=p.category,
        allergen_states=p.allergen_states,
        claims=p.claims,
        labels=p.labels,
        nutrients=p.nutrients,
        key_ingredients_text=p.key_ingredients_text,
        diet_keys=diet_keys,
    )


def _escape_like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _rank_key(
    *,
    product_id: str,
    category: str,
    name: str,
    name_fi: str | None,
    name_en: str | None,
    name_sv: str | None,
    delivery_reliability: float | None,
    co2_kg_per_kg: float | None,
    q_clean: str,
    tokens: list[str],
    promo_score: float = 0.0,
    brand_pref_score: float = 0.0,
    aimo_campaign_score: float = 0.0,
) -> tuple[Any, ...]:
    names = [nm.lower() for nm in (name, name_fi, name_en, name_sv) if nm]
    name_blob = " ".join(names)
    name_words = set(name_blob.split())

    exact_id = 0 if (q_clean and product_id.lower() == q_clean) else 1
    exact_prefix = 0 if (q_clean and any(nm.startswith(q_clean) for nm in names)) else 1
    food_tier = 0 if is_food_category(category) else 1
    all_in_names = 0 if (tokens and all(tok in name_blob for tok in tokens)) else 1
    name_token_hits = -sum(1 for tok in tokens if tok in name_blob)
    word_hits = -sum(1 for tok in tokens if tok in name_words)

    if delivery_reliability is not None and co2_kg_per_kg is not None:
        data_tier = 0
    elif delivery_reliability is not None or co2_kg_per_kg is not None:
        data_tier = 1
    else:
        data_tier = 2

    rel_score = -(float(delivery_reliability) if delivery_reliability is not None else 0.0)
    return (
        exact_id,
        exact_prefix,
        food_tier,
        all_in_names,
        name_token_hits,
        word_hits,
        data_tier,
        -float(promo_score + brand_pref_score),
        -float(aimo_campaign_score),
        rel_score,
        product_id,
    )


def search_products_with_promotions(
    db: Session,
    *,
    q: str | None = None,
    category: str | None = None,
    allergen_free: str | None = None,
    label: str | None = None,
    diet: str | None = None,
    limit: int = 20,
    offset: int = 0,
    company_id: str | None = None,
) -> tuple[list[ProductOut], int, dict[str, tuple[bool, list[str]]]]:
    """Search, diet-filter, subtly promotion-rank, and paginate catalog products."""
    from app.services.admin_ranking import (
        evaluate_aimo_campaign_score,
        evaluate_brand_preference_score,
        get_active_aimo_campaigns,
        get_active_brand_preferences,
    )
    from app.services.promotions import evaluate_product_promotions, get_active_promotions

    diet_keys = resolve_diet_keys(diet, allergen_free)
    label_tokens = [tok.strip().lower() for tok in (label or "").split(",") if tok.strip()]

    q_clean = (q or "").strip().lower()
    tokens = [tok for tok in q_clean.split() if tok]

    filters = []
    if category and category.strip():
        cat_prefix = _escape_like(category.strip())
        filters.append(Product.category.like(f"{cat_prefix}%", escape="\\"))

    for tok in tokens:
        pattern = f"%{_escape_like(tok)}%"
        filters.append(
            or_(
                func.lower(Product.search_text).like(pattern, escape="\\"),
                and_(
                    Product.search_text == "",
                    or_(
                        func.lower(Product.id).like(pattern, escape="\\"),
                        func.lower(Product.name).like(pattern, escape="\\"),
                        func.lower(func.coalesce(Product.name_fi, "")).like(pattern, escape="\\"),
                        func.lower(func.coalesce(Product.name_en, "")).like(pattern, escape="\\"),
                        func.lower(func.coalesce(Product.name_sv, "")).like(pattern, escape="\\"),
                        func.lower(func.coalesce(Product.brand, "")).like(pattern, escape="\\"),
                        func.lower(func.coalesce(Product.vendor, "")).like(pattern, escape="\\"),
                    ),
                ),
            )
        )

    stmt = select(
        Product.id,
        Product.category,
        Product.brand,
        Product.name,
        Product.name_fi,
        Product.name_en,
        Product.name_sv,
        Product.delivery_reliability,
        Product.co2_kg_per_kg,
        Product.allergen_states,
        Product.claims,
        Product.labels,
        Product.nutrients,
        Product.key_ingredients_text,
    )
    if filters:
        stmt = stmt.where(*filters)

    candidates = db.execute(stmt).all()
    active_promos = get_active_promotions(db, company_id) if company_id else []
    active_brand_prefs = get_active_brand_preferences(db, company_id) if company_id else []
    active_aimo_campaigns = get_active_aimo_campaigns(db)

    matched: list[tuple[Any, bool, list[str], float, float, float]] = []
    for row in candidates:
        if label_tokens:
            row_labels = {str(lb).lower() for lb in (row.labels or [])}
            if not all(lt in row_labels for lt in label_tokens):
                continue
        if diet_keys:
            passed, _ = _evaluate_fields_against_diets(
                category=row.category,
                allergen_states=row.allergen_states,
                claims=row.claims,
                labels=row.labels,
                nutrients=row.nutrients,
                key_ingredients_text=row.key_ingredients_text,
                diet_keys=diet_keys,
            )
            if not passed:
                continue
        promoted, promo_reasons, promo_score = (
            evaluate_product_promotions(
                product_id=row.id,
                brand=row.brand,
                category=row.category,
                rules=active_promos,
                on_sale=False,
            )
            if active_promos
            else (False, [], 0.0)
        )
        brand_pref_score = (
            evaluate_brand_preference_score(
                brand=row.brand,
                category=row.category,
                preferences=active_brand_prefs,
            )
            if active_brand_prefs
            else 0.0
        )
        aimo_campaign_score = (
            evaluate_aimo_campaign_score(
                product_id=row.id,
                brand=row.brand,
                category=row.category,
                campaigns=active_aimo_campaigns,
            )
            if active_aimo_campaigns
            else 0.0
        )
        matched.append(
            (row, promoted, promo_reasons, promo_score, brand_pref_score, aimo_campaign_score)
        )

    matched.sort(
        key=lambda entry: _rank_key(
            product_id=entry[0].id,
            category=entry[0].category,
            name=entry[0].name,
            name_fi=entry[0].name_fi,
            name_en=entry[0].name_en,
            name_sv=entry[0].name_sv,
            delivery_reliability=entry[0].delivery_reliability,
            co2_kg_per_kg=entry[0].co2_kg_per_kg,
            q_clean=q_clean,
            tokens=tokens,
            promo_score=entry[3],
            brand_pref_score=entry[4],
            aimo_campaign_score=entry[5],
        )
    )

    total = len(matched)
    page_entries = matched[offset : offset + limit]
    page_ids = [entry[0].id for entry in page_entries]
    if not page_ids:
        return [], total, {}

    products = db.scalars(select(Product).where(Product.id.in_(page_ids))).all()
    by_id = {p.id: p for p in products}
    items = [product_to_out(by_id[pid]) for pid in page_ids if pid in by_id]
    promo_map = {entry[0].id: (entry[1], entry[2]) for entry in page_entries}
    return items, total, promo_map


def search_products(
    db: Session,
    *,
    q: str | None = None,
    category: str | None = None,
    allergen_free: str | None = None,
    label: str | None = None,
    diet: str | None = None,
    limit: int = 20,
    offset: int = 0,
    company_id: str | None = None,
) -> tuple[list[ProductOut], int]:
    """Search, diet-filter, rank, and paginate catalog products."""
    items, total, _ = search_products_with_promotions(
        db,
        q=q,
        category=category,
        allergen_free=allergen_free,
        label=label,
        diet=diet,
        limit=limit,
        offset=offset,
        company_id=company_id,
    )
    return items, total


def get_product(db: Session, product_id: str) -> ProductOut:
    """Return a single ProductOut by ID or raise 404 NOT_FOUND."""
    product = db.get(Product, product_id)
    if product is None:
        raise AppError(
            "NOT_FOUND",
            404,
            f"Product {product_id} not found",
            details={"product_id": product_id},
        )
    return product_to_out(product)


def list_categories(db: Session) -> list[CategoryOut]:
    """Return non-empty categories ordered by product_count desc then code asc."""
    rows = db.scalars(
        select(Category)
        .where(Category.product_count > 0)
        .order_by(Category.product_count.desc(), Category.code.asc())
    ).all()
    return [
        CategoryOut(
            code=cat.code,
            name=cat.name_en or cat.name_fi or cat.code,
            product_count=cat.product_count,
        )
        for cat in rows
    ]
