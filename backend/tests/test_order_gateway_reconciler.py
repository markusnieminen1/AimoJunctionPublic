"""Tests for Milestone M5b: Order Gateway, Reconciler, Dev Toggle, and OpenAPI Export."""

from __future__ import annotations

import json
from collections.abc import Generator
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app import clock
from app import db as app_db
from app.aimo.client import AimoClient, get_aimo_client
from app.config import settings
from app.db import get_db, init_db, make_engine
from app.main import create_app
from app.models import GatewaySubmission, Order, OrderEventModel, OrderLineModel, Quote
from app.services.order_gateway import (
    MockAimoOrderGateway,
    get_gateway_simulate_mode,
    set_gateway_simulate_mode,
)
from app.services.orders import reconcile_orders
from scripts.seed_catalog import (
    DEFAULT_CATEGORIES_PATH,
    DEFAULT_CO2_PATH,
    DEFAULT_RELIABILITY_PATH,
    seed_catalog,
)

FIXTURE_PRODUCTS_PATH = Path(__file__).resolve().parent / "fixtures" / "products_sample.json"
AIMO_FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "aimo"
OPENAPI_PATH = Path(__file__).resolve().parent.parent / "openapi.json"


@pytest.fixture()
def m5b_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Generator[tuple[TestClient, sessionmaker[Session]], None, None]:
    """Isolated SQLite DB seeded with sample products + fixture AimoClient + dev mode reset."""
    monkeypatch.setenv("APP_NOW", "2026-11-13T21:00:00+02:00")
    monkeypatch.setattr(settings, "jwt_secret", "test-secret-key-with-at-least-32-bytes")
    monkeypatch.setattr(settings, "aimo_mode", "fixture")
    monkeypatch.setattr(settings, "app_env", "dev")
    monkeypatch.setattr(settings, "order_gateway_simulate", "none")
    set_gateway_simulate_mode(None)

    db_file = tmp_path / "m5b_test.db"
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

    client = TestClient(test_app)
    try:
        yield client, session_factory
    finally:
        set_gateway_simulate_mode(None)
        test_app.dependency_overrides.clear()
        test_engine.dispose()


def _register_and_create_agent(
    client: TestClient,
    *,
    auto_approve_limit_eur: float = 500.0,
) -> tuple[dict[str, str], dict[str, str]]:
    reg = client.post(
        "/api/auth/register",
        json={
            "company_name": "Hotel Aurora Oy",
            "user_name": "Chef",
            "email": "chef@aurora.test",
            "password": "password123",
        },
    )
    assert reg.status_code == 201, reg.text
    user_headers = {"Authorization": f"Bearer {reg.json()['token']}"}

    ag = client.post(
        "/api/agents",
        headers=user_headers,
        json={
            "name": "Kitchen Reorder Bot",
            "auto_approve_limit_eur": auto_approve_limit_eur,
            "max_order_value_eur": 5000.0,
            "daily_spend_limit_eur": 5000.0,
            "can_place_orders": True,
        },
    )
    assert ag.status_code == 201, ag.text
    created = ag.json()
    tok = client.post(
        "/api/agent-auth/token",
        json={"agent_id": created["agent"]["id"], "agent_secret": created["agent_secret"]},
    )
    assert tok.status_code == 200, tok.text
    agent_headers = {"Authorization": f"Bearer {tok.json()['token']}"}
    return user_headers, agent_headers


