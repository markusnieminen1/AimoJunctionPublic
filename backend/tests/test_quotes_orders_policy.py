"""Tests for Milestone M5: Quotes, Policy, Orders, Idempotency, Dashboard, and Audit."""

from __future__ import annotations

import copy
import json
from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.aimo.client import AimoClient, get_aimo_client
from app.config import settings
from app.db import get_db, init_db, make_engine
from app.main import create_app
from app.models import Order
from scripts.seed_catalog import (
    DEFAULT_CATEGORIES_PATH,
    DEFAULT_CO2_PATH,
    DEFAULT_RELIABILITY_PATH,
    seed_catalog,
)

FIXTURE_PRODUCTS_PATH = Path(__file__).resolve().parent / "fixtures" / "products_sample.json"
AIMO_FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "aimo"


class MutablePriceAimoClient(AimoClient):
    """Fixture-backed AimoClient allowing tests to override price/availability per product."""

    def __init__(self, fixture_dir: Path) -> None:
        super().__init__(mode="fixture", fixture_dir=fixture_dir, cache_ttl_seconds=0.0)
        self.price_overrides: dict[str, dict] = {}
        self.avail_overrides: dict[str, dict] = {}

    def price(self, product_ids: list[str], requested_date: str, customer: str = "123") -> dict:
        data = copy.deepcopy(super().price(product_ids, requested_date, customer))
        for idx, item in enumerate(data.get("products", [])):
            pid = str(item.get("productId"))
            if pid in self.price_overrides:
                data["products"][idx] = copy.deepcopy(self.price_overrides[pid])
        return data

    def availability(
        self, product_ids: list[str], requested_date: str, customer: str = "123"
    ) -> dict:
        data = copy.deepcopy(super().availability(product_ids, requested_date, customer))
        for idx, item in enumerate(data.get("products", [])):
            pid = str(item.get("productId"))
            if pid in self.avail_overrides:
                data["products"][idx] = copy.deepcopy(self.avail_overrides[pid])
        return data


@pytest.fixture()
def m5_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Generator[tuple[TestClient, sessionmaker[Session], MutablePriceAimoClient], None, None]:
    """Isolated SQLite DB seeded with 50 sample products + mutable fixture AimoClient."""
    monkeypatch.setenv("APP_NOW", "2026-11-13T21:00:00+02:00")
    monkeypatch.setattr(settings, "jwt_secret", "test-secret-key-with-at-least-32-bytes")
    monkeypatch.setattr(settings, "aimo_mode", "fixture")

    db_file = tmp_path / "m5_test.db"
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
    local_client = MutablePriceAimoClient(fixture_dir=AIMO_FIXTURE_DIR)

    def _override_get_db() -> Generator[Session, None, None]:
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    test_app.dependency_overrides[get_db] = _override_get_db
    test_app.dependency_overrides[get_aimo_client] = lambda: local_client

    client = TestClient(test_app)
    try:
        yield client, session_factory, local_client
    finally:
        test_app.dependency_overrides.clear()
        test_engine.dispose()


