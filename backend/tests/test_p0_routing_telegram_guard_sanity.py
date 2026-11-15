"""Tests for Priority #0 Batch A: Request Router, Telegram Opt-In, Recipe Sanity, Guard & 3-Gate."""

from __future__ import annotations

import ast
from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.aimo.client import AimoClient, get_aimo_client
from app.config import settings
from app.db import get_db, init_db, make_engine
from app.llm.decisions import FakeDecisionProvider
from app.main import create_app
from app.models import Agent, AuditEventModel, UserTelegramSetting
from scripts.demo_seed import seed_demo
from scripts.eval_gemma_verifier import run_benchmark
from scripts.seed_catalog import (
    DEFAULT_CATEGORIES_PATH,
    DEFAULT_CO2_PATH,
    DEFAULT_RELIABILITY_PATH,
    seed_catalog,
)

FIXTURE_PRODUCTS_PATH = Path(__file__).resolve().parent / "fixtures" / "products_sample.json"
AIMO_FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "aimo"
AGENTIC_DIR = Path(__file__).resolve().parent.parent / "app" / "agentic_service"


@pytest.fixture()
def p0_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Generator[dict[str, object], None, None]:
    """Isolated SQLite DB + TestClient with seeded catalog, Hotel Aurora + extra personas."""
    monkeypatch.setenv("APP_NOW", "2026-11-13T21:00:00+02:00")
    monkeypatch.setattr(settings, "app_env", "dev")
    monkeypatch.setattr(settings, "jwt_secret", "test-secret-key-with-at-least-32-bytes")
    monkeypatch.setattr(settings, "aimo_mode", "fixture")
    monkeypatch.setattr(settings, "llm_provider", "fake")
    monkeypatch.setattr(settings, "fast_llm_provider", "fake")
    monkeypatch.setattr(settings, "order_gateway_simulate", "none")
    monkeypatch.setattr(settings, "agentic_service_url", None)

    db_file = tmp_path / "p0_batch_a.db"
    test_engine = make_engine(f"sqlite:///{db_file}")
    init_db(target_engine=test_engine)
    session_factory = sessionmaker(
        bind=test_engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )

    creds_path = tmp_path / ".demo_credentials"
    with session_factory() as db:
        seed_catalog(
            db,
            products_path=FIXTURE_PRODUCTS_PATH,
            co2_csv_path=DEFAULT_CO2_PATH,
            reliability_csv_path=DEFAULT_RELIABILITY_PATH,
            category_mapping_path=DEFAULT_CATEGORIES_PATH,
            batch_size=25,
        )
        creds = seed_demo(db, credentials_path=creds_path, with_extra_personas=True)

    test_app = create_app()
    local_aimo = AimoClient(mode="fixture", fixture_dir=AIMO_FIXTURE_DIR)

    def _override_get_db() -> Generator[Session, None, None]:
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    test_app.dependency_overrides[get_db] = _override_get_db
    test_app.dependency_overrides[get_aimo_client] = lambda: local_aimo

    client = TestClient(test_app)
    login_resp = client.post(
        "/api/auth/login",
        json={"email": "demo@aimoannos.test", "password": "demo1234"},
    )
    assert login_resp.status_code == 200
    user_token = login_resp.json()["token"]

    mikko_login = client.post(
        "/api/auth/login",
        json={"email": "mikko@bistrosatama.test", "password": "demo1234"},
    )
    assert mikko_login.status_code == 200
    other_user_token = mikko_login.json()["token"]

    kitchen_info = creds["agents"]["kitchen_agent"]
    ag_resp = client.post(
        "/api/agent-auth/token",
        json={"agent_id": kitchen_info["id"], "agent_secret": kitchen_info["agent_secret"]},
    )
    assert ag_resp.status_code == 200
    agent_token = ag_resp.json()["token"]

    try:
        yield {
            "client": client,
            "session_factory": session_factory,
            "creds": creds,
            "user_headers": {"Authorization": f"Bearer {user_token}"},
            "other_headers": {"Authorization": f"Bearer {other_user_token}"},
            "agent_headers": {"Authorization": f"Bearer {agent_token}"},
            "kitchen_agent_id": kitchen_info["id"],
        }
    finally:
        test_app.dependency_overrides.clear()
        test_engine.dispose()


