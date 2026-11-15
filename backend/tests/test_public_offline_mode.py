"""Offline public-repo tests: sanitized defaults, 3 demo personas, optional CSVs, synthetic Aimo."""

from __future__ import annotations

import json
from collections.abc import Generator
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.aimo.client import AimoClient, get_aimo_client
from app.config import Settings, settings
from app.db import get_db, init_db, make_engine
from app.main import create_app
from app.models import Agent, Company, Order, Product, User
from scripts.demo_seed import seed_demo
from scripts.seed_catalog import DEFAULT_CATEGORIES_PATH, seed_catalog


def _synthetic_catalog_json() -> list[dict[str, object]]:
    return [
        {
            "id": "900101",
            "category": "10400",
            "salesUnit": "ST",
            "baseUnit": "ST",
            "units": [{"unitId": "ST", "sizeInBaseUnits": 1}],
            "vendorName": "Nordic Dairy Co",
            "countryOfOrigin": "FI",
            "temperatureCondition": 3,
            "synkkaData": {
                "brand": "Nordic",
                "names": [
                    {"language": "fi", "value": "Laktoositon kevytmaitojuoma 1 l"},
                    {"language": "en", "value": "Lactose-free semi-skimmed milk drink 1 L"},
                ],
                "classifications": [
                    {
                        "name": "allergen",
                        "values": [{"id": "AM", "unit": "CONTAINS"}],
                    },
                    {
                        "name": "nutritionalClaim",
                        "values": [{"id": "FREE_FROM_LACTOSE"}],
                    },
                ],
                "nutritionalContent": [
                    {
                        "id": "LACS",
                        "value": 0.0,
                        "unit": "GRM",
                        "basisQuantity": 100,
                        "basisQuantityUnit": "MLT",
                        "unitPrecision": "APPROXIMATELY",
                    }
                ],
            },
        }
    ]


