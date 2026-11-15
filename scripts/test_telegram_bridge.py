"""Offline unit tests for scripts/telegram_bridge.py (zero real network calls)."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

import httpx

_PUBLIC_REPO_ROOT = str(Path(__file__).resolve().parent.parent)
if sys.path[:1] != [_PUBLIC_REPO_ROOT]:
    if _PUBLIC_REPO_ROOT in sys.path:
        sys.path.remove(_PUBLIC_REPO_ROOT)
    sys.path.insert(0, _PUBLIC_REPO_ROOT)
for _mod_name in ("scripts.telegram_bridge", "scripts"):
    _loaded = sys.modules.get(_mod_name)
    _loaded_file = getattr(_loaded, "__file__", None) or ""
    if _loaded is not None and (
        not _loaded_file or not str(Path(_loaded_file).resolve()).startswith(_PUBLIC_REPO_ROOT)
    ):
        sys.modules.pop(_mod_name, None)

from scripts.telegram_bridge import (  # noqa: E402
    BRIDGE_POST_TIMEOUT_SECONDS,
    CUSTOMER_TIMEOUT_FALLBACK_MESSAGE,
    AimoCoreApiClient,
    TelegramBotClient,
    TelegramStateStore,
    compute_bridge_secret,
    format_approval_card,
    infer_order_rescue_kind,
    is_verified_operator,
    poll_and_push_order_notifications,
    poll_and_push_pending_approvals,
    process_telegram_update,
    redact_secrets,
    resolve_bot_token,
)


class TelegramBridgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self._op_patch = patch.multiple(
            "scripts.telegram_bridge",
            OPERATOR_USERNAME="demo_operator",
            OPERATOR_USER_ID="10000001",
        )
        self._op_patch.start()

    def tearDown(self) -> None:
        self._op_patch.stop()
    def test_redact_secrets_scrubs_telegram_bot_tokens_and_jwts(self) -> None:
        fake_tg_tok = "7123456789:" + "AAHxyz1234567890abcdefghijklmnop"
        fake_jwt = "eyJ" + "hbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.sig123"
        raw = (
            f"Error calling https://api.telegram.org/bot{fake_tg_tok}/getUpdates "
            f"and bare {fake_tg_tok} "
            f"with {fake_jwt}"
        )
        cleaned = redact_secrets(raw)
        self.assertNotIn("7123456789:AAH", cleaned)
        self.assertNotIn("eyJ" + "hbGciOi", cleaned)
        self.assertIn("[REDACTED_TELEGRAM_TOKEN]", cleaned)
        self.assertIn("[REDACTED_JWT]", cleaned)

    def test_resolve_bot_token_supports_lowercase_telegram_api_key(self) -> None:
        tok = resolve_bot_token({"telegram_api_key": "12345678:AAH_sample_tok"})
        self.assertEqual(tok, "12345678:AAH_sample_tok")

    def test_fail_closed_operator_and_website_opt_in_only_plus_content_hash(self) -> None:
        tg_calls: list[tuple[str, dict[str, Any]]] = []
        aimo_calls: list[tuple[str, str, dict[str, Any], dict[str, str]]] = []
        full_hash = "a" * 64
        opted_in_recipients: list[dict[str, Any]] = []

        def tg_handler(request: httpx.Request) -> httpx.Response:
            method = request.url.path.rsplit("/", 1)[-1]
            payload = json.loads(request.content.decode("utf-8"))
            tg_calls.append((method, payload))
            if method == "getChat":
                cid = str(payload.get("chat_id"))
                if cid == "900100":
                    return httpx.Response(
                        200,
                        json={
                            "ok": True,
                            "result": {"id": 900100, "type": "private", "username": "demo_operator"},
                        },
                    )
                if cid == "777888":
                    return httpx.Response(
                        200,
                        json={
                            "ok": True,
                            "result": {"id": 777888, "type": "private", "username": "demochef"},
                        },
                    )
                return httpx.Response(
                    200,
                    json={
                        "ok": True,
                        "result": {"id": 999999, "type": "private", "username": "stranger"},
                    },
                )
            return httpx.Response(200, json={"ok": True, "result": {"message_id": 77}})

        def aimo_handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content.decode("utf-8")) if request.content else {}
            aimo_calls.append((request.method, request.url.path, body, dict(request.headers)))
            if request.url.path == "/api/auth/login":
                return httpx.Response(
                    200,
                    json={
                        "token": "jwt_from_login_endpoint",
                        "user": {
                            "kind": "user",
                            "id": "usr_demo",
                            "name": "Anna Korhonen",
                            "company_id": "cmp_demo",
                            "delegated_by": None,
                        },
                    },
                )
            if request.url.path == "/api/auth/telegram/recipients":
                return httpx.Response(200, json={"items": opted_in_recipients})
            if request.url.path == "/api/auth/telegram/verify-chat":
                return httpx.Response(200, json={"ok": True})
            if request.url.path == "/api/orders":
                return httpx.Response(
                    200,
                    json={
                        "items": [
                            {
                                "id": "ord_01TESTPENDING",
                                "status": "PENDING_APPROVAL",
                                "total_excl_vat_eur": 1010.0,
                                "requested_delivery_date": "2026-11-16",
                                "diet": "L",
                                "content_hash": full_hash,
                                "created_by": {"id": "agt_1", "name": "Breakfast Bot", "kind": "agent"},
                                "policy": {
                                    "outcome": "APPROVAL_REQUIRED",
                                    "rules": [{"rule_id": "R-AUTO-LIMIT", "outcome": "APPROVAL_REQUIRED"}],
                                },
                                "lines": [
                                    {
                                        "product_id": "464306",
                                        "product_name": "Nordic Lactose-Free Milk 1L",
                                        "quantity": 1000,
                                        "sales_unit": "ST",
                                        "delivery_status": "ON_TIME",
                                    }
                                ],
                            }
                        ]
                    },
                )
            if request.url.path == "/api/orders/ord_01TESTPENDING/approve":
                self.assertEqual(body["content_hash"], full_hash)
                self.assertIn("Approved via Telegram by @demochef", body["note"])
                self.assertTrue(request.headers.get("idempotency-key", "").startswith("tg_apr_"))
                return httpx.Response(
                    200,
                    json={
                        "id": "ord_01TESTPENDING",
                        "status": "PLACED",
                        "gateway_reference": "aimo_gw_999",
                    },
                )
            return httpx.Response(404)

        async def run() -> None:
            with tempfile.TemporaryDirectory() as tmpdir:
                tmp = Path(tmpdir)
                inbox = tmp / "INFORMATIONSHARING.md"
                inbox.write_text("## Inbox\n", encoding="utf-8")
                state = TelegramStateStore(tmp / ".telegram_state.json")
                tg = TelegramBotClient(
                    "12345678:TEST_BRIDGE_BOT_TOK",
                    transport=httpx.MockTransport(tg_handler),
                )
                aimo = AimoCoreApiClient(
                    "http://127.0.0.1:8001",
                    email="demo@aimoannos.test",
                    password="demo_password_for_test",
                    transport=httpx.MockTransport(aimo_handler),
                )
                try:
                    # 1. Unregistered stranger sends message -> SILENT DROP (0 sendMessage, no chat hijack!)
                    unauth_msg = await process_telegram_update(
                        {
                            "update_id": 1,
                            "message": {
                                "message_id": 10,
                                "from": {"id": 999999, "username": "stranger", "is_bot": False},
                                "chat": {"id": 999999, "type": "private"},
                                "text": "/start",
                            },
                        },
                        allowed_user_ids=set(),  # Even with empty allowed_user_ids, fails CLOSED!
                        state=state,
                        inbox_path=inbox,
                        tg=tg,
                        aimo=aimo,
                    )
                    self.assertEqual(unauth_msg, "unauthorized_silent_drop")
                    self.assertIsNone(state.operator_chat_id)
                    self.assertEqual(len([c for c in tg_calls if c[0] == "sendMessage"]), 0)

                    # 2. Outbound Team Lead send to non-@demo_operator chat raises PermissionError
                    with self.assertRaises(PermissionError):
                        await tg.send_verified_operator_message("999999", "Should never send!")
                    self.assertEqual(len([c for c in tg_calls if c[0] == "sendMessage"]), 0)

                    # 3. With 0 website opt-ins, poll_and_push_pending_approvals sends 0 messages
                    pushed_zero = await poll_and_push_pending_approvals(
                        state=state,
                        tg=tg,
                        aimo=aimo,
                    )
                    self.assertEqual(pushed_zero, 0)

                    # 4. @demo_operator sends Team Lead message -> logged to INFORMATIONSHARING.md & verified send works
                    op_res = await process_telegram_update(
                        {
                            "update_id": 2,
                            "message": {
                                "message_id": 11,
                                "from": {"id": 424242, "username": "demo_operator", "is_bot": False},
                                "chat": {"id": 900100, "type": "private"},
                                "text": "Status check on API separation?",
                            },
                        },
                        allowed_user_ids=set(),
                        state=state,
                        inbox_path=inbox,
                        tg=tg,
                        aimo=aimo,
                    )
                    self.assertEqual(op_res, "logged_to_team_lead")
                    self.assertEqual(state.operator_chat_id, "900100")
                    self.assertIn("Status check on API separation?", inbox.read_text(encoding="utf-8"))
                    sent_ok = await tg.send_verified_operator_message("900100", "On it!")
                    self.assertEqual(len(sent_ok), 1)

                    # 5. Demo user explicitly opts in via website clone -> /start binds chat -> push sends card
                    opted_in_recipients.append(
                        {
                            "user_id": "usr_demo",
                            "telegram_username": "demochef",
                            "telegram_chat_id": "",
                            "enabled": True,
                        }
                    )
                    start_res = await process_telegram_update(
                        {
                            "update_id": 3,
                            "message": {
                                "message_id": 20,
                                "from": {"id": 777888, "username": "demochef", "is_bot": False},
                                "chat": {"id": 777888, "type": "private"},
                                "text": "/start",
                            },
                        },
                        allowed_user_ids=set(),
                        state=state,
                        inbox_path=inbox,
                        tg=tg,
                        aimo=aimo,
                    )
                    self.assertEqual(start_res, "help")

                    pushed_one = await poll_and_push_pending_approvals(
                        state=state,
                        tg=tg,
                        aimo=aimo,
                    )
                    self.assertEqual(pushed_one, 1)
                    card_call = [c for c in tg_calls if c[0] == "sendMessage" and "reply_markup" in c[1]][-1]
                    self.assertEqual(card_call[1]["chat_id"], "777888")
                    apr_cb = card_call[1]["reply_markup"]["inline_keyboard"][0][0]["callback_data"]
                    self.assertLessEqual(len(apr_cb.encode("utf-8")), 64)

                    # 6. Stranger cannot tap Approve on the nonce
                    stranger_cb = await process_telegram_update(
                        {
                            "update_id": 4,
                            "callback_query": {
                                "id": "cb_bad",
                                "from": {"id": 999999, "username": "stranger"},
                                "message": {"message_id": 77, "chat": {"id": 999999, "type": "private"}},
                                "data": apr_cb,
                            },
                        },
                        allowed_user_ids=set(),
                        state=state,
                        inbox_path=inbox,
                        tg=tg,
                        aimo=aimo,
                    )
                    self.assertEqual(stranger_cb, "unauthorized_callback")

                    # 7. Opted-in demo user taps Approve -> sends full 64-char content_hash + note + Idempotency-Key
                    ok_cb = await process_telegram_update(
                        {
                            "update_id": 5,
                            "callback_query": {
                                "id": "cb_ok",
                                "from": {"id": 777888, "username": "demochef"},
                                "message": {"message_id": 77, "chat": {"id": 777888, "type": "private"}},
                                "data": apr_cb,
                            },
                        },
                        allowed_user_ids=set(),
                        state=state,
                        inbox_path=inbox,
                        tg=tg,
                        aimo=aimo,
                    )
                    self.assertEqual(ok_cb, "approved")
                finally:
                    await tg.aclose()
                    await aimo.aclose()

        asyncio.run(run())

    def test_one_tap_safe_rescue_path_a_places_clean_order_before_rejecting_draft(self) -> None:
        held_hash = "b" * 64
        new_hash = "c" * 64
        call_order: list[str] = []

        # Gate 1 / UNIT_MISMATCH orders are never eligible for 1-tap rescue
        self.assertIsNone(
            infer_order_rescue_kind(
                {
                    "status": "PENDING_APPROVAL",
                    "policy": {"outcome": "APPROVAL_REQUIRED", "rules": [{"rule_id": "R-DELIVERY-RISK", "outcome": "APPROVAL_REQUIRED"}]},
                    "lines": [{"product_id": "469363", "quantity": 1000, "delivery_status": "AT_RISK", "flags": ["PARTIAL", "UNIT_MISMATCH"]}],
                }
            )
        )

        held_order = {
            "id": "ord_01JTESTPARTIAL000000000001",
            "status": "PENDING_APPROVAL",
            "total_excl_vat_eur": 2480.0,
            "requested_delivery_date": "2026-11-16",
            "diet": None,
            "content_hash": held_hash,
            "created_by": {"id": "agt_kitchen", "name": "Kitchen Agent", "kind": "agent"},
            "policy": {
                "outcome": "APPROVAL_REQUIRED",
                "rules": [{"rule_id": "R-DELIVERY-RISK", "outcome": "APPROVAL_REQUIRED"}],
            },
            "lines": [
                {
                    "product_id": "469363",
                    "product_name": "Nordic Organic Milk 1L",
                    "quantity": 1000,
                    "sales_unit": "ST",
                    "delivery_status": "AT_RISK",
                    "flags": ["PARTIAL"],
                }
            ],
        }
        _, markup = format_approval_card(held_order, "abc123def456")
        self.assertEqual(len(markup["inline_keyboard"]), 2)
        fix_cb = markup["inline_keyboard"][1][0]["callback_data"]
        self.assertEqual(fix_cb, "fix:abc123def456")
        self.assertLessEqual(len(fix_cb.encode("utf-8")), 64)

        def tg_handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"ok": True, "result": {"message_id": 88}})

        def aimo_handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content.decode("utf-8")) if request.content else {}
            call_order.append(f"{request.method} {request.url.path}")
            if request.url.path == "/api/auth/login":
                return httpx.Response(200, json={"token": "jwt_user"})
            if request.url.path == "/api/auth/telegram/recipients":
                return httpx.Response(
                    200,
                    json={"items": [{"user_id": "usr_demo", "telegram_username": "demochef", "enabled": True}]},
                )
            if request.url.path == f"/api/orders/{held_order['id']}":
                return httpx.Response(200, json=held_order)
            if request.url.path == "/api/agents":
                return httpx.Response(
                    200,
                    json={"items": [{"id": "agt_kitchen", "hard_diet": "L"}], "total": 1},
                )
            if request.url.path == "/api/products/substitutes":
                #Owning agent's hard_diet="L" must be merged when order.diet is None!
                self.assertEqual(body.get("diet"), "L")
                return httpx.Response(
                    200,
                    json={
                        "requested_product_id": "469363",
                        "diet": "L",
                        "items": [
                            {
                                "product": {"id": "999111", "name": "Oddlygood Oat Drink 1L", "category_name": "Plant drinks"},
                                "quote_line": {"product_id": "999111", "delivery_status": "ON_TIME", "flags": []},
                            },
                            {
                                "product": {"id": "464306", "name": "Nordic Dairy Lactose-Free Milk 1L", "category_name": "Milk"},
                                "quote_line": {"product_id": "464306", "delivery_status": "ON_TIME", "flags": []},
                            },
                        ],
                    },
                )
            if request.url.path == "/api/quotes":
                self.assertEqual(body.get("diet"), "L")
                self.assertEqual(body["lines"], [{"product_id": "464306", "quantity": 1000.0}])
                return httpx.Response(
                    201,
                    json={
                        "id": "qte_rescue_1",
                        "policy_preview": {"outcome": "AUTO", "rules": []},
                        "lines": [{"product_id": "464306", "delivery_status": "ON_TIME", "flags": []}],
                    },
                )
            if request.url.path == "/api/orders" and request.method == "POST":
                idem = request.headers.get("idempotency-key", "")
                self.assertTrue(idem.startswith("rescue-place:"))
                self.assertLessEqual(len(idem), 128)
                self.assertEqual(body.get("diet"), "L")
                return httpx.Response(
                    201,
                    json={
                        "id": "ord_01JRESCUEDPLACED0000000002",
                        "status": "PLACED",
                        "total_excl_vat_eur": 1010.0,
                        "content_hash": new_hash,
                    },
                )
            if request.url.path == f"/api/orders/{held_order['id']}/reject":
                self.assertIn("Superseded by rescued order ord_01JRESCUEDPLACED0000000002", body.get("note", ""))
                return httpx.Response(200, json={"id": held_order["id"], "status": "REJECTED"})
            return httpx.Response(404)

        async def run() -> None:
            with tempfile.TemporaryDirectory() as tmpdir:
                tmp = Path(tmpdir)
                inbox = tmp / "INFORMATIONSHARING.md"
                inbox.write_text("## Inbox\n", encoding="utf-8")
                state = TelegramStateStore(tmp / ".telegram_state.json")
                nonce = state.register_approval_nonce(
                    order_id=held_order["id"],
                    content_hash=held_hash,
                    total_excl_vat_eur=2480.0,
                )
                tg = TelegramBotClient(
                    "12345678:TEST_BRIDGE_BOT_TOK",
                    transport=httpx.MockTransport(tg_handler),
                )
                aimo = AimoCoreApiClient(
                    "http://127.0.0.1:8001",
                    email="demo@aimoannos.test",
                    password="demo_password_for_test",
                    transport=httpx.MockTransport(aimo_handler),
                )
                try:
                    res = await process_telegram_update(
                        {
                            "update_id": 99,
                            "callback_query": {
                                "id": "cb_fix",
                                "from": {"id": 777888, "username": "demochef"},
                                "message": {"message_id": 88, "chat": {"id": 777888, "type": "private"}},
                                "data": f"fix:{nonce}",
                            },
                        },
                        allowed_user_ids=set(),
                        state=state,
                        inbox_path=inbox,
                        tg=tg,
                        aimo=aimo,
                    )
                    self.assertEqual(res, "rescued")
                    # Replacement POST /api/orders MUST occur BEFORE POST /api/orders/{id}/reject!
                    idx_place = call_order.index("POST /api/orders")
                    idx_reject = call_order.index(f"POST /api/orders/{held_order['id']}/reject")
                    self.assertLess(idx_place, idx_reject)
                finally:
                    await tg.aclose()
                    await aimo.aclose()

        asyncio.run(run())

    def test_send_verified_operator_photo_locks_to_configured_operator_only(self) -> None:
        sent_photos: list[bytes] = []

        def tg_handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/getChat"):
                body = json.loads(request.content.decode("utf-8"))
                cid = str(body.get("chat_id"))
                if cid == "10000001":
                    return httpx.Response(
                        200,
                        json={"ok": True, "result": {"id": 10000001, "type": "private", "username": "demo_operator"}},
                    )
                return httpx.Response(
                    200,
                    json={"ok": True, "result": {"id": 999, "type": "private", "username": "stranger"}},
                )
            if request.url.path.endswith("/sendPhoto"):
                sent_photos.append(request.content)
                return httpx.Response(200, json={"ok": True, "result": {"message_id": 501}})
            return httpx.Response(200, json={"ok": True, "result": True})

        async def run() -> None:
            with tempfile.TemporaryDirectory() as tmpdir:
                photo = Path(tmpdir) / "arch.png"
                photo.write_bytes(b"\x89PNG\r\n\x1a\nfake-png-bytes")
                tg = TelegramBotClient(
                    "12345678:TEST_BRIDGE_BOT_TOK",
                    transport=httpx.MockTransport(tg_handler),
                )
                try:
                    with self.assertRaises(PermissionError):
                        await tg.send_verified_operator_photo("999", photo, caption="Should fail")
                    self.assertEqual(len(sent_photos), 0)

                    res = await tg.send_verified_operator_photo(
                        "10000001",
                        photo,
                        caption="Architecture diagram",
                        reply_to_message_id=21,
                    )
                    self.assertEqual(res.get("message_id"), 501)
                    self.assertEqual(len(sent_photos), 1)
                    self.assertIn(b"fake-png-bytes", sent_photos[0])
                finally:
                    await tg.aclose()

        asyncio.run(run())

    def test_format_approval_card_renders_5h_reservation_and_warnings(self) -> None:
        base_order: dict[str, Any] = {
            "id": "ord_hold_01",
            "total_excl_vat_eur": 640.0,
            "requested_delivery_date": "2026-11-16",
            "diet": "L",
            "created_by": {"id": "agt_01", "name": "Kitchen Bot", "kind": "agent"},
            "content_hash": "a" * 64,
            "policy": {"outcome": "APPROVAL_REQUIRED", "rules": [{"rule_id": "R-AUTO-LIMIT", "outcome": "APPROVAL_REQUIRED"}]},
            "lines": [{"product_id": "464306", "product_name": "Nordic Dairy Lactose-Free Milk 1 L", "quantity": 600, "sales_unit": "ST", "delivery_status": "ON_TIME"}],
        }

        # 1. Legacy / NONE order omits reservation block cleanly
        plain_txt, _ = format_approval_card(base_order, "n1")
        self.assertNotIn("Stock Hold", plain_txt)

        # 2. Active 5h hold + 1 prior warning
        held_txt, _ = format_approval_card(
            {
                **base_order,
                "reservation_status": "HELD_5H",
                "reservation_expires_at": "2026-11-14T21:30:00+02:00",
                "company_warning_count_7d": 1,
            },
            "n2",
        )
        self.assertIn("🔒 Stock Hold: Platform stock reserved until 2026-11-14 21:30 (5h hold)", held_txt)
        self.assertIn("⚠️ Fair-Use Warning (1/3):", held_txt)

        # 3. Suspended due to >= 3 hanging-order warnings
        susp_txt, _ = format_approval_card(
            {
                **base_order,
                "reservation_status": "UNRESERVED_DUE_TO_WARNINGS",
                "company_warning_count_7d": 3,
                "reservation_warning_message": "3 expired holds in 7d — ships if stock remains at approval",
            },
            "n3",
        )
        self.assertIn("⚠️ Stock Hold Suspended: 3 expired holds in 7d — ships if stock remains at approval", susp_txt)

    def test_customer_link_claim_multibranch_prompt_and_select_branch_auto_reply(self) -> None:
        bot_token = "12345678:TEST_BRIDGE_BOT_TOK"
        expected_secret = hmac.new(
            bot_token.encode("utf-8"),
            b"aimo-tg-bridge-v1",
            hashlib.sha256,
        ).hexdigest()
        self.assertEqual(compute_bridge_secret(bot_token), expected_secret)

        tg_calls: list[tuple[str, dict[str, Any]]] = []
        aimo_calls: list[tuple[str, str, dict[str, Any], dict[str, str]]] = []
        branches = [
            {"id": "brn_strand", "name": "Hilton Helsinki Strand", "short_name": "Hilton Strand"},
            {
                "id": "brn_kalastaja",
                "name": "Hilton Helsinki Kalastajantorppa",
                "short_name": "Hilton Kalastajantorppa",
            },
        ]

        def tg_handler(request: httpx.Request) -> httpx.Response:
            method = request.url.path.rsplit("/", 1)[-1]
            payload = json.loads(request.content.decode("utf-8"))
            tg_calls.append((method, payload))
            return httpx.Response(200, json={"ok": True, "result": {"message_id": 200}})

        def aimo_handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content.decode("utf-8")) if request.content else {}
            headers = {k.lower(): v for k, v in request.headers.items()}
            aimo_calls.append((request.method, request.url.path, body, headers))
            self.assertEqual(headers.get("x-telegram-bridge-secret"), expected_secret)
            if request.url.path == "/api/telegram/link-claim":
                self.assertEqual(body["token"], "tok_hilton_123")
                self.assertEqual(body["telegram_user_id"], "555001")
                self.assertEqual(body["telegram_chat_id"], "555001")
                self.assertEqual(body["telegram_username"], "liisachef")
                return httpx.Response(
                    200,
                    json={
                        "user_name": "Liisa",
                        "user_email": "liisa@hilton.test",
                        "company_name": "Hilton Finland",
                        "branches": branches,
                        "active_branch": None,
                        "needs_branch_selection": True,
                    },
                )
            if request.url.path == "/api/telegram/select-branch":
                self.assertEqual(body["branch_id"], "brn_strand")
                return httpx.Response(
                    200,
                    json={
                        "active_branch": branches[0],
                        "branches": branches,
                        "auto_reply": {
                            "status": "ok",
                            "reply_text": "Ready for **Hilton Strand**! Found **Nordic Dairy Lactose-Free Milk 1L** × 10.",
                            "quote": {"id": "qte_strand_1", "total_excl_vat_eur": 10.10},
                        },
                    },
                )
            return httpx.Response(404)

        async def run() -> None:
            with tempfile.TemporaryDirectory() as tmpdir:
                tmp = Path(tmpdir)
                inbox = tmp / "INFORMATIONSHARING.md"
                inbox.write_text("## Inbox\n", encoding="utf-8")
                state_path = tmp / ".telegram_state.json"
                state = TelegramStateStore(state_path)
                tg = TelegramBotClient(bot_token, transport=httpx.MockTransport(tg_handler))
                aimo = AimoCoreApiClient(
                    "http://127.0.0.1:8001",
                    bot_token=bot_token,
                    transport=httpx.MockTransport(aimo_handler),
                )
                try:
                    # 1. Instant pairing `/start link_tok_hilton_123` -> multi-branch prompt
                    res_link = await process_telegram_update(
                        {
                            "update_id": 101,
                            "message": {
                                "message_id": 1,
                                "from": {"id": 555001, "username": "LiisaChef", "is_bot": False},
                                "chat": {"id": 555001, "type": "private"},
                                "text": "/start link_tok_hilton_123",
                            },
                        },
                        allowed_user_ids=set(),
                        state=state,
                        inbox_path=inbox,
                        tg=tg,
                        aimo=aimo,
                    )
                    self.assertEqual(res_link, "customer_linked")
                    self.assertIn("555001", state["customer_chats"])
                    self.assertEqual(state["customer_chats"]["555001"]["company_name"], "Hilton Finland")
                    reloaded = TelegramStateStore(state_path)
                    self.assertIn("555001", reloaded.customer_chats)

                    sent_greeting = [c[1] for c in tg_calls if c[0] == "sendMessage"][-1]
                    self.assertIn(
                        "Hi Liisa! 👋 Connected to Hilton Finland. Are you making an order for Hilton Strand or Hilton Kalastajantorppa?",
                        sent_greeting["text"],
                    )
                    btns = [
                        btn
                        for row in sent_greeting["reply_markup"]["inline_keyboard"]
                        for btn in row
                    ]
                    self.assertEqual(
                        [(b["text"], b["callback_data"]) for b in btns],
                        [
                            ("🏨 Hilton Strand", "brn:brn_strand"),
                            ("🏨 Hilton Kalastajantorppa", "brn:brn_kalastaja"),
                        ],
                    )

                    # 2. Tap `brn:brn_strand` -> sets kitchen & sends formatted auto_reply + quote button
                    res_brn = await process_telegram_update(
                        {
                            "update_id": 102,
                            "callback_query": {
                                "id": "cb_brn_1",
                                "from": {"id": 555001, "username": "LiisaChef"},
                                "message": {"message_id": 200, "chat": {"id": 555001, "type": "private"}},
                                "data": "brn:brn_strand",
                            },
                        },
                        allowed_user_ids=set(),
                        state=state,
                        inbox_path=inbox,
                        tg=tg,
                        aimo=aimo,
                    )
                    self.assertEqual(res_brn, "branch_selected_auto_replied")
                    sent_auto = [c[1] for c in tg_calls if c[0] == "sendMessage"][-1]
                    self.assertIn("📍 Kitchen set to Hilton Helsinki Strand.", sent_auto["text"])
                    self.assertIn("Ready for Hilton Strand! Found Nordic Dairy Lactose-Free Milk 1L × 10.", sent_auto["text"])
                    self.assertNotIn("**", sent_auto["text"])
                    ord_btn = sent_auto["reply_markup"]["inline_keyboard"][0][0]
                    self.assertEqual(ord_btn["text"], "🛒 Place Order (€10.10)")
                    self.assertTrue(ord_btn["callback_data"].startswith("ord:"))

                    # 3. `/branch` command renders cached branch buttons immediately; `/unlink` removes chat
                    res_menu = await process_telegram_update(
                        {
                            "update_id": 103,
                            "message": {
                                "message_id": 2,
                                "from": {"id": 555001, "username": "LiisaChef", "is_bot": False},
                                "chat": {"id": 555001, "type": "private"},
                                "text": "/branch",
                            },
                        },
                        allowed_user_ids=set(),
                        state=state,
                        inbox_path=inbox,
                        tg=tg,
                        aimo=aimo,
                    )
                    self.assertEqual(res_menu, "branch_menu")

                    res_unlink = await process_telegram_update(
                        {
                            "update_id": 104,
                            "message": {
                                "message_id": 3,
                                "from": {"id": 555001, "username": "LiisaChef", "is_bot": False},
                                "chat": {"id": 555001, "type": "private"},
                                "text": "/unlink",
                            },
                        },
                        allowed_user_ids=set(),
                        state=state,
                        inbox_path=inbox,
                        tg=tg,
                        aimo=aimo,
                    )
                    self.assertEqual(res_unlink, "customer_unlinked")
                    self.assertNotIn("555001", state.customer_chats)
                finally:
                    await tg.aclose()
                    await aimo.aclose()

        asyncio.run(run())

    def test_invalid_link_and_stranger_free_text_strict_zero_outbound_messages(self) -> None:
        bot_token = "12345678:TEST_BRIDGE_BOT_TOK"
        tg_calls: list[tuple[str, dict[str, Any]]] = []
        customer_chat_calls: list[dict[str, Any]] = []

        def tg_handler(request: httpx.Request) -> httpx.Response:
            method = request.url.path.rsplit("/", 1)[-1]
            payload = json.loads(request.content.decode("utf-8"))
            tg_calls.append((method, payload))
            return httpx.Response(200, json={"ok": True, "result": {"message_id": 300}})

        def aimo_handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content.decode("utf-8")) if request.content else {}
            if request.url.path == "/api/telegram/link-claim":
                return httpx.Response(410, json={"error": {"message": "Link expired"}})
            if request.url.path == "/api/telegram/customer-chat":
                customer_chat_calls.append(body)
                return httpx.Response(404, json={"error": {"message": "Not linked"}})
            return httpx.Response(404)

        async def run() -> None:
            with tempfile.TemporaryDirectory() as tmpdir:
                tmp = Path(tmpdir)
                inbox = tmp / "INFORMATIONSHARING.md"
                inbox.write_text("## Inbox\n", encoding="utf-8")
                state = TelegramStateStore(tmp / ".telegram_state.json")
                tg = TelegramBotClient(bot_token, transport=httpx.MockTransport(tg_handler))
                aimo = AimoCoreApiClient(
                    "http://127.0.0.1:8001",
                    bot_token=bot_token,
                    transport=httpx.MockTransport(aimo_handler),
                )
                try:
                    # 1. Stranger sends `/start link_expired` -> 0 outbound Telegram messages!
                    r1 = await process_telegram_update(
                        {
                            "update_id": 201,
                            "message": {
                                "message_id": 10,
                                "from": {"id": 888999, "username": "random_stranger", "is_bot": False},
                                "chat": {"id": 888999, "type": "private"},
                                "text": "/start link_expired",
                            },
                        },
                        allowed_user_ids=set(),
                        state=state,
                        inbox_path=inbox,
                        tg=tg,
                        aimo=aimo,
                    )
                    self.assertEqual(r1, "unauthorized_silent_drop")

                    # 2. Stranger sends free-text -> 0 outbound Telegram messages & 0 customer-chat spam!
                    r2 = await process_telegram_update(
                        {
                            "update_id": 202,
                            "message": {
                                "message_id": 11,
                                "from": {"id": 888999, "username": "random_stranger", "is_bot": False},
                                "chat": {"id": 888999, "type": "private"},
                                "text": "Can you order 50L milk for me?",
                            },
                        },
                        allowed_user_ids=set(),
                        state=state,
                        inbox_path=inbox,
                        tg=tg,
                        aimo=aimo,
                    )
                    self.assertEqual(r2, "unauthorized_silent_drop")

                    # 3. Stranger sends customer callback -> 0 outbound Telegram messages!
                    r3 = await process_telegram_update(
                        {
                            "update_id": 203,
                            "callback_query": {
                                "id": "cb_stranger_ord",
                                "from": {"id": 888999, "username": "random_stranger"},
                                "message": {"message_id": 99, "chat": {"id": 888999, "type": "private"}},
                                "data": "ord:deadbeef1234",
                            },
                        },
                        allowed_user_ids=set(),
                        state=state,
                        inbox_path=inbox,
                        tg=tg,
                        aimo=aimo,
                    )
                    self.assertEqual(r3, "unauthorized_callback")
                    self.assertEqual(len(tg_calls), 0)
                    self.assertEqual(len(customer_chat_calls), 0)

                    # 4. Stale previously-linked non-operator getting 404 from customer-chat -> purged & 0 messages!
                    state.customer_chats["777111"] = {
                        "telegram_user_id": "777111",
                        "company_name": "Old Cafe",
                    }
                    r4 = await process_telegram_update(
                        {
                            "update_id": 204,
                            "message": {
                                "message_id": 12,
                                "from": {"id": 777111, "username": "oldchef", "is_bot": False},
                                "chat": {"id": 777111, "type": "private"},
                                "text": "10 L milk",
                            },
                        },
                        allowed_user_ids=set(),
                        state=state,
                        inbox_path=inbox,
                        tg=tg,
                        aimo=aimo,
                    )
                    self.assertEqual(r4, "customer_stale_silent_drop")
                    self.assertNotIn("777111", state.customer_chats)
                    self.assertEqual(len(tg_calls), 0)

                    # 5. Operator 10000001 sending expired link gets concise expiry note
                    r5 = await process_telegram_update(
                        {
                            "update_id": 205,
                            "message": {
                                "message_id": 13,
                                "from": {"id": 10000001, "username": "demo_operator", "is_bot": False},
                                "chat": {"id": 10000001, "type": "private"},
                                "text": "/start link_expired",
                            },
                        },
                        allowed_user_ids=set(),
                        state=state,
                        inbox_path=inbox,
                        tg=tg,
                        aimo=aimo,
                    )
                    self.assertEqual(r5, "link_claim_expired_operator")
                    self.assertEqual(len([c for c in tg_calls if c[0] == "sendMessage"]), 1)
                    self.assertIn("expired or was already used", tg_calls[-1][1]["text"])
                finally:
                    await tg.aclose()
                    await aimo.aclose()

        asyncio.run(run())

    def test_operator_dual_mode_customer_and_lead_channel(self) -> None:
        bot_token = "12345678:TEST_BRIDGE_BOT_TOK"
        tg_calls: list[tuple[str, dict[str, Any]]] = []
        customer_msgs: list[str] = []

        def tg_handler(request: httpx.Request) -> httpx.Response:
            method = request.url.path.rsplit("/", 1)[-1]
            payload = json.loads(request.content.decode("utf-8"))
            tg_calls.append((method, payload))
            return httpx.Response(200, json={"ok": True, "result": {"message_id": 400}})

        def aimo_handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content.decode("utf-8")) if request.content else {}
            if request.url.path == "/api/telegram/link-claim":
                return httpx.Response(
                    200,
                    json={
                        "user_name": "Anna Korhonen",
                        "user_email": "demo@aimoannos.test",
                        "company_name": "Ravintola Katajanokka",
                        "branches": [
                            {
                                "id": "brn_main",
                                "name": "Ravintola Katajanokka Kitchen",
                                "short_name": "Katajanokka",
                            }
                        ],
                        "active_branch": {
                            "id": "brn_main",
                            "name": "Ravintola Katajanokka Kitchen",
                            "short_name": "Katajanokka",
                        },
                        "needs_branch_selection": False,
                    },
                )
            if request.url.path == "/api/telegram/customer-chat":
                customer_msgs.append(body["message"])
                return httpx.Response(
                    200,
                    json={
                        "status": "ok",
                        "reply_text": "Prepared **10 L lactose-free milk** for tomorrow!",
                        "quote": {"id": "qte_op_1", "total_excl_vat_eur": 10.10},
                    },
                )
            return httpx.Response(404)

        async def run() -> None:
            with tempfile.TemporaryDirectory() as tmpdir:
                tmp = Path(tmpdir)
                inbox = tmp / "INFORMATIONSHARING.md"
                inbox.write_text("## Inbox\n", encoding="utf-8")
                state = TelegramStateStore(tmp / ".telegram_state.json")
                tg = TelegramBotClient(bot_token, transport=httpx.MockTransport(tg_handler))
                aimo = AimoCoreApiClient(
                    "http://127.0.0.1:8001",
                    bot_token=bot_token,
                    transport=httpx.MockTransport(aimo_handler),
                )
                try:
                    self.assertEqual(state.get("operator_mode", "lead"), "lead")

                    # 1. Operator claims link -> switches to customer mode with Back to Team Lead button
                    r_claim = await process_telegram_update(
                        {
                            "update_id": 301,
                            "message": {
                                "message_id": 21,
                                "from": {"id": 10000001, "username": "demo_operator", "is_bot": False},
                                "chat": {"id": 10000001, "type": "private"},
                                "text": "/start link_valid_demo",
                            },
                        },
                        allowed_user_ids=set(),
                        state=state,
                        inbox_path=inbox,
                        tg=tg,
                        aimo=aimo,
                    )
                    self.assertEqual(r_claim, "customer_linked")
                    self.assertEqual(state.operator_mode, "customer")
                    claim_msg = [c[1] for c in tg_calls if c[0] == "sendMessage"][-1]
                    self.assertIn("Connected to Ravintola Katajanokka Kitchen.", claim_msg["text"])
                    self.assertIn("/lead", claim_msg["text"])
                    claim_cbs = [
                        b["callback_data"]
                        for row in claim_msg["reply_markup"]["inline_keyboard"]
                        for b in row
                    ]
                    self.assertIn("mode:lead", claim_cbs)

                    # 2. Operator sends plain message in customer mode -> goes to customer-chat
                    r_cust = await process_telegram_update(
                        {
                            "update_id": 302,
                            "message": {
                                "message_id": 22,
                                "from": {"id": 10000001, "username": "demo_operator", "is_bot": False},
                                "chat": {"id": 10000001, "type": "private"},
                                "text": "10 L lactose-free milk for tomorrow",
                            },
                        },
                        allowed_user_ids=set(),
                        state=state,
                        inbox_path=inbox,
                        tg=tg,
                        aimo=aimo,
                    )
                    self.assertEqual(r_cust, "customer_chat_replied")
                    self.assertEqual(customer_msgs, ["10 L lactose-free milk for tomorrow"])
                    self.assertNotIn("10 L lactose-free milk", inbox.read_text(encoding="utf-8"))
                    cust_reply = [c[1] for c in tg_calls if c[0] == "sendMessage"][-1]
                    reply_cbs = [
                        b["callback_data"]
                        for row in cust_reply["reply_markup"]["inline_keyboard"]
                        for b in row
                    ]
                    self.assertIn("mode:lead", reply_cbs)

                    # 3. `/lead <message>` forwards directly to INFORMATIONSHARING.md even in customer mode!
                    r_force_lead = await process_telegram_update(
                        {
                            "update_id": 303,
                            "message": {
                                "message_id": 23,
                                "from": {"id": 10000001, "username": "demo_operator", "is_bot": False},
                                "chat": {"id": 10000001, "type": "private"},
                                "text": "/lead Urgent note for Team Lead while testing buyer UX",
                            },
                        },
                        allowed_user_ids=set(),
                        state=state,
                        inbox_path=inbox,
                        tg=tg,
                        aimo=aimo,
                    )
                    self.assertEqual(r_force_lead, "logged_to_team_lead")
                    self.assertEqual(state.operator_mode, "customer")
                    self.assertIn(
                        "Urgent note for Team Lead while testing buyer UX",
                        inbox.read_text(encoding="utf-8"),
                    )

                    # 4. Tap `mode:lead` -> restores Team Lead mode; plain messages log to INFORMATIONSHARING.md
                    r_mode_cb = await process_telegram_update(
                        {
                            "update_id": 304,
                            "callback_query": {
                                "id": "cb_mode_lead",
                                "from": {"id": 10000001, "username": "demo_operator"},
                                "message": {"message_id": 400, "chat": {"id": 10000001, "type": "private"}},
                                "data": "mode:lead",
                            },
                        },
                        allowed_user_ids=set(),
                        state=state,
                        inbox_path=inbox,
                        tg=tg,
                        aimo=aimo,
                    )
                    self.assertEqual(r_mode_cb, "operator_mode_lead")
                    self.assertEqual(state.operator_mode, "lead")
                    mode_msg = [c[1] for c in tg_calls if c[0] == "sendMessage"][-1]
                    self.assertEqual(mode_msg["text"], "Switched back to Team Lead operator channel.")

                    r_op_plain = await process_telegram_update(
                        {
                            "update_id": 305,
                            "message": {
                                "message_id": 24,
                                "from": {"id": 10000001, "username": "demo_operator", "is_bot": False},
                                "chat": {"id": 10000001, "type": "private"},
                                "text": "Ship Batch D to live once QA passes.",
                            },
                        },
                        allowed_user_ids=set(),
                        state=state,
                        inbox_path=inbox,
                        tg=tg,
                        aimo=aimo,
                    )
                    self.assertEqual(r_op_plain, "logged_to_team_lead")
                    self.assertIn(
                        "Ship Batch D to live once QA passes.",
                        inbox.read_text(encoding="utf-8"),
                    )

                    # 5. `/customer` re-enters customer mode and `/lead` switches back
                    r_re_cust = await process_telegram_update(
                        {
                            "update_id": 306,
                            "message": {
                                "message_id": 25,
                                "from": {"id": 10000001, "username": "demo_operator", "is_bot": False},
                                "chat": {"id": 10000001, "type": "private"},
                                "text": "/customer",
                            },
                        },
                        allowed_user_ids=set(),
                        state=state,
                        inbox_path=inbox,
                        tg=tg,
                        aimo=aimo,
                    )
                    self.assertEqual(r_re_cust, "operator_mode_customer")
                    self.assertEqual(state.operator_mode, "customer")

                    r_slash_lead = await process_telegram_update(
                        {
                            "update_id": 307,
                            "message": {
                                "message_id": 26,
                                "from": {"id": 10000001, "username": "demo_operator", "is_bot": False},
                                "chat": {"id": 10000001, "type": "private"},
                                "text": "/lead",
                            },
                        },
                        allowed_user_ids=set(),
                        state=state,
                        inbox_path=inbox,
                        tg=tg,
                        aimo=aimo,
                    )
                    self.assertEqual(r_slash_lead, "operator_mode_lead")
                    self.assertEqual(state.operator_mode, "lead")
                finally:
                    await tg.aclose()
                    await aimo.aclose()

        asyncio.run(run())

    def test_customer_chat_quote_options_and_one_tap_place_order_placed_and_held_5h(self) -> None:
        bot_token = "12345678:TEST_BRIDGE_BOT_TOK"
        tg_calls: list[tuple[str, dict[str, Any]]] = []
        placed_payloads: list[dict[str, Any]] = []

        def tg_handler(request: httpx.Request) -> httpx.Response:
            method = request.url.path.rsplit("/", 1)[-1]
            payload = json.loads(request.content.decode("utf-8"))
            tg_calls.append((method, payload))
            return httpx.Response(200, json={"ok": True, "result": {"message_id": 500}})

        def aimo_handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content.decode("utf-8")) if request.content else {}
            if request.url.path == "/api/telegram/customer-chat":
                msg = body.get("message", "")
                if msg == "milk for tomorrow":
                    return httpx.Response(
                        200,
                        json={
                            "status": "ok",
                            "reply_text": "Which milk would you like?",
                            "clarification_options": [
                                "Nordic Dairy Lactose-Free Milk 1L × 20",
                                "Nordic Semi-Skimmed Milk 1L × 600",
                            ],
                        },
                    )
                if msg == "Nordic Dairy Lactose-Free Milk 1L × 20":
                    return httpx.Response(
                        200,
                        json={
                            "status": "ok",
                            "reply_text": "Quote ready: **Nordic Dairy Lactose-Free Milk 1L** × 20 for tomorrow.",
                            "quote": {"id": "qte_small_20", "total_excl_vat_eur": 20.20},
                        },
                    )
                return httpx.Response(
                    200,
                    json={
                        "status": "ok",
                        "reply_text": "Large catering quote ready (manager approval required).",
                        "quote": {"quote_id": "qte_large_600", "total_excl_vat_eur": 640.00},
                    },
                )
            if request.url.path == "/api/telegram/place-quote":
                placed_payloads.append(body)
                if body["quote_id"] == "qte_small_20":
                    return httpx.Response(
                        201,
                        json={
                            "id": "ord_01SMALLPLACED",
                            "status": "PLACED",
                            "total_excl_vat_eur": 20.20,
                            "requested_delivery_date": "2026-11-16",
                        },
                    )
                return httpx.Response(
                    201,
                    json={
                        "order": {
                            "id": "ord_01LARGEHELD",
                            "status": "PENDING_APPROVAL",
                            "total_excl_vat_eur": 640.00,
                            "requested_delivery_date": "2026-11-16",
                            "reservation_status": "HELD_5H",
                            "reservation_expires_at": "2026-11-14T23:30:00+02:00",
                        }
                    },
                )
            return httpx.Response(404)

        async def run() -> None:
            with tempfile.TemporaryDirectory() as tmpdir:
                tmp = Path(tmpdir)
                inbox = tmp / "INFORMATIONSHARING.md"
                inbox.write_text("## Inbox\n", encoding="utf-8")
                state = TelegramStateStore(tmp / ".telegram_state.json")
                state.customer_chats["600100"] = {
                    "telegram_user_id": "600100",
                    "company_name": "Bistro Helsinki",
                }
                tg = TelegramBotClient(bot_token, transport=httpx.MockTransport(tg_handler))
                aimo = AimoCoreApiClient(
                    "http://127.0.0.1:8001",
                    bot_token=bot_token,
                    transport=httpx.MockTransport(aimo_handler),
                )
                try:
                    # 1. Ambiguous message -> clarification_options buttons `opt:<nonce>:0` & `opt:<nonce>:1`
                    r1 = await process_telegram_update(
                        {
                            "update_id": 401,
                            "message": {
                                "message_id": 30,
                                "from": {"id": 600100, "username": "bistrochef", "is_bot": False},
                                "chat": {"id": 600100, "type": "private"},
                                "text": "milk for tomorrow",
                            },
                        },
                        allowed_user_ids=set(),
                        state=state,
                        inbox_path=inbox,
                        tg=tg,
                        aimo=aimo,
                    )
                    self.assertEqual(r1, "customer_chat_replied")
                    opt_msg = [c[1] for c in tg_calls if c[0] == "sendMessage"][-1]
                    opt_cb_0 = opt_msg["reply_markup"]["inline_keyboard"][0][0]["callback_data"]
                    opt_cb_1 = opt_msg["reply_markup"]["inline_keyboard"][1][0]["callback_data"]
                    self.assertTrue(opt_cb_0.startswith("opt:"))

                    # 2. Tap `opt:<nonce>:0` -> returns quote `qte_small_20` -> tap `ord:<nonce>` -> PLACED
                    r2 = await process_telegram_update(
                        {
                            "update_id": 402,
                            "callback_query": {
                                "id": "cb_opt_0",
                                "from": {"id": 600100, "username": "bistrochef"},
                                "message": {"message_id": 500, "chat": {"id": 600100, "type": "private"}},
                                "data": opt_cb_0,
                            },
                        },
                        allowed_user_ids=set(),
                        state=state,
                        inbox_path=inbox,
                        tg=tg,
                        aimo=aimo,
                    )
                    self.assertEqual(r2, "customer_chat_replied")
                    q1_msg = [c[1] for c in tg_calls if c[0] == "sendMessage"][-1]
                    ord_btn_1 = q1_msg["reply_markup"]["inline_keyboard"][0][0]
                    self.assertEqual(ord_btn_1["text"], "🛒 Place Order (€20.20)")
                    ord_cb_1 = ord_btn_1["callback_data"]
                    nonce_1 = ord_cb_1.split(":", 1)[1]

                    r3 = await process_telegram_update(
                        {
                            "update_id": 403,
                            "callback_query": {
                                "id": "cb_ord_1",
                                "from": {"id": 600100, "username": "bistrochef"},
                                "message": {"message_id": 500, "chat": {"id": 600100, "type": "private"}},
                                "data": ord_cb_1,
                            },
                        },
                        allowed_user_ids=set(),
                        state=state,
                        inbox_path=inbox,
                        tg=tg,
                        aimo=aimo,
                    )
                    self.assertEqual(r3, "customer_order_placed")
                    self.assertEqual(placed_payloads[-1]["idempotency_key"], f"tg-ord-{nonce_1}")
                    placed_msg = [c[1] for c in tg_calls if c[0] == "sendMessage"][-1]
                    self.assertIn("ord_01SMALLPLACED", placed_msg["text"])
                    self.assertIn("PLACED", placed_msg["text"])

                    # 3. Tap `opt:<nonce>:1` -> large quote -> tap `ord:<nonce>` -> PENDING_APPROVAL + HELD_5H
                    r4 = await process_telegram_update(
                        {
                            "update_id": 404,
                            "callback_query": {
                                "id": "cb_opt_1",
                                "from": {"id": 600100, "username": "bistrochef"},
                                "message": {"message_id": 500, "chat": {"id": 600100, "type": "private"}},
                                "data": opt_cb_1,
                            },
                        },
                        allowed_user_ids=set(),
                        state=state,
                        inbox_path=inbox,
                        tg=tg,
                        aimo=aimo,
                    )
                    self.assertEqual(r4, "customer_chat_replied")
                    q2_msg = [c[1] for c in tg_calls if c[0] == "sendMessage"][-1]
                    ord_cb_2 = q2_msg["reply_markup"]["inline_keyboard"][0][0]["callback_data"]

                    r5 = await process_telegram_update(
                        {
                            "update_id": 405,
                            "callback_query": {
                                "id": "cb_ord_2",
                                "from": {"id": 600100, "username": "bistrochef"},
                                "message": {"message_id": 500, "chat": {"id": 600100, "type": "private"}},
                                "data": ord_cb_2,
                            },
                        },
                        allowed_user_ids=set(),
                        state=state,
                        inbox_path=inbox,
                        tg=tg,
                        aimo=aimo,
                    )
                    self.assertEqual(r5, "customer_order_placed")
                    held_msg = [c[1] for c in tg_calls if c[0] == "sendMessage"][-1]
                    self.assertIn("ord_01LARGEHELD", held_msg["text"])
                    self.assertIn("PENDING_APPROVAL", held_msg["text"])
                    self.assertIn("HELD_5H", held_msg["text"])
                    self.assertIn("5h", held_msg["text"])
                finally:
                    await tg.aclose()
                    await aimo.aclose()

        asyncio.run(run())

    def test_bridge_post_65s_timeout_and_read_timeout_friendly_fallback(self) -> None:
        self.assertEqual(BRIDGE_POST_TIMEOUT_SECONDS, 65.0)
        bot_token = "12345678:TEST_BRIDGE_BOT_TOK"
        tg_calls: list[tuple[str, dict[str, Any]]] = []
        seen_timeouts: dict[str, dict[str, Any]] = {}
        simulate_timeout = False

        def tg_handler(request: httpx.Request) -> httpx.Response:
            method = request.url.path.rsplit("/", 1)[-1]
            payload = json.loads(request.content.decode("utf-8"))
            tg_calls.append((method, payload))
            return httpx.Response(200, json={"ok": True, "result": {"message_id": 610}})

        def aimo_handler(request: httpx.Request) -> httpx.Response:
            seen_timeouts[request.url.path] = dict(request.extensions.get("timeout") or {})
            if simulate_timeout:
                raise httpx.ReadTimeout("Gemma 4 multi-step tool loop > 65s", request=request)
            if request.url.path == "/api/telegram/customer-chat":
                return httpx.Response(
                    200,
                    json={
                        "status": "ok",
                        "reply_text": "Quote ready!",
                        "quote": {"id": "qte_t65", "total_excl_vat_eur": 19.50},
                    },
                )
            if request.url.path == "/api/telegram/select-branch":
                return httpx.Response(
                    200,
                    json={
                        "active_branch": {"id": "brn_strand", "name": "Hilton Helsinki Strand"},
                    },
                )
            if request.url.path == "/api/telegram/place-quote":
                return httpx.Response(
                    201,
                    json={"id": "ord_t65", "status": "PLACED", "total_excl_vat_eur": 19.50},
                )
            return httpx.Response(404)

        async def run() -> None:
            nonlocal simulate_timeout
            with tempfile.TemporaryDirectory() as tmpdir:
                tmp = Path(tmpdir)
                inbox = tmp / "INFORMATIONSHARING.md"
                inbox.write_text("## Inbox\n", encoding="utf-8")
                state = TelegramStateStore(tmp / ".telegram_state.json")
                state.customer_chats["650001"] = {
                    "telegram_user_id": "650001",
                    "telegram_username": "strandchef",
                    "company_name": "Hotel Aurora Oy",
                }
                nonce = state.register_customer_order_nonce(
                    chat_id="650001",
                    user_id="650001",
                    quote_id="qte_t65",
                    total_eur=19.50,
                )
                tg = TelegramBotClient(bot_token, transport=httpx.MockTransport(tg_handler))
                aimo = AimoCoreApiClient(
                    "http://127.0.0.1:8001",
                    bot_token=bot_token,
                    transport=httpx.MockTransport(aimo_handler),
                )
                try:
                    # 1. Verify 65.0s per-request timeout on customer-chat, select-branch, and place-quote
                    await aimo.send_customer_chat(
                        telegram_user_id="650001",
                        telegram_chat_id="650001",
                        telegram_username="strandchef",
                        message="10 L milk",
                    )
                    await aimo.select_customer_branch(
                        telegram_user_id="650001",
                        telegram_chat_id="650001",
                        branch_id="brn_strand",
                    )
                    await aimo.place_customer_quote(
                        telegram_user_id="650001",
                        telegram_chat_id="650001",
                        quote_id="qte_t65",
                        idempotency_key="tg-ord-test65",
                    )
                    for path in (
                        "/api/telegram/customer-chat",
                        "/api/telegram/select-branch",
                        "/api/telegram/place-quote",
                    ):
                        self.assertEqual(seen_timeouts[path]["read"], 65.0)
                        self.assertEqual(seen_timeouts[path]["connect"], 65.0)

                    # 2. Simulate ReadTimeout across customer-chat, select-branch, and place-quote
                    simulate_timeout = True
                    r_chat = await process_telegram_update(
                        {
                            "update_id": 501,
                            "message": {
                                "message_id": 41,
                                "from": {"id": 650001, "username": "strandchef", "is_bot": False},
                                "chat": {"id": 650001, "type": "private"},
                                "text": "1,000 L lactose-free milk by Monday",
                            },
                        },
                        allowed_user_ids=set(),
                        state=state,
                        inbox_path=inbox,
                        tg=tg,
                        aimo=aimo,
                    )
                    self.assertEqual(r_chat, "customer_chat_timeout")
                    self.assertEqual(
                        [c[1] for c in tg_calls if c[0] == "sendMessage"][-1]["text"],
                        CUSTOMER_TIMEOUT_FALLBACK_MESSAGE,
                    )
                    self.assertIn("650001", state.customer_chats)

                    r_brn = await process_telegram_update(
                        {
                            "update_id": 502,
                            "callback_query": {
                                "id": "cb_brn_timeout",
                                "from": {"id": 650001, "username": "strandchef"},
                                "message": {"message_id": 610, "chat": {"id": 650001, "type": "private"}},
                                "data": "brn:brn_strand",
                            },
                        },
                        allowed_user_ids=set(),
                        state=state,
                        inbox_path=inbox,
                        tg=tg,
                        aimo=aimo,
                    )
                    self.assertEqual(r_brn, "branch_select_timeout")
                    self.assertEqual(
                        [c[1] for c in tg_calls if c[0] == "sendMessage"][-1]["text"],
                        CUSTOMER_TIMEOUT_FALLBACK_MESSAGE,
                    )

                    r_ord = await process_telegram_update(
                        {
                            "update_id": 503,
                            "callback_query": {
                                "id": "cb_ord_timeout",
                                "from": {"id": 650001, "username": "strandchef"},
                                "message": {"message_id": 610, "chat": {"id": 650001, "type": "private"}},
                                "data": f"ord:{nonce}",
                            },
                        },
                        allowed_user_ids=set(),
                        state=state,
                        inbox_path=inbox,
                        tg=tg,
                        aimo=aimo,
                    )
                    self.assertEqual(r_ord, "customer_order_timeout")
                    self.assertEqual(
                        [c[1] for c in tg_calls if c[0] == "sendMessage"][-1]["text"],
                        CUSTOMER_TIMEOUT_FALLBACK_MESSAGE,
                    )
                    self.assertIn(nonce, state.customer_order_nonces)
                finally:
                    await tg.aclose()
                    await aimo.aclose()

        asyncio.run(run())

    def test_unlink_calls_backend_api_telegram_unlink_and_tolerates_network_errors(self) -> None:
        bot_token = "12345678:TEST_BRIDGE_BOT_TOK"
        expected_secret = compute_bridge_secret(bot_token)
        tg_calls: list[tuple[str, dict[str, Any]]] = []
        unlink_calls: list[tuple[dict[str, Any], dict[str, str]]] = []
        opted_in_recipients: list[dict[str, Any]] = [
            {
                "user_id": "usr_demo",
                "telegram_username": "aurorachef",
                "telegram_chat_id": "555002",
                "enabled": True,
            }
        ]
        fail_unlink_network = False

        def tg_handler(request: httpx.Request) -> httpx.Response:
            method = request.url.path.rsplit("/", 1)[-1]
            payload = json.loads(request.content.decode("utf-8"))
            tg_calls.append((method, payload))
            return httpx.Response(200, json={"ok": True, "result": {"message_id": 710}})

        def aimo_handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content.decode("utf-8")) if request.content else {}
            headers = {k.lower(): v for k, v in request.headers.items()}
            if request.url.path == "/api/auth/telegram/recipients":
                return httpx.Response(200, json={"items": opted_in_recipients})
            if request.url.path == "/api/telegram/unlink":
                unlink_calls.append((body, headers))
                if fail_unlink_network:
                    raise httpx.ConnectError("Backend restarting", request=request)
                opted_in_recipients.clear()
                return httpx.Response(200, json={"ok": True, "enabled": False})
            return httpx.Response(404)

        async def run() -> None:
            nonlocal fail_unlink_network
            with tempfile.TemporaryDirectory() as tmpdir:
                tmp = Path(tmpdir)
                inbox = tmp / "INFORMATIONSHARING.md"
                inbox.write_text("## Inbox\n", encoding="utf-8")
                state = TelegramStateStore(tmp / ".telegram_state.json")
                state.customer_chats["555002"] = {
                    "telegram_user_id": "555002",
                    "telegram_username": "aurorachef",
                    "company_name": "Hotel Aurora Oy",
                }
                state.record_opted_in_chat(chat_id="555002", user_id="555002", username="aurorachef")
                tg = TelegramBotClient(bot_token, transport=httpx.MockTransport(tg_handler))
                aimo = AimoCoreApiClient(
                    "http://127.0.0.1:8001",
                    bearer_token="jwt_demo_approver",
                    bot_token=bot_token,
                    transport=httpx.MockTransport(aimo_handler),
                )
                try:
                    # 1. Customer `/unlink` calls POST /api/telegram/unlink with HMAC & flips backend enabled=False
                    r1 = await process_telegram_update(
                        {
                            "update_id": 601,
                            "message": {
                                "message_id": 51,
                                "from": {"id": 555002, "username": "aurorachef", "is_bot": False},
                                "chat": {"id": 555002, "type": "private"},
                                "text": "/unlink",
                            },
                        },
                        allowed_user_ids=set(),
                        state=state,
                        inbox_path=inbox,
                        tg=tg,
                        aimo=aimo,
                    )
                    self.assertEqual(r1, "customer_unlinked")
                    self.assertEqual(len(unlink_calls), 1)
                    self.assertEqual(
                        unlink_calls[0][0],
                        {"telegram_user_id": "555002", "telegram_chat_id": "555002"},
                    )
                    self.assertEqual(
                        unlink_calls[0][1].get("x-telegram-bridge-secret"),
                        expected_secret,
                    )
                    self.assertNotIn("555002", state.customer_chats)
                    self.assertNotIn("@aurorachef", state.verified_chats)
                    self.assertEqual(opted_in_recipients, [])

                    # Subsequent approval poll sends 0 cards to unlinked user
                    pushed = await poll_and_push_pending_approvals(state=state, tg=tg, aimo=aimo)
                    self.assertEqual(pushed, 0)

                    # 2. Even if POST /api/telegram/unlink hits a network error, operator `/unlink` completes cleanly
                    fail_unlink_network = True
                    state.customer_chats["10000001"] = {
                        "telegram_user_id": "10000001",
                        "telegram_username": "demo_operator",
                        "company_name": "Hotel Aurora Oy",
                    }
                    state.operator_mode = "customer"
                    r2 = await process_telegram_update(
                        {
                            "update_id": 602,
                            "message": {
                                "message_id": 52,
                                "from": {"id": 10000001, "username": "demo_operator", "is_bot": False},
                                "chat": {"id": 10000001, "type": "private"},
                                "text": "/unlink",
                            },
                        },
                        allowed_user_ids=set(),
                        state=state,
                        inbox_path=inbox,
                        tg=tg,
                        aimo=aimo,
                    )
                    self.assertEqual(r2, "customer_unlinked")
                    self.assertEqual(state.operator_mode, "lead")
                    self.assertNotIn("10000001", state.customer_chats)
                finally:
                    await tg.aclose()
                    await aimo.aclose()

        asyncio.run(run())

    def test_website_disconnect_prunes_stale_customer_chats_before_start_help_and_on_poll(
        self,
    ) -> None:
        bot_token = "12345678:TEST_BRIDGE_BOT_TOK"
        tg_calls: list[tuple[str, dict[str, Any]]] = []
        opted_in_recipients: list[dict[str, Any]] = [
            {
                "user_id": "usr_strand",
                "telegram_username": "chef_strand",
                "telegram_chat_id": "555003",
                "enabled": True,
            }
        ]

        def tg_handler(request: httpx.Request) -> httpx.Response:
            method = request.url.path.rsplit("/", 1)[-1]
            payload = json.loads(request.content.decode("utf-8"))
            tg_calls.append((method, payload))
            return httpx.Response(200, json={"ok": True, "result": {"message_id": 810}})

        def aimo_handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/auth/telegram/recipients":
                return httpx.Response(200, json={"items": opted_in_recipients})
            if request.url.path == "/api/auth/telegram/verify-chat":
                return httpx.Response(200, json={"ok": True})
            if request.url.path == "/api/telegram/customer-chat":
                return httpx.Response(404, json={"error": {"message": "Not linked"}})
            return httpx.Response(404)

        async def run() -> None:
            with tempfile.TemporaryDirectory() as tmpdir:
                tmp = Path(tmpdir)
                inbox = tmp / "INFORMATIONSHARING.md"
                inbox.write_text("## Inbox\n", encoding="utf-8")
                state = TelegramStateStore(tmp / ".telegram_state.json")
                state.customer_chats["555003"] = {
                    "telegram_user_id": "555003",
                    "telegram_username": "chef_strand",
                    "company_name": "Hotel Aurora Oy",
                    "active_branch": {"id": "brn_strand", "name": "Hilton Helsinki Strand"},
                }
                state.customer_chats["10000001"] = {
                    "telegram_user_id": "10000001",
                    "telegram_username": "demo_operator",
                    "company_name": "Hotel Aurora Oy",
                }
                tg = TelegramBotClient(bot_token, transport=httpx.MockTransport(tg_handler))
                aimo = AimoCoreApiClient(
                    "http://127.0.0.1:8001",
                    bearer_token="jwt_demo_approver",
                    bot_token=bot_token,
                    transport=httpx.MockTransport(aimo_handler),
                )
                aimo_unconfigured = AimoCoreApiClient(
                    "http://127.0.0.1:8001",
                    bot_token=bot_token,
                    transport=httpx.MockTransport(aimo_handler),
                )
                try:
                    # 1. While opted-in on website, `/help` replies with connected location
                    r_ok = await process_telegram_update(
                        {
                            "update_id": 701,
                            "message": {
                                "message_id": 61,
                                "from": {"id": 555003, "username": "chef_strand", "is_bot": False},
                                "chat": {"id": 555003, "type": "private"},
                                "text": "/help",
                            },
                        },
                        allowed_user_ids=set(),
                        state=state,
                        inbox_path=inbox,
                        tg=tg,
                        aimo=aimo,
                    )
                    self.assertEqual(r_ok, "help")
                    self.assertEqual(len([c for c in tg_calls if c[0] == "sendMessage"]), 1)
                    tg_calls.clear()

                    # 2. User clicks Website Disconnect -> opted_in_recipients becomes []
                    opted_in_recipients.clear()

                    # Even BEFORE poll_and_push_pending_approvals runs, `/start` & `/help` prune & silent-drop!
                    r_start = await process_telegram_update(
                        {
                            "update_id": 702,
                            "message": {
                                "message_id": 62,
                                "from": {"id": 555003, "username": "chef_strand", "is_bot": False},
                                "chat": {"id": 555003, "type": "private"},
                                "text": "/start",
                            },
                        },
                        allowed_user_ids=set(),
                        state=state,
                        inbox_path=inbox,
                        tg=tg,
                        aimo=aimo,
                    )
                    self.assertEqual(r_start, "unauthorized_silent_drop")
                    self.assertNotIn("555003", state.customer_chats)
                    # Operator session is preserved
                    self.assertIn("10000001", state.customer_chats)
                    self.assertEqual(len(tg_calls), 0)

                    # 3. Background recipients refresh in poll_and_push_pending_approvals also prunes stale chats
                    state.customer_chats["555003"] = {
                        "telegram_user_id": "555003",
                        "telegram_username": "chef_strand",
                        "company_name": "Hotel Aurora Oy",
                    }
                    await poll_and_push_pending_approvals(state=state, tg=tg, aimo=aimo)
                    self.assertNotIn("555003", state.customer_chats)
                    self.assertIn("10000001", state.customer_chats)

                    # 4. Token-only client (404 from backend) also prunes stale non-operator before `/start` reply
                    state.customer_chats["555004"] = {
                        "telegram_user_id": "555004",
                        "telegram_username": "stale_buyer",
                        "company_name": "Old Bistro",
                    }
                    r_404 = await process_telegram_update(
                        {
                            "update_id": 703,
                            "message": {
                                "message_id": 63,
                                "from": {"id": 555004, "username": "stale_buyer", "is_bot": False},
                                "chat": {"id": 555004, "type": "private"},
                                "text": "/start",
                            },
                        },
                        allowed_user_ids=set(),
                        state=state,
                        inbox_path=inbox,
                        tg=tg,
                        aimo=aimo_unconfigured,
                    )
                    self.assertEqual(r_404, "unauthorized_silent_drop")
                    self.assertNotIn("555004", state.customer_chats)
                    self.assertEqual(len(tg_calls), 0)
                finally:
                    await tg.aclose()
                    await aimo.aclose()
                    await aimo_unconfigured.aclose()

        asyncio.run(run())

    def test_batch_e_auto_executed_order_assurance_summary_and_quote_hint(self) -> None:
        bot_token = "12345678:TEST_BRIDGE_BOT_TOK"
        tg_calls: list[tuple[str, dict[str, Any]]] = []

        def tg_handler(request: httpx.Request) -> httpx.Response:
            method = request.url.path.rsplit("/", 1)[-1]
            payload = json.loads(request.content.decode("utf-8"))
            tg_calls.append((method, payload))
            return httpx.Response(200, json={"ok": True, "result": {"message_id": 910}})

        def aimo_handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content.decode("utf-8")) if request.content else {}
            if request.url.path == "/api/telegram/customer-chat":
                msg = body.get("message", "")
                if msg == "20 liters of milk":
                    return httpx.Response(
                        200,
                        json={
                            "status": "ok",
                            "reply_text": "Ordered **Nordic Dairy Lactose-Free Milk 1L** × 20 for tomorrow!",
                            "auto_executed": True,
                            "assurance_score": 62,
                            "assurance_threshold": 50,
                            "quote": {"id": "qte_auto_123", "total_excl_vat_eur": 20.20},
                            "order": {
                                "id": "ord_123",
                                "status": "PLACED",
                                "total_excl_vat_eur": 20.20,
                                "requested_delivery_date": "2026-11-16",
                            },
                        },
                    )
                return httpx.Response(
                    200,
                    json={
                        "status": "ok",
                        "reply_text": "Prepared **Nordic Dairy Lactose-Free Milk 1L** × 20 for tomorrow.",
                        "auto_executed": False,
                        "assurance_score": 62,
                        "assurance_threshold": 80,
                        "quote": {"id": "qte_confirm_80", "total_excl_vat_eur": 20.20},
                        "order": None,
                    },
                )
            if request.url.path == "/api/telegram/select-branch":
                return httpx.Response(
                    200,
                    json={
                        "active_branch": {"id": "brn_strand", "name": "Hilton Helsinki Strand"},
                        "auto_reply": {
                            "status": "ok",
                            "reply_text": "Large catering order routed for manager approval.",
                            "auto_executed": True,
                            "assurance_score": 88,
                            "assurance_threshold": 80,
                            "order": {
                                "id": "ord_456",
                                "status": "PENDING_APPROVAL",
                                "total_excl_vat_eur": 640.00,
                                "reservation_status": "HELD_5H",
                                "reservation_expires_at": "2026-11-14T23:30:00+02:00",
                            },
                        },
                    },
                )
            return httpx.Response(404)

        async def run() -> None:
            with tempfile.TemporaryDirectory() as tmpdir:
                tmp = Path(tmpdir)
                inbox = tmp / "INFORMATIONSHARING.md"
                inbox.write_text("## Inbox\n", encoding="utf-8")
                state_path = tmp / ".telegram_state.json"
                state = TelegramStateStore(state_path)
                state.customer_chats["600200"] = {
                    "telegram_user_id": "600200",
                    "telegram_username": "easychef",
                    "company_name": "Hotel Aurora Oy",
                }
                tg = TelegramBotClient(bot_token, transport=httpx.MockTransport(tg_handler))
                aimo = AimoCoreApiClient(
                    "http://127.0.0.1:8001",
                    bot_token=bot_token,
                    transport=httpx.MockTransport(aimo_handler),
                )
                try:
                    # 1. Auto-executed PLACED order -> assurance summary + `cnl:` Undo button (no `ord:` button)
                    r_auto = await process_telegram_update(
                        {
                            "update_id": 801,
                            "message": {
                                "message_id": 71,
                                "from": {"id": 600200, "username": "easychef", "is_bot": False},
                                "chat": {"id": 600200, "type": "private"},
                                "text": "20 liters of milk",
                            },
                        },
                        allowed_user_ids=set(),
                        state=state,
                        inbox_path=inbox,
                        tg=tg,
                        aimo=aimo,
                    )
                    self.assertEqual(r_auto, "customer_chat_replied")
                    sent_auto = [c[1] for c in tg_calls if c[0] == "sendMessage"][-1]
                    self.assertIn(
                        "🎯 Assurance 62/100 (threshold 50) · Auto-placed",
                        sent_auto["text"],
                    )
                    self.assertIn("Order ord_123 (PLACED) · €20.20 excl. VAT", sent_auto["text"])
                    btns_auto = [
                        b
                        for row in sent_auto["reply_markup"]["inline_keyboard"]
                        for b in row
                    ]
                    self.assertEqual(len(btns_auto), 1)
                    self.assertEqual(btns_auto[0]["text"], "↩️ Undo / Cancel Order")
                    cnl_cb = btns_auto[0]["callback_data"]
                    self.assertTrue(cnl_cb.startswith("cnl:"))
                    self.assertLessEqual(len(cnl_cb.encode("utf-8")), 64)
                    cnl_nonce = cnl_cb.split(":", 1)[1]
                    reloaded = TelegramStateStore(state_path)
                    self.assertEqual(reloaded.customer_cancel_nonces[cnl_nonce]["order_id"], "ord_123")
                    self.assertEqual(reloaded.customer_cancel_nonces[cnl_nonce]["chat_id"], "600200")
                    self.assertEqual(reloaded.customer_cancel_nonces[cnl_nonce]["user_id"], "600200")

                    # 2. Non-auto-executed quote with assurance_score -> subtle hint + `ord:<nonce>` button
                    r_quote = await process_telegram_update(
                        {
                            "update_id": 802,
                            "message": {
                                "message_id": 72,
                                "from": {"id": 600200, "username": "easychef", "is_bot": False},
                                "chat": {"id": 600200, "type": "private"},
                                "text": "quote 20 liters at strict threshold",
                            },
                        },
                        allowed_user_ids=set(),
                        state=state,
                        inbox_path=inbox,
                        tg=tg,
                        aimo=aimo,
                    )
                    self.assertEqual(r_quote, "customer_chat_replied")
                    sent_quote = [c[1] for c in tg_calls if c[0] == "sendMessage"][-1]
                    self.assertIn("Assurance 62/80 — tap Confirm to place", sent_quote["text"])
                    q_btn = sent_quote["reply_markup"]["inline_keyboard"][0][0]
                    self.assertTrue(q_btn["callback_data"].startswith("ord:"))

                    # 3. `select-branch` auto_reply returning PENDING_APPROVAL order -> assurance + 5h hold + `cnl:` button
                    r_brn = await process_telegram_update(
                        {
                            "update_id": 803,
                            "callback_query": {
                                "id": "cb_brn_auto_ord",
                                "from": {"id": 600200, "username": "easychef"},
                                "message": {"message_id": 910, "chat": {"id": 600200, "type": "private"}},
                                "data": "brn:brn_strand",
                            },
                        },
                        allowed_user_ids=set(),
                        state=state,
                        inbox_path=inbox,
                        tg=tg,
                        aimo=aimo,
                    )
                    self.assertEqual(r_brn, "branch_selected_auto_replied")
                    sent_brn = [c[1] for c in tg_calls if c[0] == "sendMessage"][-1]
                    self.assertIn("🎯 Assurance 88/100 (threshold 80) · Auto-placed", sent_brn["text"])
                    self.assertIn("Order ord_456 (PENDING_APPROVAL) · €640.00 excl. VAT", sent_brn["text"])
                    self.assertIn("HELD_5H", sent_brn["text"])
                    brn_btn = sent_brn["reply_markup"]["inline_keyboard"][0][0]
                    self.assertEqual(brn_btn["text"], "↩️ Undo / Cancel Order")
                    self.assertTrue(brn_btn["callback_data"].startswith("cnl:"))
                finally:
                    await tg.aclose()
                    await aimo.aclose()

        asyncio.run(run())

    def test_batch_e_cancel_callback_auth_hmac_409_conflict_and_timeout(self) -> None:
        bot_token = "12345678:TEST_BRIDGE_BOT_TOK"
        expected_secret = compute_bridge_secret(bot_token)
        tg_calls: list[tuple[str, dict[str, Any]]] = []
        cancel_calls: list[tuple[dict[str, Any], dict[str, str], dict[str, Any]]] = []
        cancel_mode = "ok"

        def tg_handler(request: httpx.Request) -> httpx.Response:
            method = request.url.path.rsplit("/", 1)[-1]
            payload = json.loads(request.content.decode("utf-8"))
            tg_calls.append((method, payload))
            return httpx.Response(200, json={"ok": True, "result": {"message_id": 950}})

        def aimo_handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content.decode("utf-8")) if request.content else {}
            headers = {k.lower(): v for k, v in request.headers.items()}
            timeouts = dict(request.extensions.get("timeout") or {})
            if request.url.path == "/api/telegram/cancel-order":
                cancel_calls.append((body, headers, timeouts))
                if cancel_mode == "timeout":
                    raise httpx.ReadTimeout("Cancel timed out", request=request)
                if cancel_mode == "conflict":
                    return httpx.Response(
                        409,
                        json={"error": {"message": "Past delivery closing_time cutoff"}},
                    )
                return httpx.Response(
                    200,
                    json={"order": {"id": body["order_id"], "status": "CANCELLED"}},
                )
            return httpx.Response(404)

        async def run() -> None:
            nonlocal cancel_mode
            with tempfile.TemporaryDirectory() as tmpdir:
                tmp = Path(tmpdir)
                inbox = tmp / "INFORMATIONSHARING.md"
                inbox.write_text("## Inbox\n", encoding="utf-8")
                state = TelegramStateStore(tmp / ".telegram_state.json")
                state.customer_chats["600200"] = {
                    "telegram_user_id": "600200",
                    "telegram_username": "easychef",
                    "company_name": "Hotel Aurora Oy",
                }
                state.customer_chats["600999"] = {
                    "telegram_user_id": "600999",
                    "telegram_username": "otherchef",
                    "company_name": "Other Bistro Oy",
                }
                cnl_nonce = state.register_customer_cancel_nonce(
                    order_id="ord_123",
                    chat_id="600200",
                    user_id="600200",
                )
                tg = TelegramBotClient(bot_token, transport=httpx.MockTransport(tg_handler))
                aimo = AimoCoreApiClient(
                    "http://127.0.0.1:8001",
                    bot_token=bot_token,
                    transport=httpx.MockTransport(aimo_handler),
                )
                try:
                    # 1. Unauthorized stranger clicking `cnl:<nonce>` -> silent drop (0 outbound messages, 0 API calls)
                    r_stranger = await process_telegram_update(
                        {
                            "update_id": 901,
                            "callback_query": {
                                "id": "cb_stranger_cnl",
                                "from": {"id": 999111, "username": "random_stranger"},
                                "message": {"message_id": 950, "chat": {"id": 999111, "type": "private"}},
                                "data": f"cnl:{cnl_nonce}",
                            },
                        },
                        allowed_user_ids=set(),
                        state=state,
                        inbox_path=inbox,
                        tg=tg,
                        aimo=aimo,
                    )
                    self.assertEqual(r_stranger, "unauthorized_callback")
                    self.assertEqual(len(tg_calls), 0)
                    self.assertEqual(len(cancel_calls), 0)

                    # 2. Different linked customer (`600999`) clicking `600200`'s `cnl:<nonce>` -> silent drop (0 outbound)
                    r_cross = await process_telegram_update(
                        {
                            "update_id": 902,
                            "callback_query": {
                                "id": "cb_cross_cnl",
                                "from": {"id": 600999, "username": "otherchef"},
                                "message": {"message_id": 950, "chat": {"id": 600999, "type": "private"}},
                                "data": f"cnl:{cnl_nonce}",
                            },
                        },
                        allowed_user_ids=set(),
                        state=state,
                        inbox_path=inbox,
                        tg=tg,
                        aimo=aimo,
                    )
                    self.assertEqual(r_cross, "unauthorized_callback")
                    self.assertEqual(len(tg_calls), 0)
                    self.assertEqual(len(cancel_calls), 0)

                    # 3. Authorized owner clicking `cnl:<nonce>` -> POST /api/telegram/cancel-order + HMAC + confirmation
                    r_ok = await process_telegram_update(
                        {
                            "update_id": 903,
                            "callback_query": {
                                "id": "cb_ok_cnl",
                                "from": {"id": 600200, "username": "easychef"},
                                "message": {"message_id": 950, "chat": {"id": 600200, "type": "private"}},
                                "data": f"cnl:{cnl_nonce}",
                            },
                        },
                        allowed_user_ids=set(),
                        state=state,
                        inbox_path=inbox,
                        tg=tg,
                        aimo=aimo,
                    )
                    self.assertEqual(r_ok, "customer_order_cancelled")
                    self.assertEqual(len(cancel_calls), 1)
                    req_body, req_headers, req_timeouts = cancel_calls[0]
                    self.assertEqual(
                        req_body,
                        {
                            "telegram_user_id": "600200",
                            "telegram_chat_id": "600200",
                            "order_id": "ord_123",
                        },
                    )
                    self.assertEqual(req_headers.get("x-telegram-bridge-secret"), expected_secret)
                    self.assertEqual(req_timeouts.get("read"), 65.0)
                    sent_ok = [c[1] for c in tg_calls if c[0] == "sendMessage"][-1]
                    self.assertEqual(sent_ok["text"], "↩️ Order ord_123 cancelled.")

                    # 4. 409 Conflict (already past cutoff) -> friendly warning message
                    cancel_mode = "conflict"
                    r_409 = await process_telegram_update(
                        {
                            "update_id": 904,
                            "callback_query": {
                                "id": "cb_409_cnl",
                                "from": {"id": 600200, "username": "easychef"},
                                "message": {"message_id": 950, "chat": {"id": 600200, "type": "private"}},
                                "data": f"cnl:{cnl_nonce}",
                            },
                        },
                        allowed_user_ids=set(),
                        state=state,
                        inbox_path=inbox,
                        tg=tg,
                        aimo=aimo,
                    )
                    self.assertEqual(r_409, "customer_order_cancel_conflict")
                    sent_409 = [c[1] for c in tg_calls if c[0] == "sendMessage"][-1]
                    self.assertIn("Order ord_123 can no longer be cancelled", sent_409["text"])
                    self.assertIn("Past delivery closing_time cutoff", sent_409["text"])

                    # 5. ReadTimeout -> CUSTOMER_TIMEOUT_FALLBACK_MESSAGE
                    cancel_mode = "timeout"
                    r_timeout = await process_telegram_update(
                        {
                            "update_id": 905,
                            "callback_query": {
                                "id": "cb_timeout_cnl",
                                "from": {"id": 600200, "username": "easychef"},
                                "message": {"message_id": 950, "chat": {"id": 600200, "type": "private"}},
                                "data": f"cnl:{cnl_nonce}",
                            },
                        },
                        allowed_user_ids=set(),
                        state=state,
                        inbox_path=inbox,
                        tg=tg,
                        aimo=aimo,
                    )
                    self.assertEqual(r_timeout, "customer_order_cancel_timeout")
                    sent_to = [c[1] for c in tg_calls if c[0] == "sendMessage"][-1]
                    self.assertEqual(sent_to["text"], CUSTOMER_TIMEOUT_FALLBACK_MESSAGE)
                finally:
                    await tg.aclose()
                    await aimo.aclose()

        asyncio.run(run())

    def test_operator_login_command_sends_persona_buttons_without_inbox_write(self) -> None:
        bot_token = "123456:TEST_LOGIN_MENU_TOKEN"
        tg_calls: list[tuple[str, dict[str, Any]]] = []

        def tg_handler(request: httpx.Request) -> httpx.Response:
            method = request.url.path.rsplit("/", 1)[-1]
            payload = json.loads(request.content.decode("utf-8")) if request.content else {}
            tg_calls.append((method, payload))
            return httpx.Response(200, json={"ok": True, "result": {"message_id": 1001}})

        def aimo_handler(request: httpx.Request) -> httpx.Response:
            raise AssertionError(f"Unexpected backend call on /login menu: {request.url.path}")

        async def run() -> None:
            with tempfile.TemporaryDirectory() as tmpdir:
                tmp = Path(tmpdir)
                inbox = tmp / "INFORMATIONSHARING.md"
                inbox.write_text("## Inbox\n", encoding="utf-8")
                state = TelegramStateStore(tmp / ".telegram_state.json")
                tg = TelegramBotClient(bot_token, transport=httpx.MockTransport(tg_handler))
                aimo = AimoCoreApiClient(
                    "http://127.0.0.1:8001",
                    bot_token=bot_token,
                    transport=httpx.MockTransport(aimo_handler),
                )
                try:
                    res = await process_telegram_update(
                        {
                            "update_id": 1001,
                            "message": {
                                "message_id": 67,
                                "from": {"id": 10000001, "username": "demo_operator", "is_bot": False},
                                "chat": {"id": 10000001, "type": "private"},
                                "text": "/login",
                            },
                        },
                        allowed_user_ids=set(),
                        state=state,
                        inbox_path=inbox,
                        tg=tg,
                        aimo=aimo,
                    )
                    self.assertEqual(res, "operator_login_menu")
                    self.assertEqual(inbox.read_text(encoding="utf-8"), "## Inbox\n")
                    sent = [c[1] for c in tg_calls if c[0] == "sendMessage"]
                    self.assertEqual(len(sent), 1)
                    rows = sent[0]["reply_markup"]["inline_keyboard"]
                    self.assertTrue(all(1 <= len(row) <= 2 for row in rows))
                    btns = [btn for row in rows for btn in row]
                    cbs = [btn["callback_data"] for btn in btns]
                    self.assertEqual(
                        cbs,
                        [
                            "lgn:anna",
                            "lgn:mikko",
                            "lgn:sari",
                        ],
                    )
                    self.assertTrue(all(len(cb.encode("utf-8")) <= 64 for cb in cbs))
                    btn_by_cb = {btn["callback_data"]: btn["text"] for btn in btns}
                    self.assertEqual(btn_by_cb["lgn:anna"], "🏨 Anna · Hotel Aurora (Hilton)")
                    self.assertEqual(btn_by_cb["lgn:mikko"], "🍽 Mikko · Bistro Satama")
                    self.assertEqual(btn_by_cb["lgn:sari"], "🎪 Sari · Nordic Campus Catering")

                    # Non-operator `/login` -> 100% silent drop (0 extra sendMessage calls)
                    unauth_login = await process_telegram_update(
                        {
                            "update_id": 1002,
                            "message": {
                                "message_id": 68,
                                "from": {"id": 999888, "username": "stranger", "is_bot": False},
                                "chat": {"id": 999888, "type": "private"},
                                "text": "/login",
                            },
                        },
                        allowed_user_ids=set(),
                        state=state,
                        inbox_path=inbox,
                        tg=tg,
                        aimo=aimo,
                    )
                    self.assertEqual(unauth_login, "unauthorized_silent_drop")
                    unauth_cb = await process_telegram_update(
                        {
                            "update_id": 1003,
                            "callback_query": {
                                "id": "cb_stranger_lgn",
                                "from": {"id": 999888, "username": "stranger"},
                                "message": {
                                    "message_id": 68,
                                    "chat": {"id": 999888, "type": "private"},
                                },
                                "data": "lgn:anna",
                            },
                        },
                        allowed_user_ids=set(),
                        state=state,
                        inbox_path=inbox,
                        tg=tg,
                        aimo=aimo,
                    )
                    self.assertEqual(unauth_cb, "unauthorized_callback")
                    self.assertEqual(len([c for c in tg_calls if c[0] == "sendMessage"]), 1)
                finally:
                    await tg.aclose()
                    await aimo.aclose()

        asyncio.run(run())

    def test_operator_login_persona_callback_and_credentials_mint_and_claim_link(self) -> None:
        bot_token = "123456:TEST_LOGIN_FLOW_TOKEN"
        tg_calls: list[tuple[str, dict[str, Any]]] = []
        aimo_calls: list[tuple[str, dict[str, Any], dict[str, str]]] = []

        def tg_handler(request: httpx.Request) -> httpx.Response:
            method = request.url.path.rsplit("/", 1)[-1]
            payload = json.loads(request.content.decode("utf-8")) if request.content else {}
            tg_calls.append((method, payload))
            return httpx.Response(200, json={"ok": True, "result": {"message_id": 1002}})

        def aimo_handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content.decode("utf-8")) if request.content else {}
            aimo_calls.append((request.url.path, body, dict(request.headers)))
            if request.url.path == "/api/auth/login":
                if body.get("email") == "anna.korhonen@hotel.fi" and body.get("password") == "demo1234":
                    return httpx.Response(200, json={"token": "jwt_anna_test"})
                return httpx.Response(401, json={"detail": "Invalid credentials"})
            if request.url.path == "/api/auth/telegram/link-token":
                self.assertEqual(request.headers.get("authorization"), "Bearer jwt_anna_test")
                return httpx.Response(200, json={"token": "minted_tok_anna"})
            if request.url.path == "/api/telegram/link-claim":
                self.assertEqual(body.get("token"), "minted_tok_anna")
                return httpx.Response(
                    200,
                    json={
                        "user_name": "Anna Korhonen",
                        "user_email": "anna.korhonen@hotel.fi",
                        "company_name": "Hotel Aurora Oy",
                        "branches": [
                            {
                                "id": "brn_strand",
                                "name": "Hilton Helsinki Strand",
                                "short_name": "Hilton Strand",
                            },
                            {
                                "id": "brn_kalastaja",
                                "name": "Hilton Helsinki Kalastajantorppa",
                                "short_name": "Hilton Kalastajantorppa",
                            },
                        ],
                        "active_branch": None,
                        "needs_branch_selection": True,
                    },
                )
            return httpx.Response(404)

        async def run() -> None:
            with tempfile.TemporaryDirectory() as tmpdir:
                tmp = Path(tmpdir)
                inbox = tmp / "INFORMATIONSHARING.md"
                inbox.write_text("## Inbox\n", encoding="utf-8")
                state = TelegramStateStore(tmp / ".telegram_state.json")
                tg = TelegramBotClient(bot_token, transport=httpx.MockTransport(tg_handler))
                aimo = AimoCoreApiClient(
                    "http://127.0.0.1:8001",
                    bot_token=bot_token,
                    transport=httpx.MockTransport(aimo_handler),
                )
                try:
                    with patch("scripts.telegram_bridge.DEFAULT_DEMO_CREDS_PATH", tmp / "missing_creds"):
                        r_cb = await process_telegram_update(
                            {
                                "update_id": 1002,
                                "callback_query": {
                                    "id": "cb_lgn_anna",
                                    "from": {"id": 10000001, "username": "demo_operator"},
                                    "message": {
                                        "message_id": 68,
                                        "chat": {"id": 10000001, "type": "private"},
                                    },
                                    "data": "lgn:anna",
                                },
                            },
                            allowed_user_ids=set(),
                            state=state,
                            inbox_path=inbox,
                            tg=tg,
                            aimo=aimo,
                        )
                    self.assertEqual(r_cb, "customer_linked")
                    self.assertEqual(state.operator_mode, "customer")
                    self.assertEqual(
                        [c[0] for c in aimo_calls],
                        [
                            "/api/auth/login",
                            "/api/auth/telegram/link-token",
                            "/api/telegram/link-claim",
                        ],
                    )
                    msg_cb = [c[1] for c in tg_calls if c[0] == "sendMessage"][-1]
                    cbs_cb = [
                        btn["callback_data"]
                        for row in msg_cb["reply_markup"]["inline_keyboard"]
                        for btn in row
                    ]
                    self.assertEqual(cbs_cb, ["brn:brn_strand", "brn:brn_kalastaja", "mode:lead"])

                    # Reset to lead & test explicit `/login <email> <password>` (never writes password to inbox)
                    state.operator_mode = "lead"
                    aimo_calls.clear()
                    r_txt = await process_telegram_update(
                        {
                            "update_id": 1003,
                            "message": {
                                "message_id": 69,
                                "from": {"id": 10000001, "username": "demo_operator", "is_bot": False},
                                "chat": {"id": 10000001, "type": "private"},
                                "text": "/login anna.korhonen@hotel.fi demo1234",
                            },
                        },
                        allowed_user_ids=set(),
                        state=state,
                        inbox_path=inbox,
                        tg=tg,
                        aimo=aimo,
                    )
                    self.assertEqual(r_txt, "customer_linked")
                    self.assertEqual(state.operator_mode, "customer")
                    self.assertEqual(
                        [c[0] for c in aimo_calls],
                        [
                            "/api/auth/login",
                            "/api/auth/telegram/link-token",
                            "/api/telegram/link-claim",
                        ],
                    )
                    self.assertEqual(inbox.read_text(encoding="utf-8"), "## Inbox\n")

                    # Already-linked non-operator customer `/login` shows connected company + /branch + /unlink
                    state.customer_chats["880011"] = {
                        "telegram_user_id": "880011",
                        "telegram_username": "linkedchef",
                        "company_name": "Bistro Satama Oy",
                        "branches": [],
                        "active_branch": {"id": "brn_satama", "name": "Bistro Satama Kitchen"},
                    }
                    r_cust = await process_telegram_update(
                        {
                            "update_id": 1004,
                            "message": {
                                "message_id": 70,
                                "from": {"id": 880011, "username": "linkedchef", "is_bot": False},
                                "chat": {"id": 880011, "type": "private"},
                                "text": "/login",
                            },
                        },
                        allowed_user_ids=set(),
                        state=state,
                        inbox_path=inbox,
                        tg=tg,
                        aimo=aimo,
                    )
                    self.assertEqual(r_cust, "customer_already_linked")
                    cust_msg = [c[1] for c in tg_calls if c[0] == "sendMessage"][-1]["text"]
                    self.assertIn("Bistro Satama Oy", cust_msg)
                    self.assertIn("/branch", cust_msg)
                    self.assertIn("/unlink", cust_msg)
                finally:
                    await tg.aclose()
                    await aimo.aclose()

        asyncio.run(run())

    def test_stranger_login_and_lgn_callback_silently_dropped_with_zero_outbound(self) -> None:
        bot_token = "123456:TEST_STRANGER_LOCK_TOKEN"
        tg_calls: list[tuple[str, dict[str, Any]]] = []
        aimo_calls: list[str] = []

        def tg_handler(request: httpx.Request) -> httpx.Response:
            method = request.url.path.rsplit("/", 1)[-1]
            payload = json.loads(request.content.decode("utf-8")) if request.content else {}
            tg_calls.append((method, payload))
            return httpx.Response(200, json={"ok": True, "result": True})

        def aimo_handler(request: httpx.Request) -> httpx.Response:
            aimo_calls.append(request.url.path)
            if request.url.path == "/api/auth/telegram/recipients":
                return httpx.Response(200, json={"items": []})
            raise AssertionError(f"Stranger triggered forbidden backend call: {request.url.path}")

        async def run() -> None:
            with tempfile.TemporaryDirectory() as tmpdir:
                tmp = Path(tmpdir)
                inbox = tmp / "INFORMATIONSHARING.md"
                inbox.write_text("## Inbox\n", encoding="utf-8")
                state = TelegramStateStore(tmp / ".telegram_state.json")
                tg = TelegramBotClient(bot_token, transport=httpx.MockTransport(tg_handler))
                aimo = AimoCoreApiClient(
                    "http://127.0.0.1:8001",
                    bot_token=bot_token,
                    transport=httpx.MockTransport(aimo_handler),
                )
                try:
                    r_msg = await process_telegram_update(
                        {
                            "update_id": 1005,
                            "message": {
                                "message_id": 71,
                                "from": {"id": 999111, "username": "random_stranger", "is_bot": False},
                                "chat": {"id": 999111, "type": "private"},
                                "text": "/login anna.korhonen@hotel.fi demo1234",
                            },
                        },
                        allowed_user_ids=set(),
                        state=state,
                        inbox_path=inbox,
                        tg=tg,
                        aimo=aimo,
                    )
                    self.assertEqual(r_msg, "unauthorized_silent_drop")
                    self.assertEqual(tg_calls, [])
                    self.assertNotIn("/api/auth/login", aimo_calls)

                    r_cb = await process_telegram_update(
                        {
                            "update_id": 1006,
                            "callback_query": {
                                "id": "cb_stranger_lgn",
                                "from": {"id": 999111, "username": "random_stranger"},
                                "message": {
                                    "message_id": 72,
                                    "chat": {"id": 999111, "type": "private"},
                                },
                                "data": "lgn:anna",
                            },
                        },
                        allowed_user_ids=set(),
                        state=state,
                        inbox_path=inbox,
                        tg=tg,
                        aimo=aimo,
                    )
                    self.assertEqual(r_cb, "unauthorized_callback")
                    self.assertEqual(tg_calls, [])
                    self.assertNotIn("/api/auth/login", aimo_calls)
                    self.assertEqual(inbox.read_text(encoding="utf-8"), "## Inbox\n")

                    # When OPERATOR_USERNAME / OPERATOR_USER_ID are unset/empty, operator /login is disabled
                    # by default, while connected website QR customer sessions continue to work cleanly.
                    with patch.multiple(
                        "scripts.telegram_bridge",
                        OPERATOR_USERNAME="",
                        OPERATOR_USER_ID="",
                    ):
                        self.assertFalse(
                            is_verified_operator(
                                username="demo_operator",
                                user_id="10000001",
                                chat_type="private",
                                allowed_user_ids=set(),
                            )
                        )
                        self.assertFalse(
                            is_verified_operator(
                                username="",
                                user_id="",
                                chat_type="private",
                                allowed_user_ids=set(),
                            )
                        )
                        r_unset_op = await process_telegram_update(
                            {
                                "update_id": 1007,
                                "message": {
                                    "message_id": 73,
                                    "from": {"id": 10000001, "username": "demo_operator", "is_bot": False},
                                    "chat": {"id": 10000001, "type": "private"},
                                    "text": "/login",
                                },
                            },
                            allowed_user_ids=set(),
                            state=state,
                            inbox_path=inbox,
                            tg=tg,
                            aimo=aimo,
                        )
                        self.assertEqual(r_unset_op, "unauthorized_silent_drop")
                        self.assertEqual(tg_calls, [])

                        state.customer_chats["555777"] = {
                            "telegram_user_id": "555777",
                            "telegram_username": "",
                            "company_name": "Hotel Aurora Oy",
                            "branches": [
                                {"id": "brn_1", "name": "Hotel Aurora Kitchen", "short_name": "Aurora"}
                            ],
                            "active_branch": {"id": "brn_1", "name": "Hotel Aurora Kitchen"},
                        }
                        r_connected_qr = await process_telegram_update(
                            {
                                "update_id": 1008,
                                "message": {
                                    "message_id": 74,
                                    "from": {"id": 555777, "username": "", "is_bot": False},
                                    "chat": {"id": 555777, "type": "private"},
                                    "text": "/branches",
                                },
                            },
                            allowed_user_ids=set(),
                            state=state,
                            inbox_path=inbox,
                            tg=tg,
                            aimo=aimo,
                        )
                        self.assertEqual(r_connected_qr, "branch_menu")
                        self.assertEqual(len([c for c in tg_calls if c[0] == "sendMessage"]), 1)
                finally:
                    await tg.aclose()
                    await aimo.aclose()

        asyncio.run(run())

    def test_multi_company_order_notifications_and_bridge_decide_order_with_stranger_guard(
        self,
    ) -> None:
        bot_token = "123456:TEST_MULTICOMPANY_NOTIF_TOKEN"
        expected_secret = compute_bridge_secret(bot_token)
        full_hash = "c" * 64
        tg_calls: list[tuple[str, dict[str, Any]]] = []
        decide_calls: list[dict[str, Any]] = []

        def tg_handler(request: httpx.Request) -> httpx.Response:
            method = request.url.path.rsplit("/", 1)[-1]
            payload = json.loads(request.content.decode("utf-8")) if request.content else {}
            tg_calls.append((method, payload))
            if method == "getChat":
                cid = str(payload.get("chat_id") or "")
                if cid == "550011":
                    return httpx.Response(
                        200,
                        json={
                            "ok": True,
                            "result": {"id": 550011, "type": "private", "username": "mikkochef"},
                        },
                    )
                if cid == "660022":
                    return httpx.Response(
                        200,
                        json={
                            "ok": True,
                            "result": {"id": 660022, "type": "private", "username": "sarifest"},
                        },
                    )
                return httpx.Response(
                    200,
                    json={"ok": True, "result": {"id": -100999, "type": "supergroup"}},
                )
            return httpx.Response(200, json={"ok": True, "result": {"message_id": 88}})

        def aimo_handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/telegram/order-notifications":
                self.assertEqual(request.headers.get("X-Telegram-Bridge-Secret"), expected_secret)
                return httpx.Response(
                    200,
                    json={
                        "server_time": "2026-11-15T03:15:00+02:00",
                        "items": [
                            {
                                "notification_id": "ord_notif:550011:ord_bistro_1:ORDER_CONFIRMED",
                                "event_type": "ORDER_CONFIRMED",
                                "event_at": "2026-11-15T03:14:50+02:00",
                                "telegram_chat_id": "550011",
                                "telegram_user_id": "550011",
                                "user_id": "usr_mikko",
                                "user_name": "Mikko Laine",
                                "company_id": "cmp_bistro",
                                "company_name": "Bistro Satama Oy",
                                "origin_channel": "web",
                                "placed_in_same_telegram_chat": False,
                                "can_undo_in_telegram": True,
                                "summary_text": (
                                    "✅ Order confirmed for Bistro Satama Oy: ord_bistro_1 "
                                    "(€124.50 excl. VAT, 1 line(s), delivery 2026-11-18, ref AIMO-901)."
                                ),
                                "order": {
                                    "id": "ord_bistro_1",
                                    "status": "PLACED",
                                    "content_hash": "b" * 64,
                                    "total_excl_vat_eur": 124.50,
                                    "requested_delivery_date": "2026-11-18",
                                    "gateway_reference": "AIMO-901",
                                    "lines": [
                                        {
                                            "product_id": "400123",
                                            "product_name": "Nordic Whipping Cream L",
                                            "quantity": 6.0,
                                            "delivery_status": "ON_TIME",
                                        }
                                    ],
                                },
                            },
                            {
                                "notification_id": "ord_notif:660022:ord_fest_2:ORDER_PENDING_APPROVAL",
                                "event_type": "ORDER_PENDING_APPROVAL",
                                "event_at": "2026-11-15T03:14:55+02:00",
                                "telegram_chat_id": "660022",
                                "telegram_user_id": "660022",
                                "user_id": "usr_sari",
                                "user_name": "Sari Virtanen",
                                "company_id": "cmp_nordic",
                                "company_name": "Nordic Campus Catering Oy",
                                "origin_channel": "mcp",
                                "placed_in_same_telegram_chat": False,
                                "can_undo_in_telegram": True,
                                "summary_text": "⏳ Approval needed for Nordic Campus Catering Oy: ord_fest_2",
                                "order": {
                                    "id": "ord_fest_2",
                                    "status": "PENDING_APPROVAL",
                                    "content_hash": full_hash,
                                    "total_excl_vat_eur": 980.00,
                                    "requested_delivery_date": "2026-11-18",
                                    "lines": [
                                        {
                                            "product_id": "400456",
                                            "product_name": "Nordic Quark L",
                                            "quantity": 40.0,
                                            "delivery_status": "ON_TIME",
                                        }
                                    ],
                                },
                            },
                            {
                                "notification_id": "ord_notif:-100999:ord_spoof:ORDER_CONFIRMED",
                                "event_type": "ORDER_CONFIRMED",
                                "event_at": "2026-11-15T03:14:59+02:00",
                                "telegram_chat_id": "-100999",
                                "telegram_user_id": "999000",
                                "user_id": "usr_spoof",
                                "user_name": "Spoof",
                                "company_id": "cmp_spoof",
                                "company_name": "Spoof Group",
                                "origin_channel": "web",
                                "placed_in_same_telegram_chat": False,
                                "can_undo_in_telegram": False,
                                "summary_text": "Should never send to non-private chat",
                                "order": {
                                    "id": "ord_spoof",
                                    "status": "PLACED",
                                    "content_hash": "d" * 64,
                                    "total_excl_vat_eur": 10.0,
                                    "lines": [],
                                },
                            },
                        ],
                    },
                )
            if request.url.path == "/api/telegram/connected-sessions":
                self.assertEqual(request.headers.get("X-Telegram-Bridge-Secret"), expected_secret)
                return httpx.Response(
                    200,
                    json={
                        "items": [
                            {
                                "user_id": "usr_sari",
                                "user_name": "Sari Virtanen",
                                "user_email": "sari.virtanen@festival.fi",
                                "company_id": "cmp_nordic",
                                "company_name": "Nordic Campus Catering Oy",
                                "telegram_chat_id": "660022",
                                "telegram_user_id": "660022",
                                "telegram_username": "sarifest",
                            }
                        ]
                    },
                )
            if request.url.path == "/api/telegram/decide-order":
                self.assertEqual(request.headers.get("X-Telegram-Bridge-Secret"), expected_secret)
                body = json.loads(request.content.decode("utf-8"))
                decide_calls.append(body)
                return httpx.Response(
                    200,
                    json={
                        "decision": body["decision"],
                        "order": {
                            "id": body["order_id"],
                            "status": "PLACED",
                            "gateway_reference": "AIMO-FEST-77",
                            "content_hash": full_hash,
                        },
                    },
                )
            raise AssertionError(f"Unexpected backend call: {request.url.path}")

        async def run() -> None:
            with tempfile.TemporaryDirectory() as tmpdir:
                tmp = Path(tmpdir)
                inbox = tmp / "INFORMATIONSHARING.md"
                inbox.write_text("## Inbox\n", encoding="utf-8")
                state = TelegramStateStore(tmp / ".telegram_state.json")
                state.order_notifications_since = "2026-11-15T03:10:00+02:00"
                tg = TelegramBotClient(bot_token, transport=httpx.MockTransport(tg_handler))
                aimo = AimoCoreApiClient(
                    "http://127.0.0.1:8001",
                    bot_token=bot_token,
                    transport=httpx.MockTransport(aimo_handler),
                )
                try:
                    pushed = await poll_and_push_order_notifications(
                        state=state,
                        tg=tg,
                        aimo=aimo,
                    )
                    self.assertEqual(pushed, 2)
                    self.assertEqual(state.order_notifications_since, "2026-11-15T03:15:00+02:00")
                    sent = [c[1] for c in tg_calls if c[0] == "sendMessage"]
                    self.assertEqual(len(sent), 2)
                    self.assertEqual(str(sent[0]["chat_id"]), "550011")
                    self.assertIn("Bistro Satama Oy", sent[0]["text"])
                    self.assertIn("• 6 × Nordic Whipping Cream L (400123) — On Time", sent[0]["text"])
                    self.assertTrue(
                        sent[0]["reply_markup"]["inline_keyboard"][0][0]["callback_data"].startswith(
                            "cnl:"
                        )
                    )
                    self.assertEqual(str(sent[1]["chat_id"]), "660022")
                    self.assertIn("Nordic Campus Catering Oy", sent[1]["text"])
                    apr_cb = sent[1]["reply_markup"]["inline_keyboard"][0][0]["callback_data"]
                    self.assertTrue(apr_cb.startswith("apr:"))

                    # Dedup: second poll pushes 0 duplicates
                    pushed_again = await poll_and_push_order_notifications(
                        state=state,
                        tg=tg,
                        aimo=aimo,
                    )
                    self.assertEqual(pushed_again, 0)

                    # Stranger attempting to click `apr:<nonce>` is silently rejected
                    tg_calls.clear()
                    r_stranger = await process_telegram_update(
                        {
                            "update_id": 2001,
                            "callback_query": {
                                "id": "cb_stranger_apr",
                                "from": {"id": 999888, "username": "intruder"},
                                "message": {
                                    "message_id": 88,
                                    "chat": {"id": 999888, "type": "private"},
                                },
                                "data": apr_cb,
                            },
                        },
                        allowed_user_ids=set(),
                        state=state,
                        inbox_path=inbox,
                        tg=tg,
                        aimo=aimo,
                    )
                    self.assertEqual(r_stranger, "unauthorized_callback")
                    self.assertEqual(tg_calls, [])
                    self.assertEqual(decide_calls, [])

                    # Connected Nordic Campus user approves via `/api/telegram/decide-order`
                    r_apr = await process_telegram_update(
                        {
                            "update_id": 2002,
                            "callback_query": {
                                "id": "cb_sari_apr",
                                "from": {"id": 660022, "username": "sarifest"},
                                "message": {
                                    "message_id": 88,
                                    "chat": {"id": 660022, "type": "private"},
                                },
                                "data": apr_cb,
                            },
                        },
                        allowed_user_ids=set(),
                        state=state,
                        inbox_path=inbox,
                        tg=tg,
                        aimo=aimo,
                    )
                    self.assertEqual(r_apr, "approved")
                    self.assertEqual(len(decide_calls), 1)
                    self.assertEqual(decide_calls[0]["order_id"], "ord_fest_2")
                    self.assertEqual(decide_calls[0]["decision"], "approve")
                    self.assertEqual(decide_calls[0]["content_hash"], full_hash)
                    edited = [c[1] for c in tg_calls if c[0] == "editMessageText"]
                    self.assertEqual(len(edited), 1)
                    self.assertIn("AIMO-FEST-77", edited[0]["text"])
                finally:
                    await tg.aclose()
                    await aimo.aclose()

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