def test_agentic_service_zero_db_imports_ast_invariant() -> None:
    """Verify app/agentic_service/*.py contains zero sqlalchemy, app.models, or app.db imports."""
    forbidden_prefixes = ("sqlalchemy", "app.models", "app.db")
    py_files = list(AGENTIC_DIR.glob("*.py"))
    assert len(py_files) >= 3

    for py_file in py_files:
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.startswith(forbidden_prefixes), (
                        f"Forbidden DB/ORM import '{alias.name}' in {py_file.name}"
                    )
            elif isinstance(node, ast.ImportFrom) and node.module:
                assert not node.module.startswith(forbidden_prefixes), (
                    f"Forbidden DB/ORM import '{node.module}' in {py_file.name}"
                )


def test_telegram_opt_in_zero_seeded_rbac_validation_and_isolation(
    p0_env: dict[str, object],
) -> None:
    """Test zero seeded Telegram IDs, 403 for agents, group ID 422, verify-chat & revoke."""
    client: TestClient = p0_env["client"]  # type: ignore[assignment]
    session_factory: sessionmaker[Session] = p0_env["session_factory"]  # type: ignore[assignment]
    user_headers: dict[str, str] = p0_env["user_headers"]  # type: ignore[assignment]
    other_headers: dict[str, str] = p0_env["other_headers"]  # type: ignore[assignment]
    agent_headers: dict[str, str] = p0_env["agent_headers"]  # type: ignore[assignment]

    # 1. Zero pre-seeded Telegram rows even with extra personas seeded
    with session_factory() as db:
        assert db.scalar(select(func.count()).select_from(UserTelegramSetting)) == 0

    init_get = client.get("/api/auth/telegram", headers=user_headers)
    assert init_get.status_code == 200
    assert init_get.json()["enabled"] is False
    assert init_get.json()["telegram_username"] is None
    assert init_get.json()["bot_username"] == "your_aimo_bot"

    init_recips = client.get("/api/auth/telegram/recipients", headers=user_headers)
    assert init_recips.status_code == 200
    assert init_recips.json()["items"] == []

    # 2. Agents strictly receive 403 FORBIDDEN on all /api/auth/telegram* endpoints
    assert client.get("/api/auth/telegram", headers=agent_headers).status_code == 403
    assert (
        client.put(
            "/api/auth/telegram",
            headers=agent_headers,
            json={"telegram_id": "@bot_attempt", "enabled": True, "consent_confirmed": True},
        ).status_code
        == 403
    )
    assert client.delete("/api/auth/telegram", headers=agent_headers).status_code == 403
    assert client.get("/api/auth/telegram/recipients", headers=agent_headers).status_code == 403
    assert (
        client.post(
            "/api/auth/telegram/verify-chat",
            headers=agent_headers,
            json={
                "telegram_user_id": "12345678",
                "telegram_username": "anna_aurora",
                "telegram_chat_id": "12345678",
            },
        ).status_code
        == 403
    )

    # 3. Negative group chat IDs and unconfirmed consent rejected with 422
    neg_resp = client.put(
        "/api/auth/telegram",
        headers=user_headers,
        json={"telegram_id": "-100987654321", "enabled": True, "consent_confirmed": True},
    )
    assert neg_resp.status_code == 422
    assert neg_resp.json()["error"]["code"] == "VALIDATION_ERROR"

    no_consent = client.put(
        "/api/auth/telegram",
        headers=user_headers,
        json={"telegram_id": "@anna_aurora", "enabled": True, "consent_confirmed": False},
    )
    assert no_consent.status_code == 422

    # 4. Explicit opt-in + verify-chat binding + company isolation + revoke
    opt_in = client.put(
        "/api/auth/telegram",
        headers=user_headers,
        json={"telegram_id": "@anna_aurora", "enabled": True, "consent_confirmed": True},
    )
    assert opt_in.status_code == 200
    assert opt_in.json()["enabled"] is True
    assert opt_in.json()["telegram_username"] == "anna_aurora"

    # Cross-company cannot see or verify Anna's Telegram opt-in
    assert client.get("/api/auth/telegram/recipients", headers=other_headers).json()["items"] == []
    assert (
        client.post(
            "/api/auth/telegram/verify-chat",
            headers=other_headers,
            json={
                "telegram_user_id": "87654321",
                "telegram_username": "anna_aurora",
                "telegram_chat_id": "87654321",
            },
        ).status_code
        == 404
    )

    # Same-company verify-chat binds private numeric chat_id
    verified = client.post(
        "/api/auth/telegram/verify-chat",
        headers=user_headers,
        json={
            "telegram_user_id": "87654321",
            "telegram_username": "@anna_aurora",
            "telegram_chat_id": "87654321",
        },
    )
    assert verified.status_code == 200
    assert verified.json()["telegram_chat_id"] == "87654321"
    assert verified.json()["enabled"] is True

    # Delete revokes consent and subsequent verify-chat does NOT re-enable
    revoked = client.delete("/api/auth/telegram", headers=user_headers)
    assert revoked.status_code == 200
    assert revoked.json()["enabled"] is False
    assert revoked.json()["telegram_chat_id"] is None
    assert (
        client.post(
            "/api/auth/telegram/verify-chat",
            headers=user_headers,
            json={
                "telegram_user_id": "87654321",
                "telegram_username": "anna_aurora",
                "telegram_chat_id": "87654321",
            },
        ).status_code
        == 404
    )


