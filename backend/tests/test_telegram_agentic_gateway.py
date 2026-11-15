"""Tests for Telegram Agentic Gateway parity & multi-company connected order confirmations."""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.aimo.client import AimoClient, get_aimo_client
from app.audit import verify_audit_chain
from app.config import settings
from app.db import get_db, init_db, make_engine
from app.main import create_app
from app.services.telegram_customer import compute_bridge_secret
from scripts.demo_seed import seed_demo
from scripts.seed_catalog import (
    DEFAULT_CATEGORIES_PATH,
    DEFAULT_CO2_PATH,
    DEFAULT_RELIABILITY_PATH,
    seed_catalog,
)

TESTS_DIR = Path(__file__).resolve().parent
FIXTURE_PRODUCTS_PATH = TESTS_DIR / "fixtures" / "products_sample.json"
AIMO_FIXTURE_DIR = TESTS_DIR / "fixtures" / "aimo"


@pytest.fixture()
def tg_gateway_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[dict[str, Any], None, None]:
    """Isolated SQLite DB seeded with 3 demo companies, catalog, and fixture Aimo + Fake LLM."""
    monkeypatch.setenv("APP_NOW", "2026-11-13T21:00:00+02:00")
    monkeypatch.setattr(settings, "app_env", "dev")
    monkeypatch.setattr(settings, "jwt_secret", "test-secret-key-with-at-least-32-bytes")
    monkeypatch.setattr(settings, "aimo_mode", "fixture")
    monkeypatch.setattr(settings, "llm_provider", "fake")
    monkeypatch.setattr(settings, "fast_llm_provider", "fake")
    monkeypatch.setattr(settings, "order_gateway_simulate", "none")
    monkeypatch.setattr(settings, "telegram_bot_token", "test-bot-token-tg-gateway")

    db_file = tmp_path / "tg_gateway.db"
    engine = make_engine(f"sqlite:///{db_file}")
    init_db(target_engine=engine)
    session_factory = sessionmaker(
        bind=engine, autoflush=False, autocommit=False, expire_on_commit=False
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
        creds = seed_demo(
            db,
            credentials_path=tmp_path / ".demo_credentials",
            with_extra_personas=True,
        )

    fixture_aimo = AimoClient(mode="fixture", fixture_dir=AIMO_FIXTURE_DIR, cache_ttl_seconds=0.0)
    app = create_app()

    def _override_db() -> Generator[Session, None, None]:
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_aimo_client] = lambda: fixture_aimo

    bridge_headers = {
        "X-Telegram-Bridge-Secret": compute_bridge_secret("test-bot-token-tg-gateway")
    }

    with TestClient(app) as client:
        anna_login = client.post(
            "/api/auth/login",
            json={"email": "anna.korhonen@hotel.fi", "password": creds["user"]["password"]},
        )
        assert anna_login.status_code == 200, anna_login.text
        anna = anna_login.json()

        mikko_login = client.post(
            "/api/auth/login",
            json={"email": "mikko.laine@restaurant.fi", "password": "demo1234"},
        )
        assert mikko_login.status_code == 200, mikko_login.text
        mikko = mikko_login.json()

        sari_login = client.post(
            "/api/auth/login",
            json={"email": "sari.virtanen@festival.fi", "password": "demo1234"},
        )
        assert sari_login.status_code == 200, sari_login.text
        sari = sari_login.json()

        kitchen = creds["agents"]["kitchen_agent"]
        a_tok = client.post(
            "/api/agent-auth/token",
            json={"agent_id": kitchen["id"], "agent_secret": kitchen["agent_secret"]},
        )
        assert a_tok.status_code == 200, a_tok.text

        bistro_ag = client.post(
            "/api/agents",
            headers={"Authorization": f"Bearer {mikko['token']}"},
            json={
                "name": "Bistro MCP Buyer",
                "auto_approve_limit_eur": 300.0,
                "max_order_value_eur": 2000.0,
                "daily_spend_limit_eur": 1500.0,
                "can_place_orders": True,
            },
        )
        assert bistro_ag.status_code == 201, bistro_ag.text
        bistro_tok = client.post(
            "/api/agent-auth/token",
            json={
                "agent_id": bistro_ag.json()["agent"]["id"],
                "agent_secret": bistro_ag.json()["agent_secret"],
            },
        )
        assert bistro_tok.status_code == 200

        yield {
            "client": client,
            "session_factory": session_factory,
            "bridge_headers": bridge_headers,
            "anna_headers": {"Authorization": f"Bearer {anna['token']}"},
            "anna_company_id": anna["user"]["company_id"],
            "mikko_headers": {"Authorization": f"Bearer {mikko['token']}"},
            "mikko_company_id": mikko["user"]["company_id"],
            "sari_headers": {"Authorization": f"Bearer {sari['token']}"},
            "sari_company_id": sari["user"]["company_id"],
            "aurora_agent_headers": {"Authorization": f"Bearer {a_tok.json()['token']}"},
            "bistro_agent_headers": {"Authorization": f"Bearer {bistro_tok.json()['token']}"},
        }

    app.dependency_overrides.clear()
    engine.dispose()


