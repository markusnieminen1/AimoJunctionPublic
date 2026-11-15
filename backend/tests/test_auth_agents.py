"""Tests for Milestone M2: Auth, Companies, Agents, Company Isolation, and Audit Chain."""

from collections.abc import Generator
from datetime import datetime, timedelta
from pathlib import Path

import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app import clock
from app.auth import JWT_AUDIENCE, compute_agent_spent_today
from app.config import settings
from app.db import get_db, init_db, make_engine
from app.ids import new_id
from app.main import create_app
from app.models import AuditEventModel, Order, Quote


@pytest.fixture()
def auth_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Generator[tuple[TestClient, sessionmaker[Session]], None, None]:
    """Provide an isolated TestClient + SQLite session factory for auth/agent tests."""
    monkeypatch.setenv("APP_NOW", "2026-11-14T10:00:00+02:00")
    monkeypatch.setattr(settings, "jwt_secret", "test-secret-key-with-at-least-32-bytes")

    db_file = tmp_path / "auth_agents_test.db"
    test_engine = make_engine(f"sqlite:///{db_file}")
    init_db(target_engine=test_engine)
    session_factory = sessionmaker(
        bind=test_engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )

    test_app = create_app()

    def _override_get_db() -> Generator[Session, None, None]:
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    test_app.dependency_overrides[get_db] = _override_get_db
    client = TestClient(test_app)
    try:
        yield client, session_factory
    finally:
        test_app.dependency_overrides.clear()
        test_engine.dispose()