def test_request_field_router_and_agentic_service_end_to_end(
    p0_env: dict[str, object],
) -> None:
    """Test Core API vs Agentic Service routing, X-Aimo-Route headers, idempotency & loop guard."""
    client: TestClient = p0_env["client"]  # type: ignore[assignment]
    session_factory: sessionmaker[Session] = p0_env["session_factory"]  # type: ignore[assignment]
    agent_headers: dict[str, str] = p0_env["agent_headers"]  # type: ignore[assignment]

    # 1. Governance routing boundary descriptor + /api/routing/inspect
    desc = client.get("/api/governance/routing", headers=agent_headers)
    assert desc.status_code == 200
    assert desc.headers["X-Aimo-Route"] == "core-api"
    assert "prompt" in desc.json()["agentic_fields"]
    assert "quote_id" in desc.json()["structured_fields"]

    insp_core = client.post(
        "/api/routing/inspect",
        headers=agent_headers,
        json={"method": "POST", "path": "/api/orders", "payload": {"quote_id": "qte_123"}},
    )
    assert insp_core.status_code == 200
    assert insp_core.json()["target_service"] == "core-api"

    insp_agentic = client.post(
        "/api/routing/inspect",
        headers=agent_headers,
        json={
            "method": "POST",
            "path": "/api/orders",
            "payload": {"prompt": "Order 5 L lactose-free milk for Monday"},
        },
    )
    assert insp_agentic.status_code == 200
    assert insp_agentic.json()["target_service"] == "agentic-service"
    assert "prompt" in insp_agentic.json()["matched_fields"]

    # 2. Structured quote + order goes directly to core-api
    q_resp = client.post(
        "/api/quotes",
        headers=agent_headers,
        json={
            "requested_delivery_date": "2026-11-16",
            "lines": [{"product_id": "464306", "quantity": 5}],
        },
    )
    assert q_resp.status_code == 201
    assert q_resp.headers["X-Aimo-Route"] == "core-api"

    o_core = client.post(
        "/api/orders",
        headers={**agent_headers, "Idempotency-Key": "p0-core-1"},
        json={"quote_id": q_resp.json()["id"]},
    )
    assert o_core.status_code == 201
    assert o_core.headers["X-Aimo-Route"] == "core-api"
    core_order_id = o_core.json()["id"]

    # 3. Unstructured prompt on POST /api/orders -> intercepted by Agentic Service -> Core API!
    o_agentic = client.post(
        "/api/orders",
        headers={**agent_headers, "Idempotency-Key": "p0-ag-1"},
        json={
            "prompt": "Order 6 of 464306 for 2026-11-16 diet L",
            "source_channel": "telegram:999:@chef",
        },
    )
    assert o_agentic.status_code == 201, o_agentic.text
    assert o_agentic.headers["X-Aimo-Route"] == "agentic-service"
    assert o_agentic.headers.get("X-Aimo-Hop") == "agentic-service->core-api"
    ag_order = o_agentic.json()
    assert ag_order["status"] == "PLACED"
    assert ag_order["lines"][0]["product_id"] == "464306"
    assert ag_order["lines"][0]["quantity"] == 6.0

    # Idempotent replay returns exact same order; mutated payload with same key returns 409
    o_replay = client.post(
        "/api/orders",
        headers={**agent_headers, "Idempotency-Key": "p0-ag-1"},
        json={
            "prompt": "Order 6 of 464306 for 2026-11-16 diet L",
            "source_channel": "telegram:999:@chef",
        },
    )
    assert o_replay.status_code == 201
    assert o_replay.json()["id"] == ag_order["id"]

    o_conflict = client.post(
        "/api/orders",
        headers={**agent_headers, "Idempotency-Key": "p0-ag-1"},
        json={"prompt": "Order 99 of 464306 for 2026-11-16"},
    )
    assert o_conflict.status_code == 409

    # 4. Spoofed X-Aimo-Forwarded-By header from external caller is stripped (CRITIQUE.md §10.8)
    spoofed_missing_idem = client.post(
        "/api/orders",
        headers={**agent_headers, "X-Aimo-Forwarded-By": "agentic-service"},
        json={"prompt": "Order 4 of 464306 for 2026-11-16"},
    )
    assert spoofed_missing_idem.status_code == 422
    assert spoofed_missing_idem.headers["X-Aimo-Route"] == "agentic-service"

    # Verify audit_events record distinct routing_path values for core vs agentic
    with session_factory() as db:
        core_ev = db.scalar(
            select(AuditEventModel).where(
                AuditEventModel.action == "order.created",
                AuditEventModel.entity_id == core_order_id,
            )
        )
        ag_ev = db.scalar(
            select(AuditEventModel).where(
                AuditEventModel.action == "order.created",
                AuditEventModel.entity_id == ag_order["id"],
            )
        )
        assert core_ev is not None and core_ev.details.get("routing_path") == "core-api"
        assert (
            ag_ev is not None and ag_ev.details.get("routing_path") == "agentic-service->core-api"
        )


