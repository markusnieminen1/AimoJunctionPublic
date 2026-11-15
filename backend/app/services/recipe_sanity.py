"""Deterministic recipe-yield over-ordering sanity checker (R-RECIPE-SANITY)."""

from __future__ import annotations

import math
import re
from collections.abc import Sequence

from app.domain.valio_recipes import VALIO_RECIPES, RecipeIngredientSpec, ValioRecipe
from app.models import Product
from app.schemas import QuoteLine, RecipeSanityOffendingLine, RecipeSanityWarning
from app.services.co2 import estimate_kg_per_sales_unit

EXCESS_RATIO_THRESHOLD = 1.5


def resolve_recipe(
    recipe_id: str | None,
    product_ids: Sequence[str] = (),
) -> ValioRecipe | None:
    """Resolve a ValioRecipe by exact ID, rcp_*_01 slug, name substring, or SKU overlap."""
    if not recipe_id or not recipe_id.strip():
        return None
    raw = recipe_id.strip().lower()
    normalized = re.sub(r"^rcp_", "", raw)
    normalized = re.sub(r"_\d+$", "", normalized)

    for recipe in VALIO_RECIPES:
        rid = recipe.id.lower()
        if raw == rid or normalized == rid:
            return recipe

    for recipe in VALIO_RECIPES:
        rid = recipe.id.lower()
        rname = recipe.name.lower()
        if normalized in rid or rid in normalized or normalized in rname:
            return recipe

    # Fallback: pick recipe with highest preferred SKU overlap when a custom recipe_id is passed
    pid_set = set(product_ids)
    best_recipe: ValioRecipe | None = None
    best_overlap = 0
    for recipe in VALIO_RECIPES:
        pref_ids = {pid for spec in recipe.ingredients for pid in spec.preferred_product_ids}
        overlap = len(pid_set & pref_ids)
        if overlap > best_overlap:
            best_overlap = overlap
            best_recipe = recipe
    return best_recipe or VALIO_RECIPES[0]


def _match_ingredient_spec(
    recipe: ValioRecipe,
    product: Product | None,
    product_id: str,
) -> RecipeIngredientSpec | None:
    for spec in recipe.ingredients:
        if product_id in spec.preferred_product_ids:
            return spec

    if product is not None:
        haystack = (
            f"{product.name} {product.name_fi or ''} {product.name_en or ''} {product.brand or ''}"
        ).lower()
        for spec in recipe.ingredients:
            if any(kw.lower() in haystack for kw in spec.search_keywords if kw):
                return spec
        for spec in recipe.ingredients:
            if product.category and any(
                product.category.startswith(pref) for pref in spec.category_prefixes
            ):
                return spec
    return None


def _expected_sales_units(
    spec: RecipeIngredientSpec | None,
    product: Product | None,
    target_pax: int,
) -> float:
    qty_per_10 = spec.qty_per_10_pax if spec is not None else 2.0
    raw_requirement = (float(target_pax) / 10.0) * float(qty_per_10)

    # Adjust for bulk cases/Novoboxes when spec hint is KG or L and pack size > 1.5 kg/L
    if product is not None and spec is not None:
        hint_upper = spec.unit_hint.upper()
        if hint_upper.startswith(("KG", "L")):
            kg_per_unit, _, _ = estimate_kg_per_sales_unit(product)
            if kg_per_unit is not None and kg_per_unit > 1.5:
                raw_requirement = raw_requirement / kg_per_unit

    expected = max(1, math.ceil(raw_requirement))
    if product is not None and product.allowed_lot_size and product.allowed_lot_size > 1:
        lot = max(1, int(round(float(product.allowed_lot_size))))
        rem = expected % lot
        if rem != 0:
            expected += lot - rem
    return float(expected)


def evaluate_recipe_sanity(
    product_map: dict[str, Product],
    lines: Sequence[QuoteLine],
    recipe_id: str | None,
    target_pax: int | None,
) -> RecipeSanityWarning | None:
    """Return RecipeSanityWarning if any line exceeds 1.5x expected recipe yield for target_pax."""
    if not recipe_id or not recipe_id.strip() or target_pax is None or target_pax <= 0:
        return None

    recipe = resolve_recipe(recipe_id, [ln.product_id for ln in lines])
    if recipe is None:
        return None

    offending: list[RecipeSanityOffendingLine] = []
    for line in lines:
        prod = product_map.get(line.product_id)
        spec = _match_ingredient_spec(recipe, prod, line.product_id)
        expected_qty = _expected_sales_units(spec, prod, target_pax)
        ordered_qty = float(line.quantity)
        if expected_qty <= 0:
            continue
        ratio = round(ordered_qty / expected_qty, 2)
        if ordered_qty > EXCESS_RATIO_THRESHOLD * expected_qty + 1e-6:
            unit_price = float(line.unit_price_eur or 0.0)
            excess_units = max(0.0, ordered_qty - expected_qty)
            excess_cost = round(excess_units * unit_price, 2)
            offending.append(
                RecipeSanityOffendingLine(
                    product_id=line.product_id,
                    product_name=line.product_name,
                    ordered_qty=ordered_qty,
                    unit=line.sales_unit,
                    expected_qty=expected_qty,
                    excess_ratio=ratio,
                    excess_cost_eur=excess_cost,
                )
            )

    if not offending:
        return None

    top = offending[0]
    total_excess_eur = round(sum(item.excess_cost_eur for item in offending), 2)
    message = (
        f"Recipe yield sanity warning for '{recipe.name}' ({target_pax} pax): "
        f"{top.product_name} ({top.product_id}) ordered {top.ordered_qty:g} {top.unit} vs "
        f"expected {top.expected_qty:g} {top.unit} ({top.excess_ratio:.1f}x yield, "
        f"+EUR {total_excess_eur:.2f} excess across {len(offending)} line(s))."
    )
    return RecipeSanityWarning(
        triggered=True,
        rule_id="R-RECIPE-SANITY",
        recipe_id=recipe.id,
        recipe_name=recipe.name,
        target_pax=target_pax,
        message=message,
        offending_lines=offending,
    )