def test_telegram_agentic_gateway_web_parity_opt_in_hydration_and_conversational_order(
    tg_gateway_env: dict[str, Any],
) -> None:
    """Verify website-opted-in Telegram users converse, plan menus, negotiate, and place orders."""
    client: TestClient = tg_gateway_env["client"]
    bridge_headers: dict[str, str] = tg_gateway_env["bridge_headers"]
    mikko_headers: dict[str, str] = tg_gateway_env["mikko_headers"]
    mikko_company_id: str = tg_gateway_env["mikko_company_id"]
    sari_headers: dict[str, str] = tg_gateway_env["sari_headers"]

    # 1. Website opt-in + verify-chat (NO QR token required!) hydrates session immediately
    opt_r = client.put(
        "/api/auth/telegram",
        headers=mikko_headers,
        json={"telegram_id": "@mikko_chef", "enabled": True, "consent_confirmed": True},
    )
    assert opt_r.status_code == 200
    assert opt_r.json()["linked_via_qr"] is False

    ver_r = client.post(
        "/api/auth/telegram/verify-chat",
        headers=mikko_headers,
        json={
            "telegram_chat_id": "77001122",
            "telegram_user_id": "77001122",
            "telegram_username": "mikko_chef",
        },
    )
    assert ver_r.status_code == 200
    assert ver_r.json()["linked_via_qr"] is True
    assert (
        client.put(
            "/api/auth/telegram/branch",
            headers=mikko_headers,
            json={"branch_id": "brn_mikko_1"},
        ).status_code
        == 200
    )

    # Also verify @username-only opt-in auto-binds on first private Telegram message
    assert (
        client.put(
            "/api/auth/telegram",
            headers=sari_headers,
            json={"telegram_id": "@sari_fest", "enabled": True, "consent_confirmed": True},
        ).status_code
        == 200
    )
    assert (
        client.put(
            "/api/auth/telegram/branch",
            headers=sari_headers,
            json={"branch_id": "brn_sari_1"},
        ).status_code
        == 200
    )
    sari_hello = client.post(
        "/api/telegram/customer-chat",
        headers=bridge_headers,
        json={
            "telegram_user_id": "77003344",
            "telegram_chat_id": "77003344",
            "telegram_username": "sari_fest",
            "message": "What are your capabilities?",
        },
    )
    assert sari_hello.status_code == 200, sari_hello.text
    assert "Nordic Campus Catering" in sari_hello.json()["reply_text"]

    # 2. Multi-turn Web-parity conversation for Mikko:
    # Turn 1: Menu planning
    t1 = client.post(
        "/api/telegram/customer-chat",
        headers=bridge_headers,
        json={
            "telegram_user_id": "77001122",
            "telegram_chat_id": "77001122",
            "telegram_username": "mikko_chef",
            "message": "Plan a lactose-free lunch menu for 20 pax",
        },
    )
    assert t1.status_code == 200, t1.text
    t1_body = t1.json()
    assert t1_body["menu_plan"] is not None
    assert t1_body["menu_plan"]["pax"] == 20
    conv_id = t1_body["conversation_id"]
    assert conv_id is not None

    # Turn 2: Quote request (capped <= 35, not auto-placed)
    t2 = client.post(
        "/api/telegram/customer-chat",
        headers=bridge_headers,
        json={
            "telegram_user_id": "77001122",
            "telegram_chat_id": "77001122",
            "telegram_username": "mikko_chef",
            "message": "Quote 10 x 464306 for 2026-11-16",
        },
    )
    assert t2.status_code == 200, t2.text
    t2_body = t2.json()
    assert t2_body["conversation_id"] == conv_id
    assert t2_body["quote"] is not None
    assert t2_body["order"] is None
    assert t2_body["auto_executed"] is False
    open_qid = t2_body["quote"]["id"]

    # Turn 3: Natural conversational confirmation ("yes") places the open quote -> PLACED!
    t3 = client.post(
        "/api/telegram/customer-chat",
        headers=bridge_headers,
        json={
            "telegram_user_id": "77001122",
            "telegram_chat_id": "77001122",
            "telegram_username": "mikko_chef",
            "message": "yes",
        },
    )
    assert t3.status_code == 200, t3.text
    t3_body = t3.json()
    assert t3_body["order"] is not None
    assert t3_body["order"]["quote_id"] == open_qid
    assert t3_body["order"]["status"] == "PLACED"
    assert t3_body["order"]["gateway_reference"] is not None
    assert t3_body["assurance_score"] == 100
    assert "Order confirmation:" in t3_body["reply_text"]
    placed_oid = t3_body["order"]["id"]

    # Turn 4: Live order status check via Telegram chat
    t4 = client.post(
        "/api/telegram/customer-chat",
        headers=bridge_headers,
        json={
            "telegram_user_id": "77001122",
            "telegram_chat_id": "77001122",
            "telegram_username": "mikko_chef",
            "message": "What is the status of my recent orders?",
        },
    )
    assert t4.status_code == 200, t4.text
    assert placed_oid in t4.json()["reply_text"]
    assert "PLACED" in t4.json()["reply_text"]

    # Turn 5: Enable negotiation in /admin -> negotiate bulk quote -> confirm in Finnish ("tilaa")
    assert (
        client.put(
            f"/api/admin/company-negotiations/{mikko_company_id}",
            headers=mikko_headers,
            json={"allow_price_negotiation": True},
        ).status_code
        == 200
    )
    t5 = client.post(
        "/api/telegram/customer-chat",
        headers=bridge_headers,
        json={
            "telegram_user_id": "77001122",
            "telegram_chat_id": "77001122",
            "telegram_username": "mikko_chef",
            "message": "Negotiate a 4% bulk discount on 200 x 464306 for 2026-11-16",
        },
    )
    assert t5.status_code == 200, t5.text
    t5_body = t5.json()
    assert t5_body["quote"] is not None
    assert (t5_body["quote"]["negotiated_savings_eur"] or 0) > 0
    assert len(t5_body["a2a_turns"]) >= 2

    t6 = client.post(
        "/api/telegram/customer-chat",
        headers=bridge_headers,
        json={
            "telegram_user_id": "77001122",
            "telegram_chat_id": "77001122",
            "telegram_username": "mikko_chef",
            "message": "tilaa",
        },
    )
    assert t6.status_code == 200, t6.text
    assert t6.json()["order"] is not None
    assert t6.json()["order"]["status"] == "PLACED"
    assert (t6.json()["order"]["negotiated_savings_eur"] or 0) > 0