def test_recipe_sanity_warning_approval_escalation_and_mobile_assistant_a2a(
    p0_env: dict[str, object],
) -> None:
    """Test R-RECIPE-SANITY (>1.5x yield -> PENDING_APPROVAL) + /assistant A2A parity."""
    client: TestClient = p0_env["client"]  # type: ignore[assignment]
    user_headers: dict[str, str] = p0_env["user_headers"]  # type: ignore[assignment]
    agent_headers: dict[str, str] = p0_env["agent_headers"]  # type: ignore[assignment]

    # 60 pax Lohikeitto expects 0.20 L * 60 = 12 L milk; ordering 120 L (10x) costs €121.20 (< €500)
    # Yet R-RECIPE-SANITY must hold it in PENDING_APPROVAL!
    q_resp = client.post(
        "/api/quotes",
        headers=agent_headers,
        json={
            "requested_delivery_date": "2026-11-16",
            "lines": [{"product_id": "464306", "quantity": 120}],
            "diet": "L",
            "recipe_id": "rcp_salmon_soup_01",
            "target_pax": 60,
        },
    )
    assert q_resp.status_code == 201
    q_data = q_resp.json()
    assert q_data["total_excl_vat_eur"] < 500.0
    assert q_data["policy_preview"]["outcome"] == "APPROVAL_REQUIRED"
    warn = q_data["recipe_sanity_warning"]
    assert warn is not None and warn["triggered"] is True
    assert warn["rule_id"] == "R-RECIPE-SANITY"
    assert warn["offending_lines"][0]["expected_qty"] == pytest.approx(12.0, rel=0.05)

    sanity_rules = [
        r for r in q_data["policy_preview"]["rules"] if r["rule_id"] == "R-RECIPE-SANITY"
    ]
    assert len(sanity_rules) == 1
    assert sanity_rules[0]["gate"] == "OPERATIONAL_SANITY"
    assert sanity_rules[0]["overridable_via_approval"] is True
    assert sanity_rules[0]["remediation_hint"]["action_type"] == "ADJUST_TO_RECIPE_YIELD"

    o_resp = client.post(
        "/api/orders",
        headers={**agent_headers, "Idempotency-Key": "p0-sanity-1"},
        json={"quote_id": q_data["id"]},
    )
    assert o_resp.status_code == 201
    o_data = o_resp.json()
    assert o_data["status"] == "PENDING_APPROVAL"
    assert o_data["recipe_sanity_warning"]["triggered"] is True

    # Human manager approves after reviewing recipe sanity alert -> PLACED
    apr_resp = client.post(
        f"/api/orders/{o_data['id']}/approve",
        headers={**user_headers, "Idempotency-Key": "p0-sanity-apr-1"},
        json={"content_hash": o_data["content_hash"], "note": "Stocking extra milk for weekend"},
    )
    assert apr_resp.status_code == 200
    assert apr_resp.json()["status"] == "PLACED"

    # Mobile /assistant A2A negotiation parity turn
    chat_resp = client.post(
        "/api/assistant/chat",
        headers=agent_headers,
        json={"message": "Negotiate via A2A 120 L of 464306 for 60 pax lohikeitto on 2026-11-16"},
    )
    assert chat_resp.status_code == 200, chat_resp.text
    action_types = {a["type"] for a in chat_resp.json()["actions"]}
    assert "a2a_negotiation" in action_types
    assert "recipe_sanity_warning" in action_types


