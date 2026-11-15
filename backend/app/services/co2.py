"""Line-level CO2 footprint estimation for Valio Aimo products."""

from __future__ import annotations

import re
from typing import Any

from app.models import Product

_WEIGHT_UNITS_TO_KG: dict[str, float] = {
    "KGM": 1.0,
    "GRM": 0.001,
}

# Match explicit pack weights/volumes like "1 l", "400 g", "2,5 kg", "250 ml", "6 x 1,5 l".
_NAME_SIZE_RE = re.compile(
    r"(?:(?P<mult>\d+)\s*[x×]\s*)?(?P<val>\d+(?:[.,]\d+)?)\s*(?P<unit>kg|g|ml|l)\b",
    re.IGNORECASE,
)


def _find_unit_size(product: Product, unit_id: str) -> float | None:
    for entry in product.units or []:
        if isinstance(entry, dict) and entry.get("unit_id") == unit_id:
            size = entry.get("size_in_base_units")
            if size is not None and float(size) > 0:
                return float(size)
    return None


def _net_weight_kg_from_conversions(product: Product) -> tuple[float | None, str | None]:
    conversions = [u for u in (product.unit_conversions or []) if isinstance(u, dict)]
    if not conversions:
        return None, None

    sales_unit = product.sales_unit
    if sales_unit in ("KI", "CS", "KRT", "KLE"):
        preferred_types = ("CASE", "BASE_UNIT_OR_EACH")
    elif sales_unit == "PAL":
        preferred_types = ("PALLET", "CASE", "BASE_UNIT_OR_EACH")
    else:
        preferred_types = ("BASE_UNIT_OR_EACH", "CASE")

    by_type: dict[str, dict[str, Any]] = {}
    for conv in conversions:
        ptype = conv.get("synkkaPackagingType")
        if isinstance(ptype, str):
            by_type[ptype] = conv

    for ptype in preferred_types:
        conv = by_type.get(ptype)
        if conv is None:
            continue
        net_w = conv.get("netWeight")
        if not isinstance(net_w, dict):
            continue
        unit_code = net_w.get("unit")
        val = net_w.get("value")
        if unit_code in _WEIGHT_UNITS_TO_KG and val is not None and float(val) > 0:
            kg_val = float(val) * _WEIGHT_UNITS_TO_KG[unit_code]
            if ptype == "BASE_UNIT_OR_EACH" and sales_unit != product.base_unit:
                mult = _find_unit_size(product, sales_unit)
                if mult is not None:
                    kg_val *= mult
                elif sales_unit in ("KI", "CS", "KRT", "KLE", "PAL"):
                    continue
            return kg_val, f"{kg_val:g} kg/{sales_unit} (Synkka netWeight)"

    return None, None


def _kg_from_product_name(product: Product) -> tuple[float | None, str | None]:
    candidates = [
        product.name,
        product.name_fi,
        product.name_en,
        product.name_sv,
    ]
    for text in candidates:
        if not text:
            continue
        match = _NAME_SIZE_RE.search(text)
        if not match:
            continue
        raw_val = float(match.group("val").replace(",", "."))
        if raw_val <= 0:
            continue
        mult = int(match.group("mult")) if match.group("mult") else 1
        unit = match.group("unit").lower()

        if unit == "kg":
            kg_val = mult * raw_val
            assumed = False
        elif unit == "g":
            kg_val = mult * (raw_val / 1000.0)
            assumed = False
        elif unit == "l":
            kg_val = mult * raw_val
            assumed = True
        else:  # ml
            kg_val = mult * (raw_val / 1000.0)
            assumed = True

        if not match.group("mult") and product.sales_unit != product.base_unit:
            size = _find_unit_size(product, product.sales_unit)
            if size is not None:
                kg_val *= size

        note = (
            "name density 1.0 kg/L (assumed)" if assumed else "parsed from product name (assumed)"
        )
        return kg_val, f"{kg_val:g} kg/{product.sales_unit} ({note})"

    return None, None


def estimate_kg_per_sales_unit(
    product: Product,
    price_item: dict[str, Any] | None = None,
) -> tuple[float | None, str | None, bool]:
    """Return (kg_per_sales_unit, basis_part, estimated) for a product's sales unit."""
    kg_per_sales_unit: float | None = None
    basis_part: str | None = None
    estimated = False

    # (1) Exact weight conversion from units[] when base_unit is KG
    if product.base_unit == "KG":
        unit_size = _find_unit_size(product, product.sales_unit)
        if unit_size is not None:
            kg_per_sales_unit = unit_size
            basis_part = f"{kg_per_sales_unit:g} kg/{product.sales_unit}"
        elif product.sales_unit == "KG":
            kg_per_sales_unit = 1.0
            basis_part = "1 kg/KG"

    # (2) Comparison price ratio when comparisonUnit is KG or L
    if kg_per_sales_unit is None and price_item and not price_item.get("error"):
        comp_unit = price_item.get("comparisonUnit")
        cat_val = (price_item.get("catalogPrice") or {}).get("value")
        comp_val = (price_item.get("comparisonPrice") or {}).get("value")
        if (
            comp_unit in ("KG", "L")
            and cat_val is not None
            and comp_val is not None
            and float(cat_val) > 0
            and float(comp_val) > 0
        ):
            ratio = float(cat_val) / float(comp_val)
            if comp_unit == "KG":
                kg_per_sales_unit = ratio
                basis_part = f"{ratio:.4g} kg/{product.sales_unit} (from comparisonPrice)"
                estimated = False
            else:
                kg_per_sales_unit = ratio * 1.0
                basis_part = f"{ratio:.4g} L/{product.sales_unit} × 1.0 kg/L (assumed)"
                estimated = True

    # (3) Synkka unitConversions netWeight (KGM/GRM only, never volume/grossWeight)
    if kg_per_sales_unit is None:
        conv_kg, conv_basis = _net_weight_kg_from_conversions(product)
        if conv_kg is not None:
            kg_per_sales_unit = conv_kg
            basis_part = conv_basis
            estimated = False

    # (4) Fallback: parse explicit kg/g/l/ml from product name
    if kg_per_sales_unit is None:
        name_kg, name_basis = _kg_from_product_name(product)
        if name_kg is not None:
            kg_per_sales_unit = name_kg
            basis_part = name_basis
            estimated = True

    return kg_per_sales_unit, basis_part, estimated


def compute_line_co2(
    product: Product,
    price_item: dict[str, Any] | None,
    quantity: float,
) -> tuple[float | None, str | None, bool]:
    """Compute (co2_kg, co2_basis, estimated) for a product line."""
    if product.co2_kg_per_kg is None:
        return None, None, False

    co2_per_kg = float(product.co2_kg_per_kg)
    qty = float(quantity)

    kg_per_sales_unit, basis_part, estimated = estimate_kg_per_sales_unit(product, price_item)

    # (5) Unable to determine weight per sales unit
    if kg_per_sales_unit is None or basis_part is None:
        return None, None, False

    co2_kg = round(co2_per_kg * kg_per_sales_unit * qty, 3)
    co2_basis = f"{co2_per_kg:g} kg CO2/kg × {basis_part} × {qty:g} {product.sales_unit}"
    return co2_kg, co2_basis, estimated
