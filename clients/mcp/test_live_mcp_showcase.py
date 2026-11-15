"""Offline unit tests for live_mcp_showcase.py (zero network calls)."""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import pathlib
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import live_mcp_showcase  # noqa: E402
from live_mcp_showcase import (  # noqa: E402
    SecretRedactor,
    ShowcaseError,
    compute_next_monday_helsinki,
    load_showcase_config,
    run_showcase,
)


FAKE_SECRET = "aks_super_secret_value_for_testing_98765"
FAKE_JWT = (
    "eyJ"
    + "hbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    + "eyJzdWIiOiJhZ3RfZGVtbzAxIiwiYXVkIjoiYWltb2Fubm9zLWFwaSJ9."
    + "mockSignatureTokenValue123456"
)


class _MockState:
    def __init__(self, *, echo_secret_in_error: bool = False) -> None:
        self.echo_secret_in_error = echo_secret_in_error
        self.mcp_calls: list[tuple[str, dict]] = []
        self.rest_calls: list[tuple[str, str, dict | None]] = []
        self.protocol_versions: list[str | None] = []
        self.placed_quotes: list[str] = []


def _make_handler(state: _MockState):
    class MockHandler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args) -> None:  # noqa: A002
            return

        def _send_json(self, status: int, body: dict, *, headers: dict[str, str] | None = None) -> None:
            raw = json.dumps(body).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("X-Server-Time", "2026-11-14T10:15:00+02:00")
            self.send_header("X-Request-ID", "req_mock_01")
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(raw)

        def _read_json(self) -> dict:
            length = int(self.headers.get("Content-Length") or "0")
            raw = self.rfile.read(length) if length > 0 else b"{}"
            return json.loads(raw.decode("utf-8"))

        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/api/health":
                auth = self.headers.get("Authorization")
                if auth != f"Bearer {FAKE_JWT}":
                    self._send_json(
                        401,
                        {
                            "error": {
                                "code": "JUDGE_ACCESS_REQUIRED",
                                "message": "Judge access password required",
                            }
                        },
                    )
                    return
                state.rest_calls.append(("GET", self.path, None))
                self._send_json(
                    200,
                    {
                        "status": "ok",
                        "server_time": "2026-11-14T10:15:00+02:00",
                    },
                )
                return
            self._send_json(404, {"error": {"code": "NOT_FOUND", "message": "not found"}})

        def do_POST(self) -> None:  # noqa: N802
            body = self._read_json()

            if self.path == "/api/agent-auth/token":
                state.rest_calls.append(("POST", self.path, body))
                if state.echo_secret_in_error:
                    self._send_json(
                        401,
                        {
                            "error": {
                                "code": "UNAUTHORIZED",
                                "message": f"Rejected secret {FAKE_SECRET} and Bearer {FAKE_JWT}",
                            }
                        },
                    )
                    return
                self._send_json(
                    200,
                    {
                        "token": FAKE_JWT,
                        "expires_at": "2099-01-01T00:00:00Z",
                        "agent": {"id": body.get("agent_id")},
                    },
                )
                return

            if self.path == "/api/products/substitutes":
                auth = self.headers.get("Authorization")
                if auth != f"Bearer {FAKE_JWT}":
                    self._send_json(401, {"error": {"code": "UNAUTHORIZED", "message": "bad jwt"}})
                    return
                state.rest_calls.append(("POST", self.path, body))
                self._send_json(
                    200,
                    {
                        "requested_product_id": body["product_id"],
                        "diet": body.get("diet"),
                        "items": [
                            {
                                "product": {
                                    "id": "464306",
                                    "name": "Valio Maitojuoma laktoositon 1L",
                                },
                                "quote_line": {
                                    "product_id": "464306",
                                    "product_name": "Valio Maitojuoma laktoositon 1L",
                                    "quantity": 1000,
                                    "sales_unit": "ST",
                                    "delivery_status": "ON_TIME",
                                    "available_amount": 9999999999.0,
                                    "co2_kg": 1100.0,
                                    "co2_basis": "product_category_proxy",
                                },
                                "tradeoffs": ["Chilled (+4 C) cold-chain required"],
                                "reason": "Full quantity in stock for requested date",
                                "price_delta_eur": 12.50,
                            },
                            {
                                "product": {
                                    "id": "464398",
                                    "name": "Valio Maitojuoma laktoositon UHT 1L",
                                },
                                "quote_line": {
                                    "product_id": "464398",
                                    "product_name": "Valio Maitojuoma laktoositon UHT 1L",
                                    "quantity": 1000,
                                    "sales_unit": "ST",
                                    "delivery_status": "ON_TIME",
                                    "available_amount": 5000.0,
                                    "co2_kg": 1120.0,
                                    "co2_basis": "product_category_proxy",
                                },
                                "tradeoffs": ["Ambient storage (UHT)"],
                                "reason": "Full quantity in stock with ambient shelf life",
                                "price_delta_eur": 25.00,
                            },
                        ],
                    },
                )
                return

            if self.path == "/api/mcp":
                state.protocol_versions.append(self.headers.get("MCP-Protocol-Version"))
                auth = self.headers.get("Authorization")
                if auth != f"Bearer {FAKE_JWT}":
                    self._send_json(401, {"error": {"code": "UNAUTHORIZED", "message": "bad jwt"}})
                    return

                method = body.get("method")
                rpc_id = body.get("id")
                params = body.get("params") or {}

                if method == "initialize":
                    self._send_json(
                        200,
                        {
                            "jsonrpc": "2.0",
                            "id": rpc_id,
                            "result": {
                                "protocolVersion": "2025-03-26",
                                "capabilities": {
                                    "tools": {"listChanged": False},
                                    "experimental": {
                                        "supportedMatters": {
                                            "menu_planning": {"enabled": True},
                                            "order_execution": {"enabled": True},
                                            "preliminary_tendering": {"enabled": False},
                                            "exception_handling": {"enabled": True},
                                        }
                                    },
                                },
                                "serverInfo": {"name": "mock-aimo-mcp", "version": "1.0"},
                                "instructions": "Connected to Aimo Annos Purchasing Gateway.",
                            },
                        },
                    )
                    return

                if method == "notifications/initialized":
                    self.send_response(202)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return

                if method == "tools/list":
                    self._send_json(
                        200,
                        {
                            "jsonrpc": "2.0",
                            "id": rpc_id,
                            "result": {
                                "tools": [
                                    {"name": "search_products"},
                                    {"name": "check_products"},
                                    {"name": "create_quote"},
                                    {"name": "place_order"},
                                    {"name": "get_order"},
                                ]
                            },
                        },
                    )
                    return

                if method == "tools/call":
                    tool_name = str(params.get("name"))
                    args = params.get("arguments") or {}
                    state.mcp_calls.append((tool_name, args))
                    self._handle_tool(rpc_id, tool_name, args)
                    return

                self._send_json(
                    200,
                    {"jsonrpc": "2.0", "id": rpc_id, "error": {"code": -32601, "message": "Unknown"}},
                )
                return

            self._send_json(404, {"error": {"code": "NOT_FOUND", "message": "unknown path"}})

        def _tool_ok(self, rpc_id, payload: dict) -> None:
            self._send_json(
                200,
                {
                    "jsonrpc": "2.0",
                    "id": rpc_id,
                    "result": {
                        "content": [{"type": "text", "text": json.dumps(payload)}],
                        "structuredContent": payload,
                        "isError": False,
                    },
                },
            )

        def _tool_err(self, rpc_id, code: str, status_code: int, message: str, details: dict) -> None:
            payload = {
                "error": {
                    "code": code,
                    "status_code": status_code,
                    "message": message,
                    "details": details,
                }
            }
            self._send_json(
                200,
                {
                    "jsonrpc": "2.0",
                    "id": rpc_id,
                    "result": {
                        "content": [{"type": "text", "text": json.dumps(payload)}],
                        "structuredContent": payload,
                        "isError": True,
                    },
                },
            )

        def _handle_tool(self, rpc_id, tool_name: str, args: dict) -> None:
            if tool_name == "search_products":
                self._tool_ok(
                    rpc_id,
                    {
                        "items": [
                            {
                                "id": "464306",
                                "name": "Valio Maitojuoma laktoositon 1L",
                                "sales_unit": "ST",
                            }
                        ],
                        "total": 1,
                    },
                )
                return

            if tool_name == "create_quote":
                line_in = (args.get("lines") or [{}])[0]
                pid = line_in.get("product_id")
                qty = line_in.get("quantity", 1)
                diet = args.get("diet")
                req_date = args.get("requested_delivery_date")

                if pid == "464306" and diet == "L":
                    self._tool_ok(
                        rpc_id,
                        {
                            "id": "qt_act1",
                            "status": "OPEN",
                            "requested_delivery_date": req_date,
                            "total_excl_vat_eur": 890.0,
                            "total_incl_vat_eur": 1014.6,
                            "total_co2_kg": 1100.0,
                            "overall_delivery_status": "ON_TIME",
                            "lines": [
                                {
                                    "product_id": "464306",
                                    "product_name": "Valio Maitojuoma laktoositon 1L",
                                    "quantity": qty,
                                    "delivery_status": "ON_TIME",
                                    "co2_kg": 1100.0,
                                    "co2_basis": "product_category_proxy",
                                    "flags": [],
                                }
                            ],
                            "policy_preview": {
                                "outcome": "APPROVAL_REQUIRED",
                                "rules": [
                                    {
                                        "rule_id": "R-AUTO-LIMIT",
                                        "outcome": "APPROVAL_REQUIRED",
                                        "message": "Exceeds auto_approve_limit_eur",
                                    }
                                ],
                            },
                        },
                    )
                    return

                if pid == "469363":
                    self._tool_ok(
                        rpc_id,
                        {
                            "id": "qt_act2",
                            "status": "OPEN",
                            "requested_delivery_date": req_date,
                            "total_excl_vat_eur": 910.0,
                            "total_co2_kg": 980.0,
                            "overall_delivery_status": "AT_RISK",
                            "lines": [
                                {
                                    "product_id": "469363",
                                    "product_name": "Valio Kevytmaitojuoma laktoositon 1L",
                                    "quantity": qty,
                                    "available_amount": 384.0,
                                    "delivery_status": "AT_RISK",
                                    "co2_kg": 980.0,
                                    "co2_basis": "product_category_proxy",
                                    "flags": ["PARTIAL"],
                                }
                            ],
                            "policy_preview": {
                                "outcome": "APPROVAL_REQUIRED",
                                "rules": [
                                    {
                                        "rule_id": "R-DELIVERY-RISK",
                                        "outcome": "APPROVAL_REQUIRED",
                                        "message": "Delivery risk: AT_RISK (PARTIAL)",
                                    }
                                ],
                            },
                        },
                    )
                    return

                if pid == "463038":
                    self._tool_ok(
                        rpc_id,
                        {
                            "id": "qt_act3",
                            "status": "OPEN",
                            "requested_delivery_date": req_date,
                            "total_excl_vat_eur": 45.0,
                            "overall_delivery_status": "LATE",
                            "lines": [
                                {
                                    "product_id": "463038",
                                    "product_name": "Terminal pastry",
                                    "quantity": qty,
                                    "closing_time": "2026-11-12T15:00:00+0200",
                                    "delivery_status": "LATE",
                                    "flags": ["CUTOFF_PASSED"],
                                }
                            ],
                            "policy_preview": {
                                "outcome": "APPROVAL_REQUIRED",
                                "rules": [
                                    {
                                        "rule_id": "R-DELIVERY-RISK",
                                        "outcome": "APPROVAL_REQUIRED",
                                        "message": "Cut-off passed",
                                    }
                                ],
                            },
                        },
                    )
                    return

                if pid == "464306" and diet == "M":
                    self._tool_ok(
                        rpc_id,
                        {
                            "id": "qt_act4",
                            "status": "OPEN",
                            "requested_delivery_date": req_date,
                            "total_excl_vat_eur": 8.9,
                            "overall_delivery_status": "ON_TIME",
                            "lines": [{"product_id": "464306", "quantity": qty, "delivery_status": "ON_TIME"}],
                            "policy_preview": {
                                "outcome": "BLOCKED",
                                "rules": [
                                    {
                                        "rule_id": "R-HARD-DIET",
                                        "outcome": "BLOCKED",
                                        "message": "Contains milk allergen incompatible with diet M",
                                    }
                                ],
                            },
                        },
                    )
                    return

            if tool_name == "place_order":
                qid = args.get("quote_id")
                state.placed_quotes.append(str(qid))
                if qid == "qt_act4":
                    self._tool_err(
                        rpc_id,
                        code="POLICY_BLOCKED",
                        status_code=403,
                        message="Order blocked by purchasing policy",
                        details={
                            "outcome": "BLOCKED",
                            "request_id": "req_blocked_act4",
                            "rules": [
                                {
                                    "rule_id": "R-HARD-DIET",
                                    "outcome": "BLOCKED",
                                    "message": "Diet M violation on product 464306",
                                }
                            ],
                        },
                    )
                    return

                self._tool_ok(
                    rpc_id,
                    {
                        "id": "ord_act1",
                        "status": "PENDING_APPROVAL",
                        "quote_id": qid,
                        "content_hash": "sha256:abc123def456",
                        "policy": {
                            "outcome": "APPROVAL_REQUIRED",
                            "rules": [
                                {
                                    "rule_id": "R-AUTO-LIMIT",
                                    "outcome": "APPROVAL_REQUIRED",
                                    "message": "Exceeds auto-approve threshold",
                                }
                            ],
                        },
                        "events": [{"type": "ORDER_SUBMITTED", "request_id": "req_act1_order"}],
                    },
                )
                return

            if tool_name == "get_order":
                self._tool_ok(
                    rpc_id,
                    {
                        "id": args.get("order_id"),
                        "status": "PLACED",
                        "gateway_reference": "VALIO-GW-20261114-001",
                    },
                )
                return

            self._tool_err(rpc_id, "UNEXPECTED", 400, f"Unexpected tool {tool_name}", {})

    return MockHandler


