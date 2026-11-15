"""Tests for DB setup, valio_rules namespace isolation, IDs, and catalog seed."""

import json
from pathlib import Path

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.db import init_db, make_engine
from app.domain import valio_rules
from app.ids import new_id
from app.models import Category, Product
from scripts.seed_catalog import (
    DEFAULT_CATEGORIES_PATH,
    DEFAULT_CO2_PATH,
    DEFAULT_RELIABILITY_PATH,
    seed_catalog,
)

FIXTURE_PRODUCTS_PATH = Path(__file__).resolve().parent / "fixtures" / "products_sample.json"


def test_prefixed_ulid_generation() -> None:
    for prefix in ("cmp_", "usr_", "agt_", "qte_", "ord_", "evt_", "aud_", "cnv_", "tsk_"):
        generated = new_id(prefix)
        assert generated.startswith(prefix)
        assert len(generated) == len(prefix) + 26
    assert new_id("cmp").startswith("cmp_")


def test_valio_rules_unit_vs_allergen_namespace_isolation() -> None:
    # "ST" is piece in UNIT and pistachio in ALLERGEN; never conflated.
    assert valio_rules.UNIT["ST"][0] == "piece"
    assert valio_rules.UNIT["ST"][1] == "kpl"
    assert valio_rules.ALLERGEN["ST"][0] == "pistachio"

    raw_products = json.loads(FIXTURE_PRODUCTS_PATH.read_text(encoding="utf-8"))
    milk = next(p for p in raw_products if p["id"] == "464306")
    assert milk["salesUnit"] == "ST"
    states = valio_rules.allergen_states(milk)
    assert states["ST"] != "PRESENT"
    assert states["AM"] == "PRESENT"


def test_sqlite_pragmas_and_idempotent_catalog_seed(tmp_path: Path) -> None:
    db_file = tmp_path / "catalog_test.db"
    engine = make_engine(f"sqlite:///{db_file}")
    try:
        init_db(target_engine=engine)

        with engine.connect() as conn:
            journal_mode = conn.execute(text("PRAGMA journal_mode")).scalar()
            foreign_keys = conn.execute(text("PRAGMA foreign_keys")).scalar()
            busy_timeout = conn.execute(text("PRAGMA busy_timeout")).scalar()
        assert str(journal_mode).lower() == "wal"
        assert foreign_keys == 1
        assert busy_timeout == 5000

        with Session(engine) as session:
            stats_first = seed_catalog(
                session,
                products_path=FIXTURE_PRODUCTS_PATH,
                co2_csv_path=DEFAULT_CO2_PATH,
                reliability_csv_path=DEFAULT_RELIABILITY_PATH,
                category_mapping_path=DEFAULT_CATEGORIES_PATH,
                batch_size=15,
            )
            assert stats_first["products_upserted"] == 50
            assert stats_first["categories_upserted"] == 349

            # Re-run idempotently
            stats_second = seed_catalog(
                session,
                products_path=FIXTURE_PRODUCTS_PATH,
                co2_csv_path=DEFAULT_CO2_PATH,
                reliability_csv_path=DEFAULT_RELIABILITY_PATH,
                category_mapping_path=DEFAULT_CATEGORIES_PATH,
                batch_size=15,
            )
            assert stats_second["products_upserted"] == 50

            product_count = session.scalar(select(func.count()).select_from(Product))
            category_count = session.scalar(select(func.count()).select_from(Category))
            total_cat_products = session.scalar(select(func.sum(Category.product_count)))
            assert product_count == 50
            assert category_count == 349
            assert total_cat_products == 50

            # 464306: lactose-free milk drink (contains milk AM, verified FREE_FROM_LACTOSE)
            p464306 = session.get(Product, "464306")
            assert p464306 is not None
            assert p464306.allergen_states["AM"] == "PRESENT"
            assert p464306.allergen_states["ST"] != "PRESENT"
            assert p464306.verified_claims["FREE_FROM_LACTOSE"] == "VERIFIED"
            assert p464306.co2_kg_per_kg is not None
            assert len(p464306.units) >= 1
            assert all("unit_id" in u and "size_in_base_units" in u for u in p464306.units)
            assert "464398" in p464306.substitutions
            assert "469363" in p464306.substitutions
            assert p464306.category == "10400"
            assert p464306.category_name is not None
            assert p464306.temperature_class == "chilled"
            assert "464306" in p464306.search_text

            # 460013: produce with missing allergen statements & no Synkka nutritional claims
            p460013 = session.get(Product, "460013")
            assert p460013 is not None
            assert p460013.allergen_states["AM"] == "UNKNOWN"
            assert p460013.claims == []
            assert p460013.verified_claims == {}

            # Category 10400 populated and counted
            cat_10400 = session.get(Category, "10400")
            assert cat_10400 is not None
            assert cat_10400.product_count >= 4
            assert cat_10400.food is True
    finally:
        engine.dispose()


def test_seed_skips_deleted_products(tmp_path: Path) -> None:
    raw = json.loads(FIXTURE_PRODUCTS_PATH.read_text(encoding="utf-8"))[:3]
    raw[1] = {**raw[1], "deleted": True}
    custom_fixture = tmp_path / "with_deleted.json"
    custom_fixture.write_text(json.dumps(raw), encoding="utf-8")

    engine = make_engine(f"sqlite:///{tmp_path / 'deleted_test.db'}")
    try:
        init_db(target_engine=engine)
        with Session(engine) as session:
            stats = seed_catalog(
                session,
                products_path=custom_fixture,
                co2_csv_path=DEFAULT_CO2_PATH,
                reliability_csv_path=DEFAULT_RELIABILITY_PATH,
                category_mapping_path=DEFAULT_CATEGORIES_PATH,
            )
            assert stats["products_upserted"] == 2
            assert stats["products_skipped_deleted"] == 1
            assert session.scalar(select(func.count()).select_from(Product)) == 2
            assert session.get(Product, raw[1]["id"]) is None
    finally:
        engine.dispose()