def test_purchasing_guard_duplicate_spam_quarantine_and_human_review(
    p0_env: dict[str, object],
) -> None:
    """Test duplicate-fingerprint spam quarantine (>=3 distinct keys) & restore/revoke."""
    client: TestClient = p0_env["client"]  # type: ignore[assignment]
    user_headers: dict[str, str] = p0_env["user_headers"]  # type: ignore[assignment]
    agent_headers: dict[str, str] = p0_env["agent_headers"]  # type: ignore[assignment]
    agent_id: str = p0_env["kitchen_agent_id"]  # type: ignore[assignment]

    def _quote_same_cart() -> str:
        r = client.post(
            "/api/quotes",
            headers=agent_headers,
            json={
                "requested_delivery_date": "2026-11-16",
                "lines": [{"product_id": "464306", "quantity": 7}],
            },
        )
        assert r.status_code == 201
        return str(r.json()["id"])

    # Attempt 1 & 2 with identical cart content_hash succeed (PLACED)
    q1 = _quote_same_cart()
    r1 = client.post(
        "/api/orders",
        headers={**agent_headers, "Idempotency-Key": "spam-k-1"},
        json={"quote_id": q1},
    )
    assert r1.status_code == 201

    q2 = _quote_same_cart()
    r2 = client.post(
        "/api/orders",
        headers={**agent_headers, "Idempotency-Key": "spam-k-2"},
        json={"quote_id": q2},
    )
    assert r2.status_code == 201

    # Attempt 3 with identical cart content_hash across 3rd distinct key -> QUARANTINED (403)!
    q3 = _quote_same_cart()
    r3 = client.post(
        "/api/orders",
        headers={**agent_headers, "Idempotency-Key": "spam-k-3"},
        json={"quote_id": q3},
    )
    assert r3.status_code == 403
    assert r3.json()["error"]["code"] == "AGENT_DISABLED"

    # Existing JWT is immediately blocked on any endpoint
    assert client.get("/api/products", headers=agent_headers).status_code == 403
    assert client.get("/api/agents/anomalies", headers=agent_headers).status_code == 403

    # GET /api/agents and GET /api/agents/{id} return status == "quarantined" (not "disabled"!)
    ag_list = client.get("/api/agents", headers=user_headers)
    assert ag_list.status_code == 200
    ag_entry = next(a for a in ag_list.json()["items"] if a["id"] == agent_id)
    assert ag_entry["status"] == "quarantined"
    ag_detail = client.get(f"/api/agents/{agent_id}", headers=user_headers)
    assert ag_detail.status_code == 200
    assert ag_detail.json()["status"] == "quarantined"

    # Human manager sees quarantined agent in GET /api/agents/anomalies
    anom = client.get("/api/agents/anomalies", headers=user_headers)
    assert anom.status_code == 200
    items = anom.json()["items"]
    assert len(items) == 1
    assert items[0]["agent_id"] == agent_id
    assert items[0]["rule_code"] == "R-SPAM-DUPLICATE-FINGERPRINT"

    # Human manager restores agent -> JWT works again & anomalies list clears
    restored = client.post(
        f"/api/agents/{agent_id}/review",
        headers=user_headers,
        json={"action": "restore", "note": "Verified test loop fixed"},
    )
    assert restored.status_code == 200
    assert restored.json()["agent"]["status"] == "active"
    assert client.get("/api/products", headers=agent_headers).status_code == 200
    assert client.get("/api/agents/anomalies", headers=user_headers).json()["items"] == []