def test_public_sanitized_settings_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify public default settings contain zero private endpoints or bot handles."""
    for env_key in (
        "AIMO_BASE_URL",
        "aimo_base_url",
        "AIMO_MODE",
        "aimo_mode",
        "TELEGRAM_BOT_USERNAME",
        "telegram_bot_username",
    ):
        monkeypatch.delenv(env_key, raising=False)

    clean = Settings(_env_file=None)
    assert clean.aimo_base_url == "https://api.example.invalid"
    assert clean.aimo_mode == "fixture"
    assert clean.telegram_bot_username == "your_aimo_bot"


def test_demo_seed_empty_catalog_and_three_generic_personas(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify demo_seed runs cleanly on an empty products table and exposes only 3 personas."""
    monkeypatch.setenv("APP_NOW", "2026-11-15T10:00:00+02:00")
    monkeypatch.setattr(settings, "jwt_secret", "test-secret-key-with-at-least-32-bytes")
    monkeypatch.setattr(settings, "app_env", "dev")

    db_file = tmp_path / "empty_catalog_demo.db"
    engine = make_engine(f"sqlite:///{db_file}")
    init_db(target_engine=engine)
    session_factory = sessionmaker(
        bind=engine, autoflush=False, autocommit=False, expire_on_commit=False
    )

    creds_path = tmp_path / ".demo_credentials"
    with session_factory() as db:
        creds = seed_demo(
            db,
            credentials_path=creds_path,
            with_sample_orders=True,
            with_extra_personas=True,
        )
        assert creds["sample_orders_created"] == 0
        assert db.scalar(select(func.count()).select_from(Company)) == 3
        assert db.scalar(select(func.count()).select_from(User)) == 3
        assert db.scalar(select(func.count()).select_from(Agent)) == 9
        assert db.scalar(select(func.count()).select_from(Order)) == 0

    app = create_app()

    def _override_db() -> Generator[Session, None, None]:
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = _override_db
    try:
        with TestClient(app) as client:
            resp = client.get("/api/auth/demo-personas")
            assert resp.status_code == 200
            personas = resp.json()["items"]
            assert [p["email"] for p in personas] == [
                "anna.korhonen@hotel.fi",
                "mikko.laine@restaurant.fi",
                "sari.virtanen@festival.fi",
            ]
            assert all(p["group"] == "demo" for p in personas)

            monkeypatch.setattr(settings, "app_env", "prod")
            assert client.get("/api/auth/demo-personas").status_code == 404
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def test_user_supplied_catalog_optional_csvs_and_synthetic_aimo_fixture_flow(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify seeding without optional CSVs + synthetic fallback price/availability + order."""
    monkeypatch.setenv("APP_NOW", "2026-11-15T10:00:00+02:00")
    monkeypatch.setattr(settings, "jwt_secret", "test-secret-key-with-at-least-32-bytes")
    monkeypatch.setattr(settings, "aimo_mode", "fixture")

    products_json = tmp_path / "products.json"
    products_json.write_text(json.dumps(_synthetic_catalog_json()), encoding="utf-8")
    missing_co2 = tmp_path / "missing_co2.csv"
    missing_rel = tmp_path / "missing_reliability.csv"

    db_file = tmp_path / "synthetic_flow.db"
    engine = make_engine(f"sqlite:///{db_file}")
    init_db(target_engine=engine)
    session_factory = sessionmaker(
        bind=engine, autoflush=False, autocommit=False, expire_on_commit=False
    )

    # 1. Seed with missing optional CSVs -> succeeds cleanly
    with session_factory() as db:
        stats = seed_catalog(
            db,
            products_path=products_json,
            co2_csv_path=missing_co2,
            reliability_csv_path=missing_rel,
            category_mapping_path=DEFAULT_CATEGORIES_PATH,
        )
        assert stats["products_upserted"] == 1
        p = db.get(Product, "900101")
        assert p is not None
        assert p.co2_kg_per_kg is None
        assert p.delivery_reliability is None

    # 2. Re-seed with user-supplied optional CSVs (carbonFootprintKgPerUnit & reliabilityPct)
    co2_csv = tmp_path / "carbon_footprint.csv"
    co2_csv.write_text("productId,carbonFootprintKgPerUnit\n900101,1.15\n", encoding="utf-8")
    rel_csv = tmp_path / "delivery_reliability.csv"
    rel_csv.write_text("productId,reliabilityPct\n900101,98.5\n", encoding="utf-8")

    with session_factory() as db:
        seed_catalog(
            db,
            products_path=products_json,
            co2_csv_path=co2_csv,
            reliability_csv_path=rel_csv,
            category_mapping_path=DEFAULT_CATEGORIES_PATH,
        )
        creds = seed_demo(db, credentials_path=tmp_path / ".demo_credentials")
        p = db.get(Product, "900101")
        assert p is not None
        assert p.co2_kg_per_kg == pytest.approx(1.15)
        assert p.delivery_reliability == pytest.approx(0.985)

    # 3. Synthetic fallback AimoClient when fixture_dir has no price.json / availability.json
    empty_fixture_dir = tmp_path / "empty_aimo_fixtures"
    synthetic_client = AimoClient(
        mode="fixture", fixture_dir=empty_fixture_dir, cache_ttl_seconds=0.0
    )
    assert synthetic_client.ping() is True
    price_payload = synthetic_client.price(["900101"], "2026-11-17")
    assert price_payload["products"][0]["catalogPrice"] == {"value": 2.50, "valueWithTax": 2.84}
    assert price_payload["products"][0]["tax"] == {"taxRate": 13.5}
    assert price_payload["products"][0]["netPriceFound"] is True
    assert price_payload["products"][0]["error"] is False

    avail_payload = synthetic_client.availability(["900101"], "2026-11-17")
    avail_item = avail_payload["products"][0]
    assert avail_item["logisticModel"] == "Stock"
    assert avail_item["scheduleLines"][0]["availableAmount"] == 500.0
    closing_dt = datetime.fromisoformat(avail_item["closingTime"])
    assert closing_dt.date().isoformat() == "2026-11-19"

    # 4. End-to-end quote -> order placement in offline fallback fixture mode
    app = create_app()

    def _override_db() -> Generator[Session, None, None]:
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_aimo_client] = lambda: synthetic_client
    try:
        with TestClient(app) as client:
            kitchen = creds["agents"]["kitchen_agent"]
            tok = client.post(
                "/api/agent-auth/token",
                json={"agent_id": kitchen["id"], "agent_secret": kitchen["agent_secret"]},
            )
            assert tok.status_code == 200
            headers = {"Authorization": f"Bearer {tok.json()['token']}"}

            q_resp = client.post(
                "/api/quotes",
                headers=headers,
                json={
                    "requested_delivery_date": "2026-11-17",
                    "lines": [{"product_id": "900101", "quantity": 10}],
                    "diet": "L",
                },
            )
            assert q_resp.status_code == 201, q_resp.text
            quote = q_resp.json()
            assert quote["total_excl_vat_eur"] == 25.0
            assert quote["policy_preview"]["outcome"] == "AUTO"

            o_resp = client.post(
                "/api/orders",
                headers={**headers, "Idempotency-Key": "offline-public-1"},
                json={"quote_id": quote["id"], "note": "Morning milk restock"},
            )
            assert o_resp.status_code == 201, o_resp.text
            assert o_resp.json()["status"] == "PLACED"

            # Hard diet gate still blocks milk-free (M) on dairy item 900101!
            bad_q = client.post(
                "/api/quotes",
                headers=headers,
                json={
                    "requested_delivery_date": "2026-11-17",
                    "lines": [{"product_id": "900101", "quantity": 5}],
                    "diet": "M",
                },
            )
            assert bad_q.status_code == 201
            assert bad_q.json()["policy_preview"]["outcome"] == "BLOCKED"
    finally:
        app.dependency_overrides.clear()
        engine.dispose()
