"""Stream-load and upsert Valio Aimo product catalog, categories, CO2, and reliability."""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

from sqlalchemy import insert, select, update
from sqlalchemy.orm import Session

# Allow running directly as `uv run python scripts/seed_catalog.py`
_BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from app.db import SessionLocal, init_db  # noqa: E402
from app.domain import valio_rules  # noqa: E402
from app.models import Category, Product  # noqa: E402

_REPO_ROOT = _BACKEND_DIR.parent
DEFAULT_PRODUCTS_PATH = _REPO_ROOT / "catalog_data" / "products.json"
DEFAULT_CO2_PATH = _REPO_ROOT / "catalog_data" / "carbon_footprint.csv"
DEFAULT_RELIABILITY_PATH = _REPO_ROOT / "catalog_data" / "delivery_reliability.csv"
DEFAULT_CATEGORIES_PATH = _BACKEND_DIR / "app" / "domain" / "category_mapping.json"


def _load_float_csv(
    path: Path | None,
    value_cols: tuple[str, ...],
    *,
    normalize_percentage: bool = False,
) -> dict[str, float]:
    out: dict[str, float] = {}
    if path is None or not path.is_file():
        return out
    with open(path, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            pid = (row.get("productId") or "").strip()
            raw_val = ""
            used_pct_col = False
            for col in value_cols:
                candidate = (row.get(col) or "").strip()
                if candidate:
                    raw_val = candidate
                    used_pct_col = col == "reliabilityPct"
                    break
            if not pid or not raw_val:
                continue
            try:
                parsed = float(raw_val)
            except ValueError:
                continue
            if normalize_percentage and (used_pct_col and parsed > 1.0 or parsed > 1.0):
                parsed = round(parsed / 100.0, 6)
            out[pid] = parsed
    return out


def _extract_image_url(synkka_data: dict[str, Any]) -> str | None:
    for media in synkka_data.get("medias") or []:
        if isinstance(media, str) and media.startswith(("http://", "https://")):
            return media
        if isinstance(media, dict):
            for key in ("url", "uri", "href", "imageUrl"):
                val = media.get(key)
                if isinstance(val, str) and val.startswith(("http://", "https://")):
                    return val
    return None


def _extract_key_ingredients(synkka_data: dict[str, Any]) -> str | None:
    ki = synkka_data.get("keyIngredients") or []
    by_lang = {
        entry.get("language"): entry.get("value").strip()
        for entry in ki
        if isinstance(entry, dict)
        and isinstance(entry.get("value"), str)
        and entry.get("value").strip()
    }
    for lang in ("fi", "en", "sv"):
        if lang in by_lang:
            return by_lang[lang]
    return next(iter(by_lang.values()), None)


def _extract_product_row(
    raw: dict[str, Any],
    category_map: dict[str, dict[str, Any]],
    co2_map: dict[str, float],
    reliability_map: dict[str, float],
) -> dict[str, Any]:
    product_id = str(raw["id"])
    sd = raw.get("synkkaData") or {}

    names_by_lang: dict[str, str] = {}
    for entry in sd.get("names") or []:
        if not isinstance(entry, dict):
            continue
        lang = entry.get("language")
        val = entry.get("value")
        if lang in ("fi", "en", "sv") and isinstance(val, str) and val.strip():
            names_by_lang[lang] = val.strip()

    name_fi = names_by_lang.get("fi")
    name_en = names_by_lang.get("en")
    name_sv = names_by_lang.get("sv")
    name = name_fi or name_en or name_sv or product_id

    category = str(raw.get("category") or "")
    cat_info = category_map.get(category) or {}
    category_name = cat_info.get("name_en") or cat_info.get("name_fi")

    units = [
        {"unit_id": str(u["unitId"]), "size_in_base_units": float(u["sizeInBaseUnits"])}
        for u in (raw.get("units") or [])
        if isinstance(u, dict) and u.get("unitId") and u.get("sizeInBaseUnits") is not None
    ]

    groups = {
        g.get("name"): g.get("values") or []
        for g in (sd.get("classifications") or [])
        if isinstance(g, dict)
    }
    allergens = list(
        dict.fromkeys(
            str(v["id"])
            for v in groups.get("allergen", [])
            if isinstance(v, dict) and v.get("id") and v.get("unit") in ("CONTAINS", "MAY_CONTAIN")
        )
    )
    non_allergens = list(
        dict.fromkeys(
            str(v["id"])
            for v in groups.get("nonAllergen", [])
            if isinstance(v, dict) and v.get("id")
        )
    )
    labels = list(
        dict.fromkeys(
            str(v["id"])
            for v in (groups.get("packagingMarkedLabel", []) + groups.get("nutritionalClaim", []))
            if isinstance(v, dict) and v.get("id")
        )
    )

    vendor = raw.get("vendorName") or raw.get("vendor")
    brand = sd.get("brand")
    temp_cond = raw.get("temperatureCondition")
    temp_class = valio_rules.TEMPERATURE.get(temp_cond, (None, "unknown"))[1]

    var_unit_raw = sd.get("variableUnit")
    if var_unit_raw is None:
        var_unit_raw = raw.get("variableUnit")
    variable_unit = bool(var_unit_raw) if var_unit_raw is not None else None

    deposits = [d for d in (raw.get("deposits") or []) if isinstance(d, dict)]
    substitutions = [
        str(s["substituteProduct"]) if isinstance(s, dict) else str(s)
        for s in (raw.get("substitutions") or [])
        if (isinstance(s, dict) and s.get("substituteProduct")) or isinstance(s, str)
    ]
    unit_conversions = [u for u in (sd.get("unitConversions") or []) if isinstance(u, dict)]

    nutrients_map = {k: [v[0], v[1]] for k, v in valio_rules.nutrients(raw).items()}
    search_parts = [
        product_id,
        name_fi or "",
        name_en or "",
        name_sv or "",
        brand or "",
        vendor or "",
        category_name or "",
    ]
    search_text = " ".join(part.strip() for part in search_parts if part and part.strip()).lower()

    return {
        "id": product_id,
        "name": name,
        "names": names_by_lang,
        "category": category,
        "category_name": category_name,
        "sales_unit": str(raw.get("salesUnit") or ""),
        "base_unit": str(raw.get("baseUnit") or ""),
        "units": units,
        "vendor": vendor,
        "brand": brand,
        "country_of_origin": raw.get("countryOfOrigin"),
        "temperature_condition": int(temp_cond) if temp_cond is not None else None,
        "allergens": allergens,
        "non_allergens": non_allergens,
        "labels": labels,
        "image_url": _extract_image_url(sd),
        "co2_kg_per_kg": co2_map.get(product_id),
        "delivery_reliability": reliability_map.get(product_id),
        "name_fi": name_fi,
        "name_en": name_en,
        "name_sv": name_sv,
        "temperature_class": temp_class,
        "allowed_lot_size": (
            float(raw["allowedLotSize"]) if raw.get("allowedLotSize") is not None else None
        ),
        "variable_unit": variable_unit,
        "deposits": deposits,
        "substitutions": substitutions,
        "unit_conversions": unit_conversions,
        "allergen_states": valio_rules.allergen_states(raw),
        "verified_claims": valio_rules.verified_claims(raw),
        "claims": sorted(valio_rules.claims(raw)),
        "nutrients": nutrients_map,
        "key_ingredients_text": _extract_key_ingredients(sd),
        "search_text": search_text,
    }


def _upsert_rows(
    session: Session,
    model: type[Category] | type[Product],
    pk_field: str,
    rows: list[dict[str, Any]],
    existing_keys: set[str],
) -> None:
    if not rows:
        return
    to_insert: list[dict[str, Any]] = []
    to_update: list[dict[str, Any]] = []
    for row in rows:
        key = row[pk_field]
        if key in existing_keys:
            to_update.append(row)
        else:
            to_insert.append(row)
            existing_keys.add(key)

    if to_insert:
        session.execute(insert(model), to_insert)
    if to_update:
        session.execute(update(model), to_update)


def seed_catalog(
    session: Session,
    products_path: Path | str = DEFAULT_PRODUCTS_PATH,
    co2_csv_path: Path | str | None = DEFAULT_CO2_PATH,
    reliability_csv_path: Path | str | None = DEFAULT_RELIABILITY_PATH,
    category_mapping_path: Path | str = DEFAULT_CATEGORIES_PATH,
    batch_size: int = 500,
) -> dict[str, int]:
    """Load category mapping, optional CSVs, and product JSON and upsert into the database."""
    products_file = Path(products_path)
    co2_file = Path(co2_csv_path) if co2_csv_path is not None else None
    reliability_file = Path(reliability_csv_path) if reliability_csv_path is not None else None
    categories_file = Path(category_mapping_path)

    category_map = valio_rules.load_category_mapping(categories_file)
    co2_map = _load_float_csv(co2_file, ("carbonFootprintKgPerUnit", "AVG kg CO2-eq/kg"))
    reliability_map = _load_float_csv(
        reliability_file,
        ("reliabilityPct", "deliveryReliability"),
        normalize_percentage=True,
    )

    with open(products_file, "rb") as f:
        raw_products: list[dict[str, Any]] = json.load(f)

    existing_product_ids = set(session.scalars(select(Product.id)))
    category_counts: Counter[str] = Counter()
    batch: list[dict[str, Any]] = []
    processed = 0
    skipped_deleted = 0

    for raw in raw_products:
        if raw.get("deleted"):
            skipped_deleted += 1
            continue
        row = _extract_product_row(raw, category_map, co2_map, reliability_map)
        category_counts[row["category"]] += 1
        batch.append(row)
        processed += 1
        if len(batch) >= batch_size:
            _upsert_rows(session, Product, "id", batch, existing_product_ids)
            batch.clear()

    if batch:
        _upsert_rows(session, Product, "id", batch, existing_product_ids)
        batch.clear()

    existing_cat_codes = set(session.scalars(select(Category.code)))
    category_rows = [
        {
            "code": code,
            "name_en": info["name_en"],
            "name_fi": info["name_fi"],
            "parent_group": info.get("parent_group"),
            "food": bool(info.get("food", True)),
            "confidence": info.get("confidence"),
            "product_count": category_counts.get(code, 0),
        }
        for code, info in category_map.items()
    ]
    _upsert_rows(session, Category, "code", category_rows, existing_cat_codes)
    session.commit()

    return {
        "products_upserted": processed,
        "products_skipped_deleted": skipped_deleted,
        "categories_upserted": len(category_rows),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed AimoAnnos catalog into the database.")
    parser.add_argument("--products", type=Path, default=DEFAULT_PRODUCTS_PATH)
    parser.add_argument("--co2", type=Path, default=DEFAULT_CO2_PATH)
    parser.add_argument("--reliability", type=Path, default=DEFAULT_RELIABILITY_PATH)
    parser.add_argument("--categories", type=Path, default=DEFAULT_CATEGORIES_PATH)
    parser.add_argument("--batch-size", type=int, default=500)
    args = parser.parse_args()
    if not Path(args.products).is_file():
        raise SystemExit(
            f"Product catalog JSON not found at {args.products}.\n"
            "Supply your own catalog via --products path/to/products.json "
            "(or place catalog_data/products.json at the repo root; see backend/README.md)."
        )

    t0 = time.perf_counter()
    init_db()
    with SessionLocal() as session:
        stats = seed_catalog(
            session,
            products_path=args.products,
            co2_csv_path=args.co2,
            reliability_csv_path=args.reliability,
            category_mapping_path=args.categories,
            batch_size=args.batch_size,
        )
    elapsed = time.perf_counter() - t0
    print(
        f"Catalog seeded in {elapsed:.2f}s: "
        f"{stats['products_upserted']} products "
        f"(skipped {stats['products_skipped_deleted']} deleted), "
        f"{stats['categories_upserted']} categories."
    )


if __name__ == "__main__":
    main()