def test_demo_personas_shadow_policy_simulator_critique_fixes_and_gemma_benchmark(
    p0_env: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test demo-personas, simulate-policy, tier/Origin, R-STANDING-DRIFT & Gemma 40/40."""
    client: TestClient = p0_env["client"]  # type: ignore[assignment]
    session_factory: sessionmaker[Session] = p0_env["session_factory"]  # type: ignore[assignment]
    user_headers: dict[str, str] = p0_env["user_headers"]  # type: ignore[assignment]
    other_headers: dict[str, str] = p0_env["other_headers"]  # type: ignore[assignment]
    agent_headers: dict[str, str] = p0_env["agent_headers"]  # type: ignore[assignment]
    agent_id: str = p0_env["kitchen_agent_id"]  # type: ignore[assignment]

    # 1. Demo personas in dev vs non-dev
    personas = client.get("/api/auth/demo-personas")
    assert personas.status_code == 200
    emails = [p["email"] for p in personas.json()["items"]]
    assert emails == [
        "anna.korhonen@hotel.fi",
        "mikko.laine@restaurant.fi",
        "sari.virtanen@festival.fi",
    ]
    assert "password" not in personas.text

    monkeypatch.setattr(settings, "app_env", "prod")
    assert client.get("/api/auth/demo-personas").status_code == 404
    monkeypatch.setattr(settings, "app_env", "dev")

    # 2. Good-Better-Best substitute tiers, Origin transparency & L,HEART school/hospital diet
    sub_resp = client.post(
        "/api/products/substitutes",
        headers=agent_headers,
        json={
            "product_id": "469363",
            "quantity": 1000,
            "requested_delivery_date": "2026-11-16",
            "diet": "L",
        },
    )
    assert sub_resp.status_code == 200
    sub_by_id = {c["product"]["id"]: c for c in sub_resp.json()["items"]}
    assert sub_by_id["464306"]["tier"] == "BEST"
    assert sub_by_id["464398"]["tier"] == "BETTER"
    assert any("Origin: FI" in t for t in sub_by_id["464306"]["tradeoffs"])

    sub_heart = client.post(
        "/api/products/substitutes",
        headers=agent_headers,
        json={
            "product_id": "469363",
            "quantity": 1000,
            "requested_delivery_date": "2026-11-16",
            "diet": "L,HEART",
        },
    )
    assert sub_heart.status_code == 200
    for cand in sub_heart.json()["items"]:
        assert "HEART_LOW_SALT" in cand["reason"]

    # 3. Standing-order drift (R-STANDING-DRIFT): >=2 prior-day PLACED orders vs +100% creep
    for day_str, idem in (
        ("2026-11-05T10:00:00+02:00", "hist-base-1"),
        ("2026-11-06T10:00:00+02:00", "hist-base-2"),
    ):
        monkeypatch.setenv("APP_NOW", day_str)
        qb = client.post(
            "/api/quotes",
            headers=agent_headers,
            json={
                "requested_delivery_date": "2026-11-16",
                "lines": [{"product_id": "464306", "quantity": 10}],
            },
        )
        assert qb.status_code == 201
        ob = client.post(
            "/api/orders",
            headers={**agent_headers, "Idempotency-Key": idem},
            json={"quote_id": qb.json()["id"]},
        )
        assert ob.status_code == 201
        assert ob.json()["status"] == "PLACED"

    monkeypatch.setenv("APP_NOW", "2026-11-13T21:00:00+02:00")
    q_drift = client.post(
        "/api/quotes",
        headers=agent_headers,
        json={
            "requested_delivery_date": "2026-11-16",
            "lines": [{"product_id": "464306", "quantity": 20}],
        },
    )
    assert q_drift.status_code == 201
    o_drift = client.post(
        "/api/orders",
        headers={**agent_headers, "Idempotency-Key": "drift-order-1"},
        json={"quote_id": q_drift.json()["id"]},
    )
    assert o_drift.status_code == 201
    assert o_drift.json()["status"] == "PENDING_APPROVAL"
    drift_rules = [r["rule_id"] for r in o_drift.json()["policy"]["rules"]]
    assert "R-STANDING-DRIFT" in drift_rules

    # 4. Create one high-value order (> €500) so shadow simulator shows a real diff
    q_hi = client.post(
        "/api/quotes",
        headers=agent_headers,
        json={
            "requested_delivery_date": "2026-11-16",
            "lines": [{"product_id": "464306", "quantity": 600}],
            "diet": "L",
        },
    )
    assert q_hi.status_code == 201
    o_hi = client.post(
        "/api/orders",
        headers={**agent_headers, "Idempotency-Key": "sim-pol-1"},
        json={"quote_id": q_hi.json()["id"]},
    )
    assert o_hi.status_code == 201
    assert o_hi.json()["status"] == "PENDING_APPROVAL"

    # Agents get 403; cross-company gets 404 on simulate-policy
    sim_body = {
        "agent_id": agent_id,
        "auto_approve_limit_eur": 1000.0,
        "max_order_value_eur": 5000.0,
        "daily_spend_limit_eur": 5000.0,
        "sample_size": 10,
    }
    assert (
        client.post(
            "/api/governance/simulate-policy", headers=agent_headers, json=sim_body
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/api/governance/simulate-policy", headers=other_headers, json=sim_body
        ).status_code
        == 404
    )

    sim_ok = client.post("/api/governance/simulate-policy", headers=user_headers, json=sim_body)
    assert sim_ok.status_code == 200
    assert sim_ok.json()["evaluated_orders"] >= 1
    assert sim_ok.json()["newly_auto_approved"] >= 1

    # DB agent auto_approve_limit_eur must remain untouched (500.0)
    with session_factory() as db:
        ag_row = db.get(Agent, agent_id)
        assert ag_row is not None and ag_row.auto_approve_limit_eur == 500.0

    # 3. CRITIQUE.md §3.1: Menu planner scaled-quantity recheck on high pax
    menu_hi = client.post(
        "/api/menu/plan",
        headers=user_headers,
        json={
            "pax": 3000,
            "meal_type": "lunch",
            "requested_delivery_date": "2026-11-16",
            "diet": "L",
        },
    )
    assert menu_hi.status_code == 200
    assert menu_hi.json()["estimated_total_excl_vat_eur"] > 100.0

    # 4. Gemma semantic firewall offline benchmark 40/40
    passed, total, _ = run_benchmark(FakeDecisionProvider())
    assert passed == total == 40
