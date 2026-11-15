"""Tests for Milestone M6: Demo seed script and end-to-end external agent demo flow."""

from __future__ import annotations

import json
from collections.abc import Generator
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.aimo.client import AimoClient, get_aimo_client
from app.config import settings
from app.db import get_db, init_db, make_engine
from app.main import create_app
from app.models import Agent, Company, Order, User
from scripts.demo_seed import seed_demo
from scripts.seed_catalog import (
    DEFAULT_CATEGORIES_PATH,
    DEFAULT_CO2_PATH,
    DEFAULT_RELIABILITY_PATH,
    seed_catalog,
)

FIXTURE_PRODUCTS_PATH = Path(__file__).resolve().parent / "fixtures" / "products_sample.json"
AIMO_FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "aimo"


@pytest.fixture()
def m6_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Generator[tuple[TestClient, sessionmaker[Session], Path], None, None]:
    """Isolated SQLite DB seeded with 50 sample products + fixture AimoClient."""
    monkeypatch.setenv("APP_NOW", "2026-11-13T21:00:00+02:00")
    monkeypatch.setattr(settings, "jwt_secret", "test-secret-key-with-at-least-32-bytes")
    monkeypatch.setattr(settings, "aimo_mode", "fixture")

    db_file = tmp_path / "m6_test.db"
    test_engine = make_engine(f"sqlite:///{db_file}")
    init_db(target_engine=test_engine)
    session_factory = sessionmaker(
        bind=test_engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )

    with session_factory() as db:
        seed_catalog(
            db,
            products_path=FIXTURE_PRODUCTS_PATH,
            co2_csv_path=DEFAULT_CO2_PATH,
            reliability_csv_path=DEFAULT_RELIABILITY_PATH,
            category_mapping_path=DEFAULT_CATEGORIES_PATH,
            batch_size=25,
        )

    test_app = create_app()
    local_client = AimoClient(mode="fixture", fixture_dir=AIMO_FIXTURE_DIR, cache_ttl_seconds=0.0)

    def _override_get_db() -> Generator[Session, None, None]:
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    test_app.dependency_overrides[get_db] = _override_get_db
    test_app.dependency_overrides[get_aimo_client] = lambda: local_client

    creds_path = tmp_path / ".demo_credentials"
    client = TestClient(test_app)
    try:
        yield client, session_factory, creds_path
    finally:
        test_app.dependency_overrides.clear()
        test_engine.dispose()


def test_seed_demo_creates_records_writes_credentials_and_is_idempotent(
    m6_env: tuple[TestClient, sessionmaker[Session], Path],
) -> None:
    client, session_factory, creds_path = m6_env

    with session_factory() as db:
        first = seed_demo(db, credentials_path=creds_path)

    assert creds_path.exists()
    written = json.loads(creds_path.read_text(encoding="utf-8"))
    assert written["company"]["name"] == "Hotel Aurora Oy"
    assert written["company"]["business_id"] == "1234567-8"
    assert written["user"]["name"] == "Anna Korhonen"
    assert written["user"]["email"] == "anna.korhonen@hotel.fi"
    assert set(written["agents"].keys()) == {"kitchen_agent", "reorder_bot", "read_only_scout"}

    kitchen = written["agents"]["kitchen_agent"]
    reorder = written["agents"]["reorder_bot"]
    scout = written["agents"]["read_only_scout"]
    assert kitchen["agent_secret"].startswith("aks_")
    assert reorder["agent_secret"].startswith("aks_")
    assert scout["agent_secret"].startswith("aks_")
    assert kitchen["can_place_orders"] is True
    assert kitchen["auto_approve_limit_eur"] == 500.0
    assert kitchen["max_order_value_eur"] == 5000.0
    assert kitchen["daily_spend_limit_eur"] == 3000.0
    assert reorder["can_place_orders"] is True
    assert scout["can_place_orders"] is False
    assert scout["max_order_value_eur"] == 0.0

    # Demo user can log in via canonical .fi email AND legacy .test alias
    login_resp = client.post(
        "/api/auth/login",
        json={"email": "anna.korhonen@hotel.fi", "password": "demo1234"},
    )
    assert login_resp.status_code == 200
    assert login_resp.json()["user"]["name"] == "Anna Korhonen"

    legacy_login_resp = client.post(
        "/api/auth/login",
        json={"email": "demo@aimoannos.test", "password": "demo1234"},
    )
    assert legacy_login_resp.status_code == 200
    assert legacy_login_resp.json()["user"]["id"] == login_resp.json()["user"]["id"]
    assert legacy_login_resp.json()["user"]["name"] == "Anna Korhonen"

    # Re-run seed_demo idempotently (with sample orders toggled on and off)
    with session_factory() as db:
        second = seed_demo(db, credentials_path=creds_path, with_sample_orders=True)
        third = seed_demo(db, credentials_path=creds_path, with_sample_orders=True)

    assert first["company"]["id"] == second["company"]["id"] == third["company"]["id"]
    assert first["user"]["id"] == second["user"]["id"] == third["user"]["id"]
    for key in ("kitchen_agent", "reorder_bot", "read_only_scout"):
        assert (
            first["agents"][key]["id"] == second["agents"][key]["id"] == third["agents"][key]["id"]
        )
    # Secrets rotated on re-seed: old secret rejected, latest secret accepted
    assert (
        first["agents"]["kitchen_agent"]["agent_secret"]
        != third["agents"]["kitchen_agent"]["agent_secret"]
    )
    assert second["sample_orders_created"] == 1
    assert third["sample_orders_created"] == 0

    old_auth = client.post(
        "/api/agent-auth/token",
        json={
            "agent_id": first["agents"]["kitchen_agent"]["id"],
            "agent_secret": first["agents"]["kitchen_agent"]["agent_secret"],
        },
    )
    assert old_auth.status_code == 401

    new_auth = client.post(
        "/api/agent-auth/token",
        json={
            "agent_id": third["agents"]["kitchen_agent"]["id"],
            "agent_secret": third["agents"]["kitchen_agent"]["agent_secret"],
        },
    )
    assert new_auth.status_code == 200

    with session_factory() as db:
        assert db.scalar(select(func.count()).select_from(Company)) == 1
        assert db.scalar(select(func.count()).select_from(User)) == 1
        assert db.scalar(select(func.count()).select_from(Agent)) == 3
        assert db.scalar(select(func.count()).select_from(Order)) == 1