class LiveMcpShowcaseTests(unittest.TestCase):
    def test_compute_next_monday_helsinki(self) -> None:
        self.assertEqual(compute_next_monday_helsinki("2026-11-14T10:00:00+02:00"), "2026-11-16")
        self.assertEqual(compute_next_monday_helsinki("2026-11-15T21:30:00Z"), "2026-11-16")
        self.assertEqual(compute_next_monday_helsinki("2026-11-15T22:30:00Z"), "2026-11-23")
        self.assertEqual(compute_next_monday_helsinki("2026-11-16T08:00:00+02:00"), "2026-11-23")

    def test_load_mcp_json_read_only_and_placeholder_fallback(self) -> None:
        mcp_data = {
            "mcpServers": {
                "aimoannos": {
                    "command": "python3",
                    "args": ["./mcp/aimo_mcp_bridge.py"],
                    "env": {
                        "AIMO_BASE_URL": "http://127.0.0.1:8000",
                        "AIMO_AGENT_ID": "agt_demo_read_only",
                        "AIMO_AGENT_SECRET": FAKE_SECRET,
                    },
                }
            }
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg_path = pathlib.Path(tmpdir) / ".mcp.json"
            original_text = json.dumps(mcp_data, indent=2)
            cfg_path.write_text(original_text, encoding="utf-8")

            loaded = load_showcase_config(mcp_config_path=cfg_path, environ={})
            self.assertEqual(loaded.base_url, "http://127.0.0.1:8000")
            self.assertEqual(loaded.agent_id, "agt_demo_read_only")
            self.assertEqual(loaded.agent_secret, FAKE_SECRET)
            self.assertEqual(cfg_path.read_text(encoding="utf-8"), original_text)

            template_data = {
                "mcpServers": {
                    "aimoannos": {
                        "env": {
                            "AIMO_BASE_URL": "${AIMO_BASE_URL}",
                            "AIMO_AGENT_ID": "${AIMO_AGENT_ID}",
                            "AIMO_AGENT_SECRET": "${AIMO_AGENT_SECRET}",
                        }
                    }
                }
            }
            tpl_path = pathlib.Path(tmpdir) / ".mcp.json.template"
            tpl_path.write_text(json.dumps(template_data), encoding="utf-8")

            resolved = load_showcase_config(
                mcp_config_path=tpl_path,
                environ={
                    "AIMO_BASE_URL": "http://127.0.0.1:8001/",
                    "AIMO_AGENT_ID": "agt_from_env",
                    "AIMO_AGENT_SECRET": FAKE_SECRET,
                },
            )
            self.assertEqual(resolved.base_url, "http://127.0.0.1:8001")
            self.assertEqual(resolved.agent_id, "agt_from_env")
            self.assertEqual(resolved.agent_secret, FAKE_SECRET)

            with self.assertRaises(ShowcaseError):
                load_showcase_config(mcp_config_path=tpl_path, environ={})

    def test_secret_redactor_scrubs_secrets_and_jwts(self) -> None:
        redactor = SecretRedactor([FAKE_SECRET, FAKE_JWT])
        sample = (
            f"secret={FAKE_SECRET} jwt={FAKE_JWT} "
            f"Authorization: Bearer {FAKE_JWT} "
            f'{{"agent_secret": "{FAKE_SECRET}"}}'
        )
        redacted = redactor.redact(sample)
        self.assertNotIn(FAKE_SECRET, redacted)
        self.assertNotIn(FAKE_JWT, redacted)
        self.assertIn("[REDACTED]", redacted)

    def test_full_four_acts_no_wait_over_real_stdio_bridge(self) -> None:
        state = _MockState()
        server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(state))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            port = server.server_address[1]
            with tempfile.TemporaryDirectory() as tmpdir:
                cfg_path = pathlib.Path(tmpdir) / ".mcp.json"
                cfg_path.write_text(
                    json.dumps(
                        {
                            "mcpServers": {
                                "aimoannos": {
                                    "command": "python3",
                                    "args": ["./mcp/aimo_mcp_bridge.py"],
                                    "env": {
                                        "AIMO_BASE_URL": f"http://127.0.0.1:{port}",
                                        "AIMO_AGENT_ID": "agt_showcase_test",
                                        "AIMO_AGENT_SECRET": FAKE_SECRET,
                                    },
                                }
                            }
                        }
                    ),
                    encoding="utf-8",
                )

                out = io.StringIO()
                err = io.StringIO()
                started = time.monotonic()
                exit_code = run_showcase(
                    ["--mcp-config", str(cfg_path), "--act", "all", "--no-wait", "--verbose"],
                    out=out,
                    err=err,
                )
                elapsed = time.monotonic() - started

            stdout_text = out.getvalue()
            stderr_text = err.getvalue()
            self.assertEqual(exit_code, 0, msg=f"stderr:\n{stderr_text}\nstdout:\n{stdout_text}")
            self.assertLess(elapsed, 5.0)
            self.assertIn("delivery_date=2026-11-16", stdout_text)
            self.assertIn("PENDING_APPROVAL", stdout_text)
            self.assertIn("R-AUTO-LIMIT", stdout_text)
            self.assertIn("469363", stdout_text)
            self.assertIn("AT_RISK", stdout_text)
            self.assertIn("PARTIAL", stdout_text)
            self.assertIn("464398", stdout_text)
            self.assertIn("CUTOFF_PASSED", stdout_text)
            self.assertIn("POLICY_BLOCKED", stdout_text)
            self.assertIn("R-HARD-DIET", stdout_text)

            # Assert zero secret/JWT leakage in both stdout and stderr (even with --verbose).
            combined_output = stdout_text + "\n" + stderr_text
            self.assertNotIn(FAKE_SECRET, combined_output)
            self.assertNotIn(FAKE_JWT, combined_output)
            self.assertNotIn("aks_super_secret", combined_output)

            # Act 3 must NOT place an order on qt_act3; only qt_act1 and qt_act4 (blocked) should be called.
            self.assertEqual(state.placed_quotes, ["qt_act1", "qt_act4"])
            self.assertTrue(any(v == "2025-03-26" for v in state.protocol_versions))
            self.assertTrue(any(path == "/api/products/substitutes" for _, path, _ in state.rest_calls))
            self.assertTrue(any(path == "/api/health" for _, path, _ in state.rest_calls))
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_error_output_never_leaks_secret_or_jwt(self) -> None:
        state = _MockState(echo_secret_in_error=True)
        server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(state))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            port = server.server_address[1]
            out = io.StringIO()
            err = io.StringIO()
            with mock.patch.dict(
                "os.environ",
                {
                    "AIMO_BASE_URL": f"http://127.0.0.1:{port}",
                    "AIMO_AGENT_ID": "agt_bad_creds",
                    "AIMO_AGENT_SECRET": FAKE_SECRET,
                },
                clear=False,
            ):
                exit_code = run_showcase(["--act", "1", "--no-wait", "--verbose"], out=out, err=err)

            self.assertEqual(exit_code, 1)
            combined = out.getvalue() + "\n" + err.getvalue()
            self.assertNotIn(FAKE_SECRET, combined)
            self.assertNotIn(FAKE_JWT, combined)
            self.assertIn("[REDACTED]", combined)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_act_1_polling_approves_order_and_prints_gateway_ref(self) -> None:
        state = _MockState()
        server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(state))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            port = server.server_address[1]
            out = io.StringIO()
            err = io.StringIO()
            with mock.patch.dict(
                "os.environ",
                {
                    "AIMO_BASE_URL": f"http://127.0.0.1:{port}",
                    "AIMO_AGENT_ID": "agt_poll_test",
                    "AIMO_AGENT_SECRET": FAKE_SECRET,
                },
                clear=False,
            ), mock.patch.object(live_mcp_showcase.time, "sleep", return_value=None):
                exit_code = run_showcase(
                    ["--act", "1", "--wait-approval-seconds", "5"],
                    out=out,
                    err=err,
                )

            self.assertEqual(exit_code, 0, msg=err.getvalue())
            self.assertIn("status=PLACED", out.getvalue())
            self.assertIn("VALIO-GW-20261114-001", out.getvalue())
            self.assertTrue(any(name == "get_order" for name, _ in state.mcp_calls))
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_health_check_refreshes_stale_bearer_behind_judge_gate(self) -> None:
        state = _MockState()
        server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(state))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            port = server.server_address[1]
            cfg = live_mcp_showcase.ShowcaseConfig(
                base_url=f"http://127.0.0.1:{port}",
                agent_id="agt_gate_refresh",
                agent_secret=FAKE_SECRET,
                bearer_token="stale_unverified_token",
            )
            redactor = SecretRedactor([FAKE_SECRET])
            client = live_mcp_showcase.ShowcaseRestClient(cfg, redactor)
            payload, headers = client.get_health()
            self.assertEqual(payload.get("status"), "ok")
            self.assertIn("2026-11-14", payload.get("server_time", "") or headers.get("x-server-time", ""))
            self.assertTrue(any(path == "/api/agent-auth/token" for _, path, _ in state.rest_calls))
            self.assertTrue(any(path == "/api/health" for _, path, _ in state.rest_calls))
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