def _create_quote(client: TestClient, headers: dict[str, str], quantity: float = 100) -> dict:
    resp = client.post(
        "/api/quotes",
        headers=headers,
        json={
            "requested_delivery_date": "2026-11-16",
            "lines": [{"product_id": "464306", "quantity": quantity}],
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_timeout_after_accept_reconciles_to_placed_with_single_submission(
    m5b_env: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, session_factory = m5b_env
    user_headers, agent_headers = _register_and_create_agent(client)

    # Toggle timeout_after_accept via dev endpoint
    mode_resp = client.post(
        "/api/dev/gateway-mode",
        headers=user_headers,
        json={"mode": "timeout_after_accept"},
    )
    assert mode_resp.status_code == 200
    assert mode_resp.json() == {"mode": "timeout_after_accept"}

    quote = _create_quote(client, agent_headers, quantity=100)
    ord_resp = client.post(
        "/api/orders",
        headers={**agent_headers, "Idempotency-Key": "timeout-order-1"},
        json={"quote_id": quote["id"]},
    )
    assert ord_resp.status_code == 201, ord_resp.text
    order = ord_resp.json()
    assert order["status"] == "SUBMISSION_UNKNOWN"
    assert order["gateway_reference"] is None
    assert [ev["type"] for ev in order["events"]] == ["order.created", "order.submission_unknown"]

    # Upstream submission was persisted once despite simulated timeout
    with session_factory() as db:
        sub_count = db.scalar(select(func.count()).select_from(GatewaySubmission))
        assert sub_count == 1
        summary = reconcile_orders(db)
        assert summary == {"checked": 1, "placed": 1, "failed": 0, "unknown": 0}
        sub_count_after = db.scalar(select(func.count()).select_from(GatewaySubmission))
        assert sub_count_after == 1

    # Order is now PLACED with the exact single gateway submission reference
    fetched = client.get(f"/api/orders/{order['id']}", headers=agent_headers)
    assert fetched.status_code == 200
    placed = fetched.json()
    expected_ref = f"aimo_sim_{order['id'][-12:]}"
    assert placed["status"] == "PLACED"
    assert placed["gateway_reference"] == expected_ref
    assert placed["events"][-1]["type"] == "order.placed"
    assert "Reconciler confirmed existing gateway submission" in placed["events"][-1]["message"]

    # Direct resubmit on MockAimoOrderGateway is also strictly idempotent
    with session_factory() as db:
        gw = MockAimoOrderGateway()
        res2 = gw.submit(db, order["id"], {"order_id": order["id"]})
        assert res2.status == "ok"
        assert res2.reference == expected_ref
        assert db.scalar(select(func.count()).select_from(GatewaySubmission)) == 1


def test_gateway_error_transitions_to_failed_without_submission(
    m5b_env: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, session_factory = m5b_env
    user_headers, agent_headers = _register_and_create_agent(client)

    client.post(
        "/api/dev/gateway-mode",
        headers=user_headers,
        json={"mode": "error"},
    )

    quote = _create_quote(client, agent_headers, quantity=100)
    ord_resp = client.post(
        "/api/orders",
        headers={**agent_headers, "Idempotency-Key": "err-order-1"},
        json={"quote_id": quote["id"]},
    )
    assert ord_resp.status_code == 201, ord_resp.text
    order = ord_resp.json()
    assert order["status"] == "FAILED"
    assert order["gateway_reference"] is None
    assert [ev["type"] for ev in order["events"]] == ["order.created", "order.failed"]

    with session_factory() as db:
        assert db.scalar(select(func.count()).select_from(GatewaySubmission)) == 0
        # Terminal FAILED orders are not touched by reconciler
        assert reconcile_orders(db) == {"checked": 0, "placed": 0, "failed": 0, "unknown": 0}


def test_crash_mid_submitting_recovered_on_startup_and_max_attempts_exhausted(
    m5b_env: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, session_factory = m5b_env
    user_headers, agent_headers = _register_and_create_agent(client)

    # Create a baseline placed order and clone/seed a crashed SUBMITTING order with no submission
    q1 = _create_quote(client, agent_headers, quantity=100)
    placed_1 = client.post(
        "/api/orders",
        headers={**agent_headers, "Idempotency-Key": "seed-order-1"},
        json={"quote_id": q1["id"]},
    ).json()
    assert placed_1["status"] == "PLACED"

    now = clock.now()
    with session_factory() as db:
        base_order = db.get(Order, placed_1["id"])
        assert base_order is not None
        base_quote = db.get(Quote, q1["id"])
        assert base_quote is not None

        crashed_id = "ord_01CRASHEDSUBMITTING01"
        crashed = Order(
            id=crashed_id,
            company_id=base_order.company_id,
            quote_id=base_quote.id,
            status="SUBMITTING",
            created_at=now,
            requested_delivery_date=base_order.requested_delivery_date,
            total_excl_vat_eur=base_order.total_excl_vat_eur,
            total_incl_vat_eur=base_order.total_incl_vat_eur,
            total_co2_kg=base_order.total_co2_kg,
            policy=base_order.policy,
            created_by_kind=base_order.created_by_kind,
            created_by_id=base_order.created_by_id,
            created_by_name=base_order.created_by_name,
            delegated_by_id=base_order.delegated_by_id,
            delegated_by_name=base_order.delegated_by_name,
            content_hash=base_order.content_hash,
            gateway_reference=None,
            gateway_attempts=1,
            updated_at=now,
        )
        crashed.lines.append(
            OrderLineModel(
                order_id=crashed_id,
                line_index=0,
                product_id="464306",
                product_name="Milk",
                quantity=100,
                sales_unit="ST",
                unit_price_eur=1.01,
                line_total_eur=101.0,
                tax_rate=14.0,
                delivery_status="ON_TIME",
                flags=[],
            )
        )
        crashed.events.append(
            OrderEventModel(
                id="evt_01CRASHED01",
                order_id=crashed_id,
                at=now,
                type="order.created",
                actor=None,
                message="Order created before simulated crash",
                request_id="req_crash_1",
            )
        )
        db.add(crashed)

        # Also create an exhausted order (gateway_attempts=3, no submission in gateway_submissions)
        exhausted_id = "ord_01EXHAUSTEDORDER0003"
        exhausted = Order(
            id=exhausted_id,
            company_id=base_order.company_id,
            quote_id=base_quote.id,
            status="SUBMISSION_UNKNOWN",
            created_at=now - timedelta(minutes=5),
            requested_delivery_date=base_order.requested_delivery_date,
            total_excl_vat_eur=base_order.total_excl_vat_eur,
            total_incl_vat_eur=base_order.total_incl_vat_eur,
            total_co2_kg=base_order.total_co2_kg,
            policy=base_order.policy,
            created_by_kind=base_order.created_by_kind,
            created_by_id=base_order.created_by_id,
            created_by_name=base_order.created_by_name,
            delegated_by_id=base_order.delegated_by_id,
            delegated_by_name=base_order.delegated_by_name,
            content_hash=base_order.content_hash,
            gateway_reference=None,
            gateway_attempts=3,
            updated_at=now - timedelta(minutes=2),
        )
        exhausted.lines.append(
            OrderLineModel(
                order_id=exhausted_id,
                line_index=0,
                product_id="464306",
                product_name="Milk",
                quantity=100,
                sales_unit="ST",
                unit_price_eur=1.01,
                line_total_eur=101.0,
                tax_rate=14.0,
                delivery_status="ON_TIME",
                flags=[],
            )
        )
        db.add(exhausted)
        db.commit()

        # Regular tick (recover_all_submitting=False) skips fresh SUBMITTING (< 60s),
        # while failing exhausted SUBMISSION_UNKNOWN.
        reg_summary = reconcile_orders(db, recover_all_submitting=False)
        assert reg_summary == {"checked": 1, "placed": 0, "failed": 1, "unknown": 0}
        db.refresh(crashed)
        db.refresh(exhausted)
        assert crashed.status == "SUBMITTING"
        assert exhausted.status == "FAILED"
        assert any("needs human attention" in ev.message for ev in exhausted.events)

        # Startup recovery (recover_all_submitting=True) immediately recovers crashed SUBMITTING
        startup_summary = reconcile_orders(db, recover_all_submitting=True)
        assert startup_summary == {"checked": 1, "placed": 1, "failed": 0, "unknown": 0}
        db.refresh(crashed)
        assert crashed.status == "PLACED"
        assert crashed.gateway_reference == f"aimo_sim_{crashed_id[-12:]}"
        assert crashed.gateway_attempts == 2
        assert db.get(GatewaySubmission, crashed_id) is not None


def test_lifespan_startup_recovery_and_clean_shutdown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """App lifespan runs startup recovery & expiry and shuts down the background task cleanly."""
    monkeypatch.setenv("APP_NOW", "2026-11-13T21:00:00+02:00")
    monkeypatch.setattr(settings, "jwt_secret", "test-secret-key-with-at-least-32-bytes")
    monkeypatch.setattr(settings, "aimo_mode", "fixture")
    monkeypatch.setattr(settings, "app_env", "dev")
    set_gateway_simulate_mode("none")

    db_file = tmp_path / "lifespan_test.db"
    test_engine = make_engine(f"sqlite:///{db_file}")
    session_factory = sessionmaker(
        bind=test_engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )
    monkeypatch.setattr(app_db, "engine", test_engine)
    monkeypatch.setattr(app_db, "SessionLocal", session_factory)

    init_db(target_engine=test_engine)
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

    # Seed a crashed SUBMITTING order before lifespan starts
    raw_client = TestClient(test_app)
    _, agent_headers = _register_and_create_agent(raw_client)
    q_seed = _create_quote(raw_client, agent_headers, quantity=50)
    seed_ord = raw_client.post(
        "/api/orders",
        headers={**agent_headers, "Idempotency-Key": "lifespan-seed-1"},
        json={"quote_id": q_seed["id"]},
    ).json()

    now = clock.now()
    crashed_id = "ord_01LIFESPANCRASH0001"
    with session_factory() as db:
        base_order = db.get(Order, seed_ord["id"])
        assert base_order is not None
        crashed = Order(
            id=crashed_id,
            company_id=base_order.company_id,
            quote_id=base_order.quote_id,
            status="SUBMITTING",
            created_at=now,
            requested_delivery_date=base_order.requested_delivery_date,
            total_excl_vat_eur=base_order.total_excl_vat_eur,
            total_incl_vat_eur=base_order.total_incl_vat_eur,
            total_co2_kg=base_order.total_co2_kg,
            policy=base_order.policy,
            created_by_kind=base_order.created_by_kind,
            created_by_id=base_order.created_by_id,
            created_by_name=base_order.created_by_name,
            delegated_by_id=base_order.delegated_by_id,
            delegated_by_name=base_order.delegated_by_name,
            content_hash=base_order.content_hash,
            gateway_reference=None,
            gateway_attempts=1,
            updated_at=now,
        )
        crashed.lines.append(
            OrderLineModel(
                order_id=crashed_id,
                line_index=0,
                product_id="464306",
                product_name="Milk",
                quantity=50,
                sales_unit="ST",
                unit_price_eur=1.01,
                line_total_eur=50.5,
                tax_rate=14.0,
                delivery_status="ON_TIME",
                flags=[],
            )
        )
        db.add(crashed)
        db.commit()

    try:
        with TestClient(test_app) as client:
            resp = client.get("/api/health")
            assert resp.status_code == 200
            recovered = client.get(f"/api/orders/{crashed_id}", headers=agent_headers)
            assert recovered.status_code == 200
            assert recovered.json()["status"] == "PLACED"
            assert recovered.json()["gateway_reference"] == f"aimo_sim_{crashed_id[-12:]}"
    finally:
        set_gateway_simulate_mode(None)
        test_app.dependency_overrides.clear()
        test_engine.dispose()


def test_dev_gateway_mode_endpoint_auth_and_env_guard(
    m5b_env: tuple[TestClient, sessionmaker[Session]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _ = m5b_env
    user_headers, agent_headers = _register_and_create_agent(client)

    # Agent is forbidden -> 403 FORBIDDEN
    ag_resp = client.post(
        "/api/dev/gateway-mode",
        headers=agent_headers,
        json={"mode": "error"},
    )
    assert ag_resp.status_code == 403
    assert ag_resp.json()["error"]["code"] == "FORBIDDEN"

    # Invalid mode -> 422 VALIDATION_ERROR
    bad_resp = client.post(
        "/api/dev/gateway-mode",
        headers=user_headers,
        json={"mode": "bogus"},
    )
    assert bad_resp.status_code == 422
    assert bad_resp.json()["error"]["code"] == "VALIDATION_ERROR"

    # Valid mode -> 200 and updates runtime mode
    ok_resp = client.post(
        "/api/dev/gateway-mode",
        headers=user_headers,
        json={"mode": "timeout_after_accept"},
    )
    assert ok_resp.status_code == 200
    assert ok_resp.json() == {"mode": "timeout_after_accept"}
    assert get_gateway_simulate_mode() == "timeout_after_accept"

    # When app_env != "dev", endpoint is not mounted
    monkeypatch.setattr(settings, "app_env", "prod")
    prod_app = create_app()
    prod_client = TestClient(prod_app)
    prod_resp = prod_client.post(
        "/api/dev/gateway-mode",
        headers=user_headers,
        json={"mode": "none"},
    )
    assert prod_resp.status_code == 404


def test_exported_openapi_json_is_valid_3_1() -> None:
    assert OPENAPI_PATH.exists(), f"Missing exported OpenAPI spec at {OPENAPI_PATH}"
    spec = json.loads(OPENAPI_PATH.read_text(encoding="utf-8"))
    assert spec["openapi"].startswith("3.1")
    paths = spec["paths"]
    for expected_path in (
        "/api/health",
        "/api/orders",
        "/api/orders/{order_id}/approve",
        "/api/quotes",
        "/api/products",
        "/api/products/check",
        "/api/products/substitutes",
        "/api/dashboard",
        "/api/audit",
        "/api/dev/gateway-mode",
    ):
        assert expected_path in paths, f"{expected_path} missing from openapi.json"