def test_demo_agent_end_to_end_sequence(
    m6_env: tuple[TestClient, sessionmaker[Session], Path],
) -> None:
    client, session_factory, creds_path = m6_env
    with session_factory() as db:
        creds = seed_demo(db, credentials_path=creds_path)

    # 1. Health check & compute next Monday from X-Server-Time
    health = client.get("/api/health")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"
    srv_dt = datetime.fromisoformat(health.headers["X-Server-Time"])
    days_ahead = (0 - srv_dt.weekday()) % 7 or 7
    next_monday = (srv_dt.date() + timedelta(days=days_ahead)).isoformat()
    assert next_monday == "2026-11-16"

    # 2. Exchange Hotel Aurora Kitchen Agent credentials
    kitchen_creds = creds["agents"]["kitchen_agent"]
    tok_resp = client.post(
        "/api/agent-auth/token",
        json={
            "agent_id": kitchen_creds["id"],
            "agent_secret": kitchen_creds["agent_secret"],
        },
    )
    assert tok_resp.status_code == 200
    kitchen_headers = {"Authorization": f"Bearer {tok_resp.json()['token']}"}

    # 3. Search lactose-free milk (diet=L) -> finds 464306
    search_resp = client.get(
        "/api/products",
        params={"q": "laktoositon maito", "diet": "L", "limit": 5},
        headers=kitchen_headers,
    )
    assert search_resp.status_code == 200
    product_ids = [item["id"] for item in search_resp.json()["items"]]
    assert "464306" in product_ids

    # 4. Quote 1,000 x 464306 for next Monday -> APPROVAL_REQUIRED, non-null CO2 + co2_basis
    quote_resp = client.post(
        "/api/quotes",
        headers=kitchen_headers,
        json={
            "requested_delivery_date": next_monday,
            "lines": [{"product_id": "464306", "quantity": 1000}],
            "diet": "L",
        },
    )
    assert quote_resp.status_code == 201
    quote = quote_resp.json()
    assert quote["total_excl_vat_eur"] == 1010.0
    assert quote["total_co2_kg"] is not None and quote["total_co2_kg"] > 0
    assert quote["lines"][0]["co2_basis"]
    assert quote["policy_preview"]["outcome"] == "APPROVAL_REQUIRED"

    # 5. Place order with Idempotency-Key -> PENDING_APPROVAL (R-AUTO-LIMIT)
    order_resp = client.post(
        "/api/orders",
        headers={**kitchen_headers, "Idempotency-Key": "demo-step-5"},
        json={"quote_id": quote["id"], "note": "Monday breakfast milk"},
    )
    assert order_resp.status_code == 201
    order = order_resp.json()
    assert order["status"] == "PENDING_APPROVAL"
    assert any(r["rule_id"] == "R-AUTO-LIMIT" for r in order["policy"]["rules"])

    # 6a. Exception: 1,000 x 469363 partial stock (384) -> substitutes include 464306 / 464398
    partial_quote_resp = client.post(
        "/api/quotes",
        headers=kitchen_headers,
        json={
            "requested_delivery_date": next_monday,
            "lines": [{"product_id": "469363", "quantity": 1000}],
            "diet": "L",
        },
    )
    assert partial_quote_resp.status_code == 201
    partial_line = partial_quote_resp.json()["lines"][0]
    assert partial_line["available_qty_state"] == "FINITE"
    assert partial_line["available_amount"] == 384.0
    assert "PARTIAL" in partial_line["flags"]

    subs_resp = client.post(
        "/api/products/substitutes",
        headers=kitchen_headers,
        json={
            "product_id": "469363",
            "quantity": 1000,
            "requested_delivery_date": next_monday,
            "diet": "L",
        },
    )
    assert subs_resp.status_code == 200
    sub_ids = [item["product"]["id"] for item in subs_resp.json()["items"]]
    assert "464306" in sub_ids
    assert "464398" in sub_ids

    # 6b. Exception: 10,000 x 464306 -> 403 POLICY_BLOCKED (R-MAX-VALUE)
    max_quote_resp = client.post(
        "/api/quotes",
        headers=kitchen_headers,
        json={
            "requested_delivery_date": next_monday,
            "lines": [{"product_id": "464306", "quantity": 10000}],
            "diet": "L",
        },
    )
    assert max_quote_resp.status_code == 201
    max_order_resp = client.post(
        "/api/orders",
        headers={**kitchen_headers, "Idempotency-Key": "demo-step-6b"},
        json={"quote_id": max_quote_resp.json()["id"]},
    )
    assert max_order_resp.status_code == 403
    max_err = max_order_resp.json()
    assert max_err["error"]["code"] == "POLICY_BLOCKED"
    assert any(r["rule_id"] == "R-MAX-VALUE" for r in max_err["error"]["details"]["rules"])

    # 6c. Exception: diet=M on 464306 -> 403 POLICY_BLOCKED (R-HARD-DIET)
    diet_quote_resp = client.post(
        "/api/quotes",
        headers=kitchen_headers,
        json={
            "requested_delivery_date": next_monday,
            "lines": [{"product_id": "464306", "quantity": 10}],
            "diet": "M",
        },
    )
    assert diet_quote_resp.status_code == 201
    diet_order_resp = client.post(
        "/api/orders",
        headers={**kitchen_headers, "Idempotency-Key": "demo-step-6c"},
        json={"quote_id": diet_quote_resp.json()["id"], "diet": "M"},
    )
    assert diet_order_resp.status_code == 403
    diet_err = diet_order_resp.json()
    assert diet_err["error"]["code"] == "POLICY_BLOCKED"
    assert any(r["rule_id"] == "R-HARD-DIET" for r in diet_err["error"]["details"]["rules"])

    # 7. Read-only Scout attempt to order -> 403 POLICY_BLOCKED (R-AGENT-NO-ORDER)
    scout_creds = creds["agents"]["read_only_scout"]
    scout_tok_resp = client.post(
        "/api/agent-auth/token",
        json={
            "agent_id": scout_creds["id"],
            "agent_secret": scout_creds["agent_secret"],
        },
    )
    assert scout_tok_resp.status_code == 200
    scout_headers = {"Authorization": f"Bearer {scout_tok_resp.json()['token']}"}

    scout_quote_resp = client.post(
        "/api/quotes",
        headers=scout_headers,
        json={
            "requested_delivery_date": next_monday,
            "lines": [{"product_id": "464306", "quantity": 10}],
        },
    )
    assert scout_quote_resp.status_code == 201
    scout_order_resp = client.post(
        "/api/orders",
        headers={**scout_headers, "Idempotency-Key": "demo-step-7"},
        json={"quote_id": scout_quote_resp.json()["id"]},
    )
    assert scout_order_resp.status_code == 403
    scout_err = scout_order_resp.json()
    assert scout_err["error"]["code"] == "POLICY_BLOCKED"
    assert any(r["rule_id"] == "R-AGENT-NO-ORDER" for r in scout_err["error"]["details"]["rules"])

    # 8. Human approval flow (Anna logs in and approves PENDING_APPROVAL order -> PLACED)
    anna_login = client.post(
        "/api/auth/login",
        json={"email": "anna.korhonen@hotel.fi", "password": "demo1234"},
    )
    assert anna_login.status_code == 200
    anna_headers = {"Authorization": f"Bearer {anna_login.json()['token']}"}

    approve_resp = client.post(
        f"/api/orders/{order['id']}/approve",
        headers={**anna_headers, "Idempotency-Key": "demo-step-8-approve"},
        json={"content_hash": order["content_hash"], "note": "Approved on phone"},
    )
    assert approve_resp.status_code == 200
    approved = approve_resp.json()
    assert approved["status"] == "PLACED"
    assert approved["gateway_reference"] is not None