def _register_user(
    client: TestClient,
    *,
    company_name: str = "Hotel Aurora Oy",
    user_name: str = "Chef",
    email: str = "chef@aurora.test",
) -> tuple[dict[str, str], dict]:
    resp = client.post(
        "/api/auth/register",
        json={
            "company_name": company_name,
            "user_name": user_name,
            "email": email,
            "password": "password123",
        },
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    return {"Authorization": f"Bearer {data['token']}"}, data


def _create_agent_and_headers(
    client: TestClient,
    user_headers: dict[str, str],
    *,
    name: str = "Kitchen Reorder Bot",
    auto_approve_limit_eur: float = 500.0,
    max_order_value_eur: float = 5000.0,
    daily_spend_limit_eur: float = 3000.0,
    allowed_categories: list[str] | None = None,
    max_quantity_per_line: float | None = None,
    can_place_orders: bool = True,
) -> tuple[dict[str, str], dict]:
    create_resp = client.post(
        "/api/agents",
        headers=user_headers,
        json={
            "name": name,
            "auto_approve_limit_eur": auto_approve_limit_eur,
            "max_order_value_eur": max_order_value_eur,
            "daily_spend_limit_eur": daily_spend_limit_eur,
            "allowed_categories": allowed_categories or [],
            "max_quantity_per_line": max_quantity_per_line,
            "can_place_orders": can_place_orders,
        },
    )
    assert create_resp.status_code == 201, create_resp.text
    created = create_resp.json()
    tok_resp = client.post(
        "/api/agent-auth/token",
        json={
            "agent_id": created["agent"]["id"],
            "agent_secret": created["agent_secret"],
        },
    )
    assert tok_resp.status_code == 200, tok_resp.text
    return {"Authorization": f"Bearer {tok_resp.json()['token']}"}, created["agent"]


def test_full_demo_flow_approval_auto_and_blocked(
    m5_env: tuple[TestClient, sessionmaker[Session], MutablePriceAimoClient],
) -> None:
    client, _, _ = m5_env
    user_headers, _ = _register_user(client)
    agent_headers, agent = _create_agent_and_headers(
        client,
        user_headers,
        auto_approve_limit_eur=500.0,
        max_order_value_eur=5000.0,
        daily_spend_limit_eur=3000.0,
    )

    # 1. Agent quotes 1,000 x 464306 (€1,010.00 > auto limit €500 -> APPROVAL_REQUIRED)
    q1_resp = client.post(
        "/api/quotes",
        headers=agent_headers,
        json={
            "requested_delivery_date": "2026-11-16",
            "lines": [{"product_id": "464306", "quantity": 1000}],
        },
    )
    assert q1_resp.status_code == 201, q1_resp.text
    q1 = q1_resp.json()
    assert q1["status"] == "OPEN"
    assert q1["total_excl_vat_eur"] == 1010.0
    assert q1["total_incl_vat_eur"] == 1146.35
    assert q1["total_co2_kg"] is not None
    assert q1["overall_delivery_status"] == "ON_TIME"
    assert q1["policy_preview"]["outcome"] == "APPROVAL_REQUIRED"
    assert [r["rule_id"] for r in q1["policy_preview"]["rules"]] == ["R-AUTO-LIMIT"]
    assert q1["created_by"]["delegated_by"]["id"] is not None

    # Agent places order -> PENDING_APPROVAL
    o1_resp = client.post(
        "/api/orders",
        headers={**agent_headers, "Idempotency-Key": "demo-order-1"},
        json={"quote_id": q1["id"], "note": "Monday dairy restock"},
    )
    assert o1_resp.status_code == 201, o1_resp.text
    o1 = o1_resp.json()
    assert o1["status"] == "PENDING_APPROVAL"
    assert o1["approval_expires_at"] is not None
    assert o1["gateway_reference"] is None
    assert [ev["type"] for ev in o1["events"]] == ["order.created"]

    # Agent cannot approve its own order -> 403 FORBIDDEN
    agent_approve = client.post(
        f"/api/orders/{o1['id']}/approve",
        headers={**agent_headers, "Idempotency-Key": "agent-try-approve"},
        json={"content_hash": o1["content_hash"]},
    )
    assert agent_approve.status_code == 403
    assert agent_approve.json()["error"]["code"] == "FORBIDDEN"

    # Human user approves with content_hash + Idempotency-Key -> PLACED
    approve_resp = client.post(
        f"/api/orders/{o1['id']}/approve",
        headers={**user_headers, "Idempotency-Key": "user-approve-1"},
        json={"content_hash": o1["content_hash"], "note": "Approved for Monday"},
    )
    assert approve_resp.status_code == 200, approve_resp.text
    approved = approve_resp.json()
    assert approved["status"] == "PLACED"
    assert approved["gateway_reference"] == f"aimo_sim_{o1['id'][-12:]}"
    assert approved["decided_by"]["kind"] == "user"
    assert approved["decision_note"] == "Approved for Monday"
    assert [ev["type"] for ev in approved["events"]] == [
        "order.created",
        "order.approved",
        "order.placed",
    ]

    # 2. Same agent quotes and orders 300 x 464306 (€303.00 <= 500) -> PLACED automatically
    q2_resp = client.post(
        "/api/quotes",
        headers=agent_headers,
        json={
            "requested_delivery_date": "2026-11-16",
            "lines": [{"product_id": "464306", "quantity": 300}],
        },
    )
    assert q2_resp.status_code == 201
    q2 = q2_resp.json()
    assert q2["policy_preview"]["outcome"] == "AUTO"
    assert [r["rule_id"] for r in q2["policy_preview"]["rules"]] == ["R-AGENT-WITHIN-LIMITS"]

    o2_resp = client.post(
        "/api/orders",
        headers={**agent_headers, "Idempotency-Key": "demo-order-2"},
        json={"quote_id": q2["id"]},
    )
    assert o2_resp.status_code == 201, o2_resp.text
    o2 = o2_resp.json()
    assert o2["status"] == "PLACED"
    assert o2["gateway_reference"] == f"aimo_sim_{o2['id'][-12:]}"
    assert [ev["type"] for ev in o2["events"]] == ["order.created", "order.placed"]

    # 3. Order > max_order_value_eur (6,000 x 464306 = €6,060 > 5000) -> 403 POLICY_BLOCKED
    #    and quote stays OPEN!
    q3_resp = client.post(
        "/api/quotes",
        headers=agent_headers,
        json={
            "requested_delivery_date": "2026-11-16",
            "lines": [{"product_id": "464306", "quantity": 6000}],
        },
    )
    assert q3_resp.status_code == 201
    q3 = q3_resp.json()
    assert q3["policy_preview"]["outcome"] == "BLOCKED"
    rule_ids = {r["rule_id"] for r in q3["policy_preview"]["rules"]}
    assert {"R-MAX-VALUE", "R-AUTO-LIMIT", "R-DAILY-SPEND"} <= rule_ids

    o3_resp = client.post(
        "/api/orders",
        headers={**agent_headers, "Idempotency-Key": "demo-order-3-blocked"},
        json={"quote_id": q3["id"]},
    )
    assert o3_resp.status_code == 403
    err = o3_resp.json()["error"]
    assert err["code"] == "POLICY_BLOCKED"
    assert err["details"]["outcome"] == "BLOCKED"

    q3_after = client.get(f"/api/quotes/{q3['id']}", headers=agent_headers)
    assert q3_after.status_code == 200
    assert q3_after.json()["status"] == "OPEN"

    # Agent spent_today_eur now reflects €1,010 + €303 = €1,313
    agent_detail = client.get(f"/api/agents/{agent['id']}", headers=user_headers)
    assert agent_detail.status_code == 200
    assert agent_detail.json()["spent_today_eur"] == 1313.0


def test_hard_diet_not_deliverable_and_read_only_agent_blocked(
    m5_env: tuple[TestClient, sessionmaker[Session], MutablePriceAimoClient],
) -> None:
    client, _, _ = m5_env
    user_headers, _ = _register_user(client)
    scout_headers, _ = _create_agent_and_headers(
        client,
        user_headers,
        name="Read-only Scout",
        can_place_orders=False,
    )

    # Diet M (MILK_FREE) on 464306 (milk) -> blocked for quote diet and order diet, even for user
    q_diet_resp = client.post(
        "/api/quotes",
        headers=user_headers,
        json={
            "requested_delivery_date": "2026-11-16",
            "lines": [{"product_id": "464306", "quantity": 10}],
            "diet": "M",
        },
    )
    assert q_diet_resp.status_code == 201
    q_diet = q_diet_resp.json()
    assert q_diet["policy_preview"]["outcome"] == "BLOCKED"
    assert [r["rule_id"] for r in q_diet["policy_preview"]["rules"]] == ["R-HARD-DIET"]

    o_diet_resp = client.post(
        "/api/orders",
        headers={**user_headers, "Idempotency-Key": "diet-block-1"},
        json={"quote_id": q_diet["id"]},
    )
    assert o_diet_resp.status_code == 403
    assert o_diet_resp.json()["error"]["code"] == "POLICY_BLOCKED"
    assert o_diet_resp.json()["error"]["details"]["rules"][0]["rule_id"] == "R-HARD-DIET"

    # Open quote without diet, then diet="M" passed at order time -> also R-HARD-DIET
    q_plain = client.post(
        "/api/quotes",
        headers=user_headers,
        json={
            "requested_delivery_date": "2026-11-16",
            "lines": [{"product_id": "464306", "quantity": 5}],
        },
    ).json()
    o_diet_late = client.post(
        "/api/orders",
        headers={**user_headers, "Idempotency-Key": "diet-block-2"},
        json={"quote_id": q_plain["id"], "diet": "M"},
    )
    assert o_diet_late.status_code == 403
    assert o_diet_late.json()["error"]["details"]["rules"][0]["rule_id"] == "R-HARD-DIET"

    # R-NOT-DELIVERABLE on 460013 -> 403 POLICY_BLOCKED
    q_nd = client.post(
        "/api/quotes",
        headers=user_headers,
        json={
            "requested_delivery_date": "2026-11-16",
            "lines": [{"product_id": "460013", "quantity": 10}],
        },
    ).json()
    assert q_nd["overall_delivery_status"] == "NOT_DELIVERABLE"
    assert q_nd["policy_preview"]["outcome"] == "BLOCKED"
    assert q_nd["policy_preview"]["rules"][0]["rule_id"] == "R-NOT-DELIVERABLE"

    o_nd = client.post(
        "/api/orders",
        headers={**user_headers, "Idempotency-Key": "nd-block"},
        json={"quote_id": q_nd["id"]},
    )
    assert o_nd.status_code == 403
    assert o_nd.json()["error"]["code"] == "POLICY_BLOCKED"

    # Read-only agent (can_place_orders=false) -> R-AGENT-NO-ORDER
    q_scout = client.post(
        "/api/quotes",
        headers=scout_headers,
        json={
            "requested_delivery_date": "2026-11-16",
            "lines": [{"product_id": "464306", "quantity": 5}],
        },
    ).json()
    assert q_scout["policy_preview"]["outcome"] == "BLOCKED"
    assert any(r["rule_id"] == "R-AGENT-NO-ORDER" for r in q_scout["policy_preview"]["rules"])
    o_scout = client.post(
        "/api/orders",
        headers={**scout_headers, "Idempotency-Key": "scout-block"},
        json={"quote_id": q_scout["id"]},
    )
    assert o_scout.status_code == 403
    assert o_scout.json()["error"]["code"] == "POLICY_BLOCKED"


def test_agent_policy_rules_category_line_qty_daily_spend_and_delivery_risk(
    m5_env: tuple[TestClient, sessionmaker[Session], MutablePriceAimoClient],
) -> None:
    client, _, _ = m5_env
    user_headers, _ = _register_user(client)

    # 1. R-CATEGORY: allowed_categories=["173"], ordering 464306 (category "10400", ON_TIME)
    cat_agent_headers, _ = _create_agent_and_headers(
        client,
        user_headers,
        name="Beverage Only Agent",
        auto_approve_limit_eur=500.0,
        max_order_value_eur=5000.0,
        daily_spend_limit_eur=3000.0,
        allowed_categories=["173"],
    )
    q_cat = client.post(
        "/api/quotes",
        headers=cat_agent_headers,
        json={
            "requested_delivery_date": "2026-11-16",
            "lines": [{"product_id": "464306", "quantity": 10}],
        },
    ).json()
    assert q_cat["policy_preview"]["outcome"] == "APPROVAL_REQUIRED"
    assert [r["rule_id"] for r in q_cat["policy_preview"]["rules"]] == ["R-CATEGORY"]

    # 2. R-LINE-QTY: max_quantity_per_line=50, ordering 60 x 464306 (€60.60 < 500)
    qty_agent_headers, _ = _create_agent_and_headers(
        client,
        user_headers,
        name="Small Qty Agent",
        auto_approve_limit_eur=500.0,
        max_order_value_eur=5000.0,
        daily_spend_limit_eur=3000.0,
        max_quantity_per_line=50.0,
    )
    q_qty = client.post(
        "/api/quotes",
        headers=qty_agent_headers,
        json={
            "requested_delivery_date": "2026-11-16",
            "lines": [{"product_id": "464306", "quantity": 60}],
        },
    ).json()
    assert q_qty["policy_preview"]["outcome"] == "APPROVAL_REQUIRED"
    assert [r["rule_id"] for r in q_qty["policy_preview"]["rules"]] == ["R-LINE-QTY"]

    # 3. R-DAILY-SPEND: splitting orders across daily limit (auto=500, daily=600)
    daily_agent_headers, _ = _create_agent_and_headers(
        client,
        user_headers,
        name="Daily Budget Agent",
        auto_approve_limit_eur=500.0,
        max_order_value_eur=5000.0,
        daily_spend_limit_eur=600.0,
    )
    q_d1 = client.post(
        "/api/quotes",
        headers=daily_agent_headers,
        json={
            "requested_delivery_date": "2026-11-16",
            "lines": [{"product_id": "464306", "quantity": 400}],
        },
    ).json()
    o_d1 = client.post(
        "/api/orders",
        headers={**daily_agent_headers, "Idempotency-Key": "daily-1"},
        json={"quote_id": q_d1["id"]},
    )
    assert o_d1.status_code == 201
    assert o_d1.json()["status"] == "PLACED"  # €404 <= €600

    q_d2 = client.post(
        "/api/quotes",
        headers=daily_agent_headers,
        json={
            "requested_delivery_date": "2026-11-16",
            "lines": [{"product_id": "464306", "quantity": 300}],
        },
    ).json()
    assert q_d2["policy_preview"]["outcome"] == "APPROVAL_REQUIRED"
    assert [r["rule_id"] for r in q_d2["policy_preview"]["rules"]] == ["R-DAILY-SPEND"]
    o_d2 = client.post(
        "/api/orders",
        headers={**daily_agent_headers, "Idempotency-Key": "daily-2"},
        json={"quote_id": q_d2["id"]},
    )
    assert o_d2.status_code == 201
    assert o_d2.json()["status"] == "PENDING_APPROVAL"

    # 4. R-DELIVERY-RISK: 500 x 469363 (384 in stock -> PARTIAL / AT_RISK)
    risk_agent_headers, _ = _create_agent_and_headers(
        client,
        user_headers,
        name="High Auto Agent",
        auto_approve_limit_eur=5000.0,
        max_order_value_eur=10000.0,
        daily_spend_limit_eur=10000.0,
    )
    q_risk = client.post(
        "/api/quotes",
        headers=risk_agent_headers,
        json={
            "requested_delivery_date": "2026-11-16",
            "lines": [{"product_id": "469363", "quantity": 500}],
        },
    ).json()
    assert q_risk["overall_delivery_status"] == "AT_RISK"
    assert "PARTIAL" in q_risk["lines"][0]["flags"]
    assert q_risk["policy_preview"]["outcome"] == "APPROVAL_REQUIRED"
    assert [r["rule_id"] for r in q_risk["policy_preview"]["rules"]] == ["R-DELIVERY-RISK"]

    o_risk = client.post(
        "/api/orders",
        headers={**risk_agent_headers, "Idempotency-Key": "risk-1"},
        json={"quote_id": q_risk["id"]},
    ).json()
    assert o_risk["status"] == "PENDING_APPROVAL"

    # Human user can approve despite R-DELIVERY-RISK (approval_override=True)
    app_risk = client.post(
        f"/api/orders/{o_risk['id']}/approve",
        headers={**user_headers, "Idempotency-Key": "risk-approve-1"},
        json={"content_hash": o_risk["content_hash"], "note": "Accept partial delivery"},
    )
    assert app_risk.status_code == 200
    assert app_risk.json()["status"] == "PLACED"


def test_idempotency_replay_conflict_and_missing_header(
    m5_env: tuple[TestClient, sessionmaker[Session], MutablePriceAimoClient],
) -> None:
    client, session_factory, _ = m5_env
    user_headers, _ = _register_user(client)
    agent_headers, _ = _create_agent_and_headers(
        client,
        user_headers,
        auto_approve_limit_eur=500.0,
        max_order_value_eur=5000.0,
        daily_spend_limit_eur=5000.0,
    )

    q1 = client.post(
        "/api/quotes",
        headers=agent_headers,
        json={
            "requested_delivery_date": "2026-11-16",
            "lines": [{"product_id": "464306", "quantity": 100}],
        },
    ).json()
    q2 = client.post(
        "/api/quotes",
        headers=agent_headers,
        json={
            "requested_delivery_date": "2026-11-16",
            "lines": [{"product_id": "464306", "quantity": 1000}],
        },
    ).json()

    # Missing Idempotency-Key on POST /api/orders -> 422 VALIDATION_ERROR
    no_key = client.post("/api/orders", headers=agent_headers, json={"quote_id": q1["id"]})
    assert no_key.status_code == 422
    assert no_key.json()["error"]["code"] == "VALIDATION_ERROR"

    # Double POST with same key + same body -> identical order, only 1 order in DB
    r1 = client.post(
        "/api/orders",
        headers={**agent_headers, "Idempotency-Key": "idem-create-1"},
        json={"quote_id": q1["id"], "note": "first"},
    )
    assert r1.status_code == 201
    r2 = client.post(
        "/api/orders",
        headers={**agent_headers, "Idempotency-Key": "idem-create-1"},
        json={"quote_id": q1["id"], "note": "first"},
    )
    assert r2.status_code == 201
    assert r2.json()["id"] == r1.json()["id"]

    with session_factory() as db:
        count = db.scalar(select(func.count()).select_from(Order))
        assert count == 1

    # Same key + different body -> 409 CONFLICT
    conflict_create = client.post(
        "/api/orders",
        headers={**agent_headers, "Idempotency-Key": "idem-create-1"},
        json={"quote_id": q2["id"], "note": "different"},
    )
    assert conflict_create.status_code == 409
    assert conflict_create.json()["error"]["code"] == "CONFLICT"

    # Create pending order for approve idempotency checks
    o2 = client.post(
        "/api/orders",
        headers={**agent_headers, "Idempotency-Key": "idem-create-2"},
        json={"quote_id": q2["id"]},
    ).json()
    assert o2["status"] == "PENDING_APPROVAL"

    # Missing Idempotency-Key on approve -> 422 VALIDATION_ERROR
    no_key_app = client.post(
        f"/api/orders/{o2['id']}/approve",
        headers=user_headers,
        json={"content_hash": o2["content_hash"]},
    )
    assert no_key_app.status_code == 422
    assert no_key_app.json()["error"]["code"] == "VALIDATION_ERROR"

    # Double approve with same key + same body -> same 200 response
    a1 = client.post(
        f"/api/orders/{o2['id']}/approve",
        headers={**user_headers, "Idempotency-Key": "idem-approve-1"},
        json={"content_hash": o2["content_hash"], "note": "ok"},
    )
    assert a1.status_code == 200
    a2 = client.post(
        f"/api/orders/{o2['id']}/approve",
        headers={**user_headers, "Idempotency-Key": "idem-approve-1"},
        json={"content_hash": o2["content_hash"], "note": "ok"},
    )
    assert a2.status_code == 200
    assert a2.json() == a1.json()

    # Same key + different body on approve -> 409 CONFLICT
    a_conflict = client.post(
        f"/api/orders/{o2['id']}/approve",
        headers={**user_headers, "Idempotency-Key": "idem-approve-1"},
        json={"content_hash": o2["content_hash"], "note": "changed note"},
    )
    assert a_conflict.status_code == 409
    assert a_conflict.json()["error"]["code"] == "CONFLICT"


def test_quote_expiry_used_and_stale_checks(
    m5_env: tuple[TestClient, sessionmaker[Session], MutablePriceAimoClient],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _, aimo_client = m5_env
    user_headers, _ = _register_user(client)
    agent_headers, _ = _create_agent_and_headers(client, user_headers)

    # Create quote at 21:00 on 2026-11-13 -> expires_at = 21:30
    q_exp = client.post(
        "/api/quotes",
        headers=agent_headers,
        json={
            "requested_delivery_date": "2026-11-16",
            "lines": [{"product_id": "464306", "quantity": 10}],
        },
    ).json()
    assert q_exp["expires_at"] == "2026-11-13T21:30:00+02:00"

    # Advance past expiry (within token TTL) -> GET lazy-expires and POST /api/orders -> 409
    monkeypatch.setenv("APP_NOW", "2026-11-13T21:31:00+02:00")
    q_exp_get = client.get(f"/api/quotes/{q_exp['id']}", headers=agent_headers)
    assert q_exp_get.status_code == 200
    assert q_exp_get.json()["status"] == "EXPIRED"

    o_exp = client.post(
        "/api/orders",
        headers={**agent_headers, "Idempotency-Key": "exp-order"},
        json={"quote_id": q_exp["id"]},
    )
    assert o_exp.status_code == 409
    assert o_exp.json()["error"]["code"] == "QUOTE_EXPIRED"

    # Reset time, create fresh quote, bump price -> 409 QUOTE_STALE with fresh lines
    monkeypatch.setenv("APP_NOW", "2026-11-13T21:00:00+02:00")
    q_stale = client.post(
        "/api/quotes",
        headers=agent_headers,
        json={
            "requested_delivery_date": "2026-11-16",
            "lines": [{"product_id": "464306", "quantity": 10}],
        },
    ).json()

    raw_price = json.loads((AIMO_FIXTURE_DIR / "price.json").read_text(encoding="utf-8"))
    item_464306 = next(p for p in raw_price["products"] if p["productId"] == "464306")
    bumped = copy.deepcopy(item_464306)
    bumped["catalogPrice"]["value"] = 1.25
    aimo_client.price_overrides["464306"] = bumped

    o_stale = client.post(
        "/api/orders",
        headers={**agent_headers, "Idempotency-Key": "stale-order-1"},
        json={"quote_id": q_stale["id"]},
    )
    assert o_stale.status_code == 409
    assert o_stale.json()["error"]["code"] == "QUOTE_STALE"
    assert o_stale.json()["error"]["details"]["lines"][0]["unit_price_eur"] == 1.25

    # Restore price -> order succeeds, then re-using same quote -> 409 QUOTE_ALREADY_USED
    aimo_client.price_overrides.clear()
    o_ok = client.post(
        "/api/orders",
        headers={**agent_headers, "Idempotency-Key": "stale-order-2"},
        json={"quote_id": q_stale["id"]},
    )
    assert o_ok.status_code == 201

    o_reuse = client.post(
        "/api/orders",
        headers={**agent_headers, "Idempotency-Key": "reuse-order"},
        json={"quote_id": q_stale["id"]},
    )
    assert o_reuse.status_code == 409
    assert o_reuse.json()["error"]["code"] == "QUOTE_ALREADY_USED"

    # Stale price during human approval -> 409 QUOTE_STALE
    q_pend = client.post(
        "/api/quotes",
        headers=agent_headers,
        json={
            "requested_delivery_date": "2026-11-16",
            "lines": [{"product_id": "464306", "quantity": 1000}],
        },
    ).json()
    o_pend = client.post(
        "/api/orders",
        headers={**agent_headers, "Idempotency-Key": "pend-stale"},
        json={"quote_id": q_pend["id"]},
    ).json()
    assert o_pend["status"] == "PENDING_APPROVAL"

    # Wrong content_hash -> 409 APPROVAL_CONTENT_CHANGED
    bad_hash = client.post(
        f"/api/orders/{o_pend['id']}/approve",
        headers={**user_headers, "Idempotency-Key": "bad-hash-key"},
        json={"content_hash": "deadbeef" * 8},
    )
    assert bad_hash.status_code == 409
    assert bad_hash.json()["error"]["code"] == "APPROVAL_CONTENT_CHANGED"

    # Bump price before approve -> 409 QUOTE_STALE
    aimo_client.price_overrides["464306"] = bumped
    stale_app = client.post(
        f"/api/orders/{o_pend['id']}/approve",
        headers={**user_headers, "Idempotency-Key": "stale-app-key"},
        json={"content_hash": o_pend["content_hash"]},
    )
    assert stale_app.status_code == 409
    assert stale_app.json()["error"]["code"] == "QUOTE_STALE"
    assert stale_app.json()["error"]["details"]["lines"][0]["unit_price_eur"] == 1.25

    # Closing time capping: at 16:50 before 17:00 cut-off (re-login user at new clock)
    monkeypatch.setenv("APP_NOW", "2026-11-15T16:50:00+02:00")
    login_late = client.post(
        "/api/auth/login",
        json={"email": "chef@aurora.test", "password": "password123"},
    )
    assert login_late.status_code == 200
    late_headers = {"Authorization": f"Bearer {login_late.json()['token']}"}
    q_cap = client.post(
        "/api/quotes",
        headers=late_headers,
        json={
            "requested_delivery_date": "2026-11-16",
            "lines": [{"product_id": "464306", "quantity": 10}],
        },
    )
    assert q_cap.status_code == 201
    assert q_cap.json()["expires_at"] == "2026-11-15T17:00:00+02:00"


def test_reject_cancel_pending_expiry_dashboard_audit_and_isolation(
    m5_env: tuple[TestClient, sessionmaker[Session], MutablePriceAimoClient],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _, _ = m5_env
    user_a_headers, _ = _register_user(
        client, company_name="Company A", user_name="User A", email="a@compa.test"
    )
    user_b_headers, _ = _register_user(
        client, company_name="Company B", user_name="User B", email="b@compb.test"
    )
    agent_a1_headers, agent_a1 = _create_agent_and_headers(client, user_a_headers, name="Agent A1")
    agent_a2_headers, _ = _create_agent_and_headers(client, user_a_headers, name="Agent A2")

    # Order 1: PLACED automatically (300 x 464306 = €303.00)
    q_auto = client.post(
        "/api/quotes",
        headers=agent_a1_headers,
        json={
            "requested_delivery_date": "2026-11-16",
            "lines": [{"product_id": "464306", "quantity": 300}],
        },
    ).json()
    o_placed = client.post(
        "/api/orders",
        headers={**agent_a1_headers, "Idempotency-Key": "iso-1"},
        json={"quote_id": q_auto["id"]},
    ).json()
    assert o_placed["status"] == "PLACED"

    # Order 2: PENDING_APPROVAL -> rejected by user
    q_rej = client.post(
        "/api/quotes",
        headers=agent_a1_headers,
        json={
            "requested_delivery_date": "2026-11-16",
            "lines": [{"product_id": "464306", "quantity": 1000}],
        },
    ).json()
    o_rej = client.post(
        "/api/orders",
        headers={**agent_a1_headers, "Idempotency-Key": "iso-2"},
        json={"quote_id": q_rej["id"]},
    ).json()
    rej_resp = client.post(
        f"/api/orders/{o_rej['id']}/reject",
        headers=user_a_headers,
        json={"note": "Too much milk"},
    )
    assert rej_resp.status_code == 200
    assert rej_resp.json()["status"] == "REJECTED"
    assert rej_resp.json()["decision_note"] == "Too much milk"

    # Cannot approve/cancel a REJECTED order -> 409 INVALID_STATE
    app_after_rej = client.post(
        f"/api/orders/{o_rej['id']}/approve",
        headers={**user_a_headers, "Idempotency-Key": "app-after-rej"},
        json={"content_hash": o_rej["content_hash"]},
    )
    assert app_after_rej.status_code == 409
    assert app_after_rej.json()["error"]["code"] == "INVALID_STATE"

    # Order 3: PENDING_APPROVAL -> cancelled by owning agent A1
    q_can = client.post(
        "/api/quotes",
        headers=agent_a1_headers,
        json={
            "requested_delivery_date": "2026-11-16",
            "lines": [{"product_id": "464306", "quantity": 1000}],
        },
    ).json()
    o_can = client.post(
        "/api/orders",
        headers={**agent_a1_headers, "Idempotency-Key": "iso-3"},
        json={"quote_id": q_can["id"]},
    ).json()
    # Agent A2 in SAME company cannot read or cancel Agent A1's order -> 404 NOT_FOUND
    assert client.get(f"/api/orders/{o_can['id']}", headers=agent_a2_headers).status_code == 404
    assert (
        client.post(f"/api/orders/{o_can['id']}/cancel", headers=agent_a2_headers).status_code
        == 404
    )

    can_resp = client.post(
        f"/api/orders/{o_can['id']}/cancel",
        headers=agent_a1_headers,
        json={"note": "No longer needed"},
    )
    assert can_resp.status_code == 200
    assert can_resp.json()["status"] == "CANCELLED"

    # Order 4: PENDING_APPROVAL stays pending for dashboard check
    q_stay = client.post(
        "/api/quotes",
        headers=agent_a2_headers,
        json={
            "requested_delivery_date": "2026-11-16",
            "lines": [{"product_id": "464306", "quantity": 1000}],
        },
    ).json()
    o_stay = client.post(
        "/api/orders",
        headers={**agent_a2_headers, "Idempotency-Key": "iso-4"},
        json={"quote_id": q_stay["id"]},
    ).json()
    assert o_stay["status"] == "PENDING_APPROVAL"

    # Agent A1 lists orders -> sees only its own 3 orders, not A2's
    list_a1 = client.get("/api/orders", headers=agent_a1_headers)
    assert list_a1.status_code == 200
    assert list_a1.json()["total"] == 3
    assert all(item["created_by"]["id"] == agent_a1["id"] for item in list_a1.json()["items"])

    # Company B user cannot read Company A's quote or order -> 404 NOT_FOUND
    assert client.get(f"/api/quotes/{q_auto['id']}", headers=user_b_headers).status_code == 404
    assert client.get(f"/api/orders/{o_stay['id']}", headers=user_b_headers).status_code == 404
    assert (
        client.post(
            f"/api/orders/{o_stay['id']}/approve",
            headers={**user_b_headers, "Idempotency-Key": "cross-app"},
            json={"content_hash": o_stay["content_hash"]},
        ).status_code
        == 404
    )

    # Dashboard KPIs (user only)
    assert client.get("/api/dashboard", headers=agent_a1_headers).status_code == 403
    dash = client.get("/api/dashboard", headers=user_a_headers)
    assert dash.status_code == 200, dash.text
    dash_data = dash.json()
    assert dash_data["pending_approvals"] == 1
    assert dash_data["orders_today"] == 1
    assert dash_data["spend_today_eur"] == 303.0
    assert dash_data["spend_7d_eur"] == 303.0
    assert dash_data["co2_7d_kg"] > 0
    assert dash_data["active_agents"] == 2
    assert len(dash_data["recent_orders"]) == 4

    # Audit query filters (user only)
    assert client.get("/api/audit", headers=agent_a1_headers).status_code == 403
    audit_ord = client.get(
        f"/api/audit?entity_type=order&entity_id={o_placed['id']}",
        headers=user_a_headers,
    )
    assert audit_ord.status_code == 200
    assert audit_ord.json()["total"] == 2  # order.created + order.placed

    audit_actor = client.get(
        f"/api/audit?actor_id={agent_a1['id']}",
        headers=user_a_headers,
    )
    assert audit_actor.status_code == 200
    assert audit_actor.json()["total"] >= 5

    # Pending approval expiration when time advances past approval_expires_at (2026-11-15T17:00)
    monkeypatch.setenv("APP_NOW", "2026-11-15T17:05:00+02:00")
    login_a_late = client.post(
        "/api/auth/login",
        json={"email": "a@compa.test", "password": "password123"},
    )
    assert login_a_late.status_code == 200
    user_a_late_headers = {"Authorization": f"Bearer {login_a_late.json()['token']}"}

    o_stay_get = client.get(f"/api/orders/{o_stay['id']}", headers=user_a_late_headers)
    assert o_stay_get.status_code == 200
    assert o_stay_get.json()["status"] == "EXPIRED"

    dash_after_exp = client.get("/api/dashboard", headers=user_a_late_headers).json()
    assert dash_after_exp["pending_approvals"] == 0