def test_connected_telegram_order_confirmations_decisions_and_tenant_isolation(
    tg_gateway_env: dict[str, Any],
) -> None:
    """Verify HMAC auth, multi-company order confirmations, 1-tap approve, and unlink privacy."""
    client: TestClient = tg_gateway_env["client"]
    session_factory: sessionmaker[Session] = tg_gateway_env["session_factory"]
    bridge_headers: dict[str, str] = tg_gateway_env["bridge_headers"]
    anna_headers: dict[str, str] = tg_gateway_env["anna_headers"]
    anna_company_id: str = tg_gateway_env["anna_company_id"]
    mikko_headers: dict[str, str] = tg_gateway_env["mikko_headers"]
    mikko_company_id: str = tg_gateway_env["mikko_company_id"]
    aurora_agent_headers: dict[str, str] = tg_gateway_env["aurora_agent_headers"]
    bistro_agent_headers: dict[str, str] = tg_gateway_env["bistro_agent_headers"]

    # 1. Missing/invalid HMAC secret -> 403 FORBIDDEN on all new bridge endpoints
    assert client.get("/api/telegram/connected-sessions").status_code == 403
    assert client.get("/api/telegram/order-notifications").status_code == 403
    assert (
        client.post(
            "/api/telegram/decide-order",
            json={
                "telegram_user_id": "88001122",
                "telegram_chat_id": "88001122",
                "order_id": "ord_fake",
                "decision": "reject",
            },
        ).status_code
        == 403
    )

    # 2. Connect Anna (Hotel Aurora via QR) & Mikko (Bistro Satama via web opt-in + verify-chat)
    tok = client.post("/api/auth/telegram/link-token", headers=anna_headers).json()["token"]
    assert (
        client.post(
            "/api/telegram/link-claim",
            headers=bridge_headers,
            json={
                "token": tok,
                "telegram_user_id": "88001122",
                "telegram_chat_id": "88001122",
                "telegram_username": "anna_hotel",
            },
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/api/telegram/select-branch",
            headers=bridge_headers,
            json={
                "telegram_user_id": "88001122",
                "telegram_chat_id": "88001122",
                "branch_id": "brn_strand",
            },
        ).status_code
        == 200
    )

    client.put(
        "/api/auth/telegram",
        headers=mikko_headers,
        json={"telegram_id": "77009900", "enabled": True, "consent_confirmed": True},
    )
    assert (
        client.post(
            "/api/auth/telegram/verify-chat",
            headers=mikko_headers,
            json={
                "telegram_chat_id": "77009900",
                "telegram_user_id": "77009900",
                "telegram_username": "mikko_bistro",
            },
        ).status_code
        == 200
    )
    assert (
        client.put(
            "/api/auth/telegram/branch",
            headers=mikko_headers,
            json={"branch_id": "brn_mikko_1"},
        ).status_code
        == 200
    )

    sessions_r = client.get("/api/telegram/connected-sessions", headers=bridge_headers)
    assert sessions_r.status_code == 200
    connected_chats = {s["telegram_chat_id"]: s for s in sessions_r.json()["items"]}
    assert set(connected_chats.keys()) == {"88001122", "77009900"}
    assert connected_chats["88001122"]["company_id"] == anna_company_id
    assert connected_chats["88001122"]["active_branch"]["short_name"] == "Hilton Strand"
    assert connected_chats["77009900"]["company_id"] == mikko_company_id

    # Capture cursor timestamp before creating new orders
    baseline = client.get("/api/telegram/order-notifications", headers=bridge_headers).json()
    cursor_ts = baseline["server_time"]

    # 3. Anna places an order on Web Cart -> ORDER_CONFIRMED delivered to Anna ONLY
    q_web = client.post(
        "/api/quotes",
        headers=anna_headers,
        json={
            "requested_delivery_date": "2026-11-16",
            "lines": [{"product_id": "464306", "quantity": 12}],
        },
    ).json()
    o_web = client.post(
        "/api/orders",
        headers={**anna_headers, "Idempotency-Key": "tg-notif-web-1"},
        json={"quote_id": q_web["id"], "note": "Web cart order"},
    ).json()
    assert o_web["status"] == "PLACED"

    # 4. Bistro Satama MCP agent places an auto-approved order -> ORDER_CONFIRMED to Mikko ONLY
    mcp_q = client.post(
        "/api/mcp",
        headers=bistro_agent_headers,
        json={
            "jsonrpc": "2.0",
            "id": "b-q-1",
            "method": "tools/call",
            "params": {
                "name": "create_quote",
                "arguments": {
                    "requested_delivery_date": "2026-11-16",
                    "lines": [{"product_id": "464306", "quantity": 15}],
                },
            },
        },
    ).json()["result"]["structuredContent"]
    mcp_o = client.post(
        "/api/mcp",
        headers=bistro_agent_headers,
        json={
            "jsonrpc": "2.0",
            "id": "b-o-1",
            "method": "tools/call",
            "params": {
                "name": "place_order",
                "arguments": {
                    "quote_id": mcp_q["id"],
                    "idempotency_key": "tg-notif-mcp-1",
                    "note": "MCP restock",
                },
            },
        },
    ).json()["result"]["structuredContent"]
    assert mcp_o["status"] == "PLACED"

    # 5. Same-chat Telegram order has placed_in_same_telegram_chat=True (skipped unless requested)
    q_tg = client.post(
        "/api/quotes",
        headers=mikko_headers,
        json={
            "requested_delivery_date": "2026-11-16",
            "lines": [{"product_id": "464398", "quantity": 4}],
        },
    ).json()
    o_tg = client.post(
        "/api/telegram/place-quote",
        headers=bridge_headers,
        json={
            "telegram_user_id": "77009900",
            "telegram_chat_id": "77009900",
            "quote_id": q_tg["id"],
            "idempotency_key": "tg-same-chat-1",
        },
    ).json()["order"]
    assert o_tg["status"] == "PLACED"

    notif_r = client.get(
        f"/api/telegram/order-notifications?since={cursor_ts}",
        headers=bridge_headers,
    )
    assert notif_r.status_code == 200, notif_r.text
    notifs = notif_r.json()["items"]
    by_order = {n["order"]["id"]: n for n in notifs}

    assert o_web["id"] in by_order
    assert by_order[o_web["id"]]["event_type"] == "ORDER_CONFIRMED"
    assert by_order[o_web["id"]]["telegram_chat_id"] == "88001122"
    assert by_order[o_web["id"]]["origin_channel"] == "web"
    assert by_order[o_web["id"]]["can_undo_in_telegram"] is True

    assert mcp_o["id"] in by_order
    assert by_order[mcp_o["id"]]["event_type"] == "ORDER_CONFIRMED"
    assert by_order[mcp_o["id"]]["telegram_chat_id"] == "77009900"
    assert by_order[mcp_o["id"]]["origin_channel"] == "mcp"

    # Same-chat Telegram order skipped by default, included with include_same_chat_origin=true
    assert o_tg["id"] not in by_order
    notif_all = client.get(
        f"/api/telegram/order-notifications?since={cursor_ts}&include_same_chat_origin=true",
        headers=bridge_headers,
    ).json()["items"]
    assert any(
        n["order"]["id"] == o_tg["id"] and n["placed_in_same_telegram_chat"] is True
        for n in notif_all
    )

    # 6. Pending approval -> 1-tap Telegram approve with 12-char hash prefix (& tamper check)
    q_pend = client.post(
        "/api/quotes",
        headers=aurora_agent_headers,
        json={
            "requested_delivery_date": "2026-11-16",
            "lines": [{"product_id": "464306", "quantity": 600}],
        },
    ).json()
    o_pend = client.post(
        "/api/orders",
        headers={**aurora_agent_headers, "Idempotency-Key": "tg-notif-pend-1"},
        json={"quote_id": q_pend["id"], "note": "Needs chef sign-off"},
    ).json()
    assert o_pend["status"] == "PENDING_APPROVAL"

    pend_notifs = client.get(
        f"/api/telegram/order-notifications?since={cursor_ts}",
        headers=bridge_headers,
    ).json()["items"]
    pend_item = next(n for n in pend_notifs if n["order"]["id"] == o_pend["id"])
    assert pend_item["event_type"] == "ORDER_PENDING_APPROVAL"
    assert pend_item["telegram_chat_id"] == "88001122"

    # Cross-tenant Mikko CANNOT approve Hotel Aurora's order -> 404 NOT_FOUND
    cross_decide = client.post(
        "/api/telegram/decide-order",
        headers=bridge_headers,
        json={
            "telegram_user_id": "77009900",
            "telegram_chat_id": "77009900",
            "order_id": o_pend["id"],
            "decision": "approve",
            "content_hash": o_pend["content_hash"][:12],
        },
    )
    assert cross_decide.status_code == 404

    # Tampered 12-char hash prefix -> 409 APPROVAL_CONTENT_CHANGED
    tampered = client.post(
        "/api/telegram/decide-order",
        headers=bridge_headers,
        json={
            "telegram_user_id": "88001122",
            "telegram_chat_id": "88001122",
            "order_id": o_pend["id"],
            "decision": "approve",
            "content_hash": "000000000000",
        },
    )
    assert tampered.status_code == 409
    assert tampered.json()["error"]["code"] == "APPROVAL_CONTENT_CHANGED"

    # Valid 12-char hash prefix -> 200 PLACED!
    decided = client.post(
        "/api/telegram/decide-order",
        headers=bridge_headers,
        json={
            "telegram_user_id": "88001122",
            "telegram_chat_id": "88001122",
            "order_id": o_pend["id"],
            "decision": "approve",
            "content_hash": o_pend["content_hash"][:12],
            "note": "Approved from Telegram inline button",
        },
    )
    assert decided.status_code == 200, decided.text
    assert decided.json()["order"]["status"] == "PLACED"
    assert decided.json()["active_branch"]["short_name"] == "Hilton Strand"

    # 7. Unlink immediately stops all notifications to that chat
    assert (
        client.post(
            "/api/telegram/unlink",
            headers=bridge_headers,
            json={"telegram_user_id": "88001122", "telegram_chat_id": "88001122"},
        ).status_code
        == 200
    )
    after_unlink = client.get(
        f"/api/telegram/order-notifications?since={cursor_ts}",
        headers=bridge_headers,
    ).json()["items"]
    assert all(n["telegram_chat_id"] != "88001122" for n in after_unlink)

    with session_factory() as db:
        assert verify_audit_chain(db, anna_company_id).valid is True
        assert verify_audit_chain(db, mikko_company_id).valid is True