def _register_user(
    client: TestClient,
    *,
    company_name: str,
    user_name: str,
    email: str,
    password: str = "secret123",
) -> dict:
    resp = client.post(
        "/api/auth/register",
        json={
            "company_name": company_name,
            "business_id": "1234567-8",
            "user_name": user_name,
            "email": email,
            "password": password,
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_full_auth_and_agent_lifecycle(
    auth_client: tuple[TestClient, sessionmaker[Session]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, session_factory = auth_client

    # 1. Register user + company
    reg = _register_user(
        client,
        company_name="Hotel Aurora Oy",
        user_name="Aino Chef",
        email="aino@aurora.test",
        password="correct-horse-battery",
    )
    user_token = reg["token"]
    assert reg["user"]["kind"] == "user"
    assert reg["user"]["id"].startswith("usr_")
    assert reg["user"]["delegated_by"] is None
    assert reg["company"]["id"].startswith("cmp_")
    assert reg["company"]["name"] == "Hotel Aurora Oy"

    # 2. Duplicate email -> 409 EMAIL_TAKEN
    dup_resp = client.post(
        "/api/auth/register",
        json={
            "company_name": "Other Oy",
            "user_name": "Duplicate",
            "email": "  AINO@aurora.test ",
            "password": "other",
        },
    )
    assert dup_resp.status_code == 409
    assert dup_resp.json()["error"]["code"] == "EMAIL_TAKEN"
    assert dup_resp.json()["request_id"].startswith("req_")

    # 3. Login failure (401) and success (200)
    bad_login = client.post(
        "/api/auth/login",
        json={"email": "aino@aurora.test", "password": "wrong-password"},
    )
    assert bad_login.status_code == 401
    assert bad_login.json()["error"]["code"] == "UNAUTHENTICATED"

    good_login = client.post(
        "/api/auth/login",
        json={"email": "aino@aurora.test", "password": "correct-horse-battery"},
    )
    assert good_login.status_code == 200
    assert good_login.json()["user"]["id"] == reg["user"]["id"]
    user_headers = {"Authorization": f"Bearer {good_login.json()['token']}"}

    # 4. GET /api/me as user
    me_user = client.get("/api/me", headers=user_headers)
    assert me_user.status_code == 200
    assert me_user.json()["principal"]["kind"] == "user"
    assert me_user.json()["company"]["id"] == reg["company"]["id"]
    assert me_user.json()["agent"] is None

    # 5. Create agent (secret shown once with aks_ prefix)
    create_resp = client.post(
        "/api/agents",
        headers=user_headers,
        json={
            "name": "Kitchen Reorder Bot",
            "description": "Morning dairy replenishment",
            "auto_approve_limit_eur": 500.129,
            "max_order_value_eur": 5000,
            "daily_spend_limit_eur": 3000,
            "allowed_categories": ["10400"],
            "max_quantity_per_line": 250,
            "can_place_orders": True,
        },
    )
    assert create_resp.status_code == 201, create_resp.text
    created = create_resp.json()
    agent = created["agent"]
    agent_secret = created["agent_secret"]
    assert agent["id"].startswith("agt_")
    assert agent_secret.startswith("aks_")
    assert agent["auto_approve_limit_eur"] == 500.13
    assert agent["delegated_by"] == {"id": reg["user"]["id"], "name": "Aino Chef"}
    assert agent["spent_today_eur"] == 0.0
    assert "secret_hash" not in agent

    # 6. Exchange agent token (wrong secret -> 401, right secret -> 200)
    bad_exchange = client.post(
        "/api/agent-auth/token",
        json={"agent_id": agent["id"], "agent_secret": "aks_wrong"},
    )
    assert bad_exchange.status_code == 401
    assert bad_exchange.json()["error"]["code"] == "UNAUTHENTICATED"

    exchange_resp = client.post(
        "/api/agent-auth/token",
        json={"agent_id": agent["id"], "agent_secret": agent_secret},
    )
    assert exchange_resp.status_code == 200, exchange_resp.text
    exchanged = exchange_resp.json()
    agent_token = exchanged["token"]
    assert exchanged["expires_at"] == "2026-11-14T11:00:00+02:00"
    assert exchanged["agent"]["last_used_at"] == "2026-11-14T10:00:00+02:00"

    # 7. GET /api/me as agent (advancing APP_NOW updates last_used_at)
    monkeypatch.setenv("APP_NOW", "2026-11-14T10:15:00+02:00")
    agent_headers = {"Authorization": f"Bearer {agent_token}"}
    me_agent = client.get("/api/me", headers=agent_headers)
    assert me_agent.status_code == 200, me_agent.text
    me_body = me_agent.json()
    assert me_body["principal"]["kind"] == "agent"
    assert me_body["principal"]["id"] == agent["id"]
    assert me_body["principal"]["delegated_by"] == {
        "id": reg["user"]["id"],
        "name": "Aino Chef",
    }
    assert me_body["agent"]["id"] == agent["id"]
    assert me_body["agent"]["last_used_at"] == "2026-11-14T10:15:00+02:00"

    # 8. Disable agent via PATCH -> existing agent token immediately fails with 403 AGENT_DISABLED
    patch_resp = client.patch(
        f"/api/agents/{agent['id']}",
        headers={"Authorization": f"Bearer {user_token}"},
        json={"status": "disabled"},
    )
    assert patch_resp.status_code == 200
    assert patch_resp.json()["status"] == "disabled"

    disabled_me = client.get("/api/me", headers=agent_headers)
    assert disabled_me.status_code == 403
    assert disabled_me.json()["error"]["code"] == "AGENT_DISABLED"

    # 9. Verify audit trail and hash chain
    with session_factory() as db:
        events = db.scalars(
            select(AuditEventModel)
            .where(AuditEventModel.company_id == reg["company"]["id"])
            .order_by(AuditEventModel.at.asc(), AuditEventModel.id.asc())
        ).all()
        actions = [e.action for e in events]
        assert actions == [
            "user.registered",
            "agent.created",
            "agent.token_issued",
            "agent.updated",
        ]
        assert events[0].prev_hash is None
        assert events[0].hash is not None
        for idx in range(1, len(events)):
            assert events[idx].prev_hash == events[idx - 1].hash
            assert events[idx].hash is not None


def test_expired_agent_rejected_on_exchange_and_existing_token(
    auth_client: tuple[TestClient, sessionmaker[Session]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _ = auth_client
    reg = _register_user(
        client,
        company_name="Fjord Catering",
        user_name="Matti",
        email="matti@fjord.test",
    )
    user_headers = {"Authorization": f"Bearer {reg['token']}"}

    create_resp = client.post(
        "/api/agents",
        headers=user_headers,
        json={
            "name": "Short-Lived Bot",
            "auto_approve_limit_eur": 100,
            "max_order_value_eur": 500,
            "daily_spend_limit_eur": 1000,
            "expires_at": "2026-11-14T10:30:00+02:00",
        },
    )
    assert create_resp.status_code == 201
    agent_id = create_resp.json()["agent"]["id"]
    agent_secret = create_resp.json()["agent_secret"]

    # Obtain token at 10:00 (valid until 11:00 JWT exp, but agent expires at 10:30)
    tok_resp = client.post(
        "/api/agent-auth/token",
        json={"agent_id": agent_id, "agent_secret": agent_secret},
    )
    assert tok_resp.status_code == 200
    agent_token = tok_resp.json()["token"]

    # Advance APP_NOW to 10:30 (agent expired, though JWT exp is 11:00)
    monkeypatch.setenv("APP_NOW", "2026-11-14T10:30:00+02:00")

    me_resp = client.get("/api/me", headers={"Authorization": f"Bearer {agent_token}"})
    assert me_resp.status_code == 403
    assert me_resp.json()["error"]["code"] == "AGENT_DISABLED"

    re_exchange = client.post(
        "/api/agent-auth/token",
        json={"agent_id": agent_id, "agent_secret": agent_secret},
    )
    assert re_exchange.status_code == 403
    assert re_exchange.json()["error"]["code"] == "AGENT_DISABLED"

    # User clears expires_at via PATCH -> agent works again
    clear_exp = client.patch(
        f"/api/agents/{agent_id}",
        headers=user_headers,
        json={"expires_at": None},
    )
    assert clear_exp.status_code == 200
    assert clear_exp.json()["expires_at"] is None
    me_after_clear = client.get("/api/me", headers={"Authorization": f"Bearer {agent_token}"})
    assert me_after_clear.status_code == 200


def test_secret_rotation_invalidates_old_secret_and_accepts_new(
    auth_client: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, _ = auth_client
    reg = _register_user(
        client,
        company_name="Baltic Kitchen",
        user_name="Liisa",
        email="liisa@baltic.test",
    )
    user_headers = {"Authorization": f"Bearer {reg['token']}"}

    create_resp = client.post(
        "/api/agents",
        headers=user_headers,
        json={
            "name": "Pantry Bot",
            "auto_approve_limit_eur": 200,
            "max_order_value_eur": 1000,
            "daily_spend_limit_eur": 2000,
        },
    )
    agent_id = create_resp.json()["agent"]["id"]
    old_secret = create_resp.json()["agent_secret"]

    rotate_resp = client.post(f"/api/agents/{agent_id}/rotate-secret", headers=user_headers)
    assert rotate_resp.status_code == 200
    new_secret = rotate_resp.json()["agent_secret"]
    assert new_secret.startswith("aks_")
    assert new_secret != old_secret

    old_try = client.post(
        "/api/agent-auth/token",
        json={"agent_id": agent_id, "agent_secret": old_secret},
    )
    assert old_try.status_code == 401
    assert old_try.json()["error"]["code"] == "UNAUTHENTICATED"

    new_try = client.post(
        "/api/agent-auth/token",
        json={"agent_id": agent_id, "agent_secret": new_secret},
    )
    assert new_try.status_code == 200


def test_agent_forbidden_from_agent_management_endpoints(
    auth_client: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, _ = auth_client
    reg = _register_user(
        client,
        company_name="Nordic Bistro",
        user_name="Sami",
        email="sami@nordic.test",
    )
    user_headers = {"Authorization": f"Bearer {reg['token']}"}

    create_resp = client.post(
        "/api/agents",
        headers=user_headers,
        json={
            "name": "Scout",
            "auto_approve_limit_eur": 0,
            "max_order_value_eur": 0,
            "daily_spend_limit_eur": 0,
            "can_place_orders": False,
        },
    )
    agent_id = create_resp.json()["agent"]["id"]
    agent_secret = create_resp.json()["agent_secret"]
    tok = client.post(
        "/api/agent-auth/token",
        json={"agent_id": agent_id, "agent_secret": agent_secret},
    ).json()["token"]
    agent_headers = {"Authorization": f"Bearer {tok}"}

    for method, path in [
        ("GET", "/api/agents"),
        ("POST", "/api/agents"),
        ("GET", f"/api/agents/{agent_id}"),
        ("PATCH", f"/api/agents/{agent_id}"),
        ("POST", f"/api/agents/{agent_id}/rotate-secret"),
        ("GET", f"/api/agents/{agent_id}/activity"),
    ]:
        kwargs = {"json": {}} if method in ("POST", "PATCH") else {}
        resp = client.request(method, path, headers=agent_headers, **kwargs)
        assert resp.status_code == 403, f"{method} {path} -> {resp.status_code}"
        assert resp.json()["error"]["code"] == "FORBIDDEN"


def test_jwt_validation_failures(
    auth_client: tuple[TestClient, sessionmaker[Session]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _ = auth_client
    reg = _register_user(
        client,
        company_name="Lapland Lodge",
        user_name="Katri",
        email="katri@lodge.test",
    )
    user_id = reg["user"]["id"]
    company_id = reg["company"]["id"]
    now = clock.now()

    # Missing header
    assert client.get("/api/me").status_code == 401

    # Tampered signature
    tampered = jwt.encode(
        {
            "sub": user_id,
            "kind": "user",
            "company_id": company_id,
            "aud": JWT_AUDIENCE,
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(hours=1)).timestamp()),
        },
        "wrong-secret-key-with-at-least-32-bytes",
        algorithm="HS256",
    )
    r_tampered = client.get("/api/me", headers={"Authorization": f"Bearer {tampered}"})
    assert r_tampered.status_code == 401
    assert r_tampered.json()["error"]["code"] == "UNAUTHENTICATED"

    # Wrong aud
    wrong_aud = jwt.encode(
        {
            "sub": user_id,
            "kind": "user",
            "company_id": company_id,
            "aud": "other-service",
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(hours=1)).timestamp()),
        },
        settings.jwt_secret,
        algorithm="HS256",
    )
    r_aud = client.get("/api/me", headers={"Authorization": f"Bearer {wrong_aud}"})
    assert r_aud.status_code == 401
    assert r_aud.json()["error"]["code"] == "UNAUTHENTICATED"

    # Expired via APP_NOW advance
    monkeypatch.setenv("APP_NOW", "2026-11-14T22:00:00+02:00")
    r_exp = client.get("/api/me", headers={"Authorization": f"Bearer {reg['token']}"})
    assert r_exp.status_code == 401
    assert r_exp.json()["error"]["code"] == "UNAUTHENTICATED"


def test_company_isolation_and_activity_pagination(
    auth_client: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, _ = auth_client
    reg_a = _register_user(
        client,
        company_name="Company A Oy",
        user_name="User A",
        email="a@companya.test",
    )
    reg_b = _register_user(
        client,
        company_name="Company B Oy",
        user_name="User B",
        email="b@companyb.test",
    )
    headers_a = {"Authorization": f"Bearer {reg_a['token']}"}
    headers_b = {"Authorization": f"Bearer {reg_b['token']}"}

    create_a = client.post(
        "/api/agents",
        headers=headers_a,
        json={
            "name": "Agent A",
            "auto_approve_limit_eur": 300,
            "max_order_value_eur": 1000,
            "daily_spend_limit_eur": 2000,
        },
    )
    agent_a_id = create_a.json()["agent"]["id"]
    agent_a_secret = create_a.json()["agent_secret"]

    # Generate multiple audit events for Agent A
    client.post(
        "/api/agent-auth/token",
        json={"agent_id": agent_a_id, "agent_secret": agent_a_secret},
    )
    client.patch(
        f"/api/agents/{agent_a_id}",
        headers=headers_a,
        json={"description": "Updated"},
    )
    client.post(f"/api/agents/{agent_a_id}/rotate-secret", headers=headers_a)

    # Company B cannot see Company A's agent in list or by ID
    list_b = client.get("/api/agents", headers=headers_b)
    assert list_b.status_code == 200
    assert list_b.json() == {"items": [], "total": 0}

    for method, path, body in [
        ("GET", f"/api/agents/{agent_a_id}", None),
        ("PATCH", f"/api/agents/{agent_a_id}", {"name": "Hijacked"}),
        ("POST", f"/api/agents/{agent_a_id}/rotate-secret", None),
        ("GET", f"/api/agents/{agent_a_id}/activity", None),
    ]:
        kwargs = {"json": body} if body is not None else {}
        resp = client.request(method, path, headers=headers_b, **kwargs)
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "NOT_FOUND"

    # Company A can paginate Agent A's activity (4 events total)
    page_1 = client.get(f"/api/agents/{agent_a_id}/activity?limit=2&offset=0", headers=headers_a)
    assert page_1.status_code == 200
    assert page_1.json()["total"] == 4
    assert len(page_1.json()["items"]) == 2

    page_2 = client.get(f"/api/agents/{agent_a_id}/activity?limit=2&offset=2", headers=headers_a)
    assert page_2.status_code == 200
    assert page_2.json()["total"] == 4
    assert len(page_2.json()["items"]) == 2
    ids_1 = {item["id"] for item in page_1.json()["items"]}
    ids_2 = {item["id"] for item in page_2.json()["items"]}
    assert ids_1.isdisjoint(ids_2)


def test_compute_agent_spent_today_counts_active_statuses_for_helsinki_day(
    auth_client: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, session_factory = auth_client
    reg = _register_user(
        client,
        company_name="Spend Test Oy",
        user_name="Buyer",
        email="buyer@spend.test",
    )
    headers = {"Authorization": f"Bearer {reg['token']}"}
    agent_id = client.post(
        "/api/agents",
        headers=headers,
        json={
            "name": "Spend Bot",
            "auto_approve_limit_eur": 500,
            "max_order_value_eur": 5000,
            "daily_spend_limit_eur": 3000,
        },
    ).json()["agent"]["id"]

    now = clock.now()
    yesterday = now - timedelta(days=1)

    with session_factory() as db:
        quote_id = new_id("qte_")
        db.add(
            Quote(
                id=quote_id,
                company_id=reg["company"]["id"],
                status="USED",
                created_at=now,
                expires_at=now + timedelta(minutes=30),
                requested_delivery_date="2026-11-16",
                total_excl_vat_eur=100.0,
                total_incl_vat_eur=114.0,
                total_co2_kg=10.0,
                overall_delivery_status="ON_TIME",
                policy_preview={"outcome": "AUTO", "rules": []},
                created_by_kind="agent",
                created_by_id=agent_id,
                created_by_name="Spend Bot",
            )
        )
        db.flush()

        def _add_order(status_val: str, amount: float, created_at: datetime) -> None:
            db.add(
                Order(
                    id=new_id("ord_"),
                    company_id=reg["company"]["id"],
                    quote_id=quote_id,
                    status=status_val,
                    created_at=created_at,
                    requested_delivery_date="2026-11-16",
                    total_excl_vat_eur=amount,
                    total_incl_vat_eur=round(amount * 1.14, 2),
                    total_co2_kg=5.0,
                    policy={"outcome": "AUTO", "rules": []},
                    created_by_kind="agent",
                    created_by_id=agent_id,
                    created_by_name="Spend Bot",
                    content_hash="abc",
                    updated_at=created_at,
                )
            )

        # Counted today
        _add_order("PLACED", 100.25, now)
        _add_order("SUBMITTING", 50.10, now)
        _add_order("SUBMISSION_UNKNOWN", 25.40, now)
        _add_order("PENDING_APPROVAL", 200.00, now)
        # Excluded statuses & yesterday
        _add_order("REJECTED", 999.00, now)
        _add_order("CANCELLED", 999.00, now)
        _add_order("EXPIRED", 999.00, now)
        _add_order("FAILED", 999.00, now)
        _add_order("PLACED", 500.00, yesterday)
        db.commit()

        spent = compute_agent_spent_today(db, agent_id, reg["company"]["id"])
        assert spent == 375.75

    agent_resp = client.get(f"/api/agents/{agent_id}", headers=headers)
    assert agent_resp.status_code == 200
    assert agent_resp.json()["spent_today_eur"] == 375.75
