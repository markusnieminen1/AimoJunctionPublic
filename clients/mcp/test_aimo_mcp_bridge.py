"""Offline unit tests for aimo_mcp_bridge.py."""

from __future__ import annotations

from dataclasses import dataclass
import io
import json
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from aimo_mcp_bridge import (  # noqa: E402
    AimoMcpBridge,
    BridgeConfig,
    BridgeError,
    MCP_PROTOCOL_VERSION,
    call_rest_one_shot,
    call_tool_one_shot,
    check_connection,
    extract_allow_price_negotiation,
    main,
    normalize_base_url,
    normalize_mcp_initialize_result,
    parse_jwt_exp_epoch,
    validate_mcp_initialize_result,
)


@dataclass
class FakeResponse:
    status: int
    payload: dict | str

    def read(self) -> bytes:
        if isinstance(self.payload, str):
            return self.payload.encode("utf-8")
        return json.dumps(self.payload).encode("utf-8")


class FakeTransport:
    """Recorded HTTP transport for deterministic offline testing."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, str], dict]] = []
        self.token_counter = 0
        self.expires_at_iso = "2026-11-14T14:00:00+00:00"
        self.fail_next_mcp_401 = False
        self.custom_mcp_handler = None

    def __call__(
        self, url: str, headers: dict[str, str], body: bytes, timeout: float
    ) -> FakeResponse:
        parsed = json.loads(body.decode("utf-8"))
        self.calls.append((url, dict(headers), parsed))

        if url.endswith("/api/agent-auth/token"):
            self.token_counter += 1
            return FakeResponse(
                200,
                {
                    "token": f"jwt_tok_{self.token_counter}",
                    "expires_at": self.expires_at_iso,
                },
            )

        if url.endswith("/api/mcp"):
            if self.fail_next_mcp_401:
                self.fail_next_mcp_401 = False
                return FakeResponse(
                    401,
                    {
                        "error": {"code": "UNAUTHENTICATED", "message": "Token expired"},
                        "request_id": "req_401",
                    },
                )
            if self.custom_mcp_handler is not None:
                return self.custom_mcp_handler(url, headers, parsed)

            method = parsed.get("method")
            rpc_id = parsed.get("id")
            if method == "notifications/initialized":
                return FakeResponse(202, "")
            if method == "initialize":
                return FakeResponse(
                    200,
                    {
                        "jsonrpc": "2.0",
                        "id": rpc_id,
                        "result": {
                            "protocolVersion": MCP_PROTOCOL_VERSION,
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
                            "serverInfo": {"name": "aimoannos-mcp", "version": "0.1.0"},
                            "instructions": "Connected to Aimo Annos Purchasing Gateway.",
                        },
                    },
                )
            return FakeResponse(
                200,
                {"jsonrpc": "2.0", "id": rpc_id, "result": {"echo_method": method}},
            )

        return FakeResponse(404, {"detail": "Not found"})


class TestAimoMcpBridge(unittest.TestCase):
    def test_normalize_base_url(self) -> None:
        self.assertEqual(normalize_base_url("http://127.0.0.1:8000"), "http://127.0.0.1:8000")
        self.assertEqual(normalize_base_url("http://127.0.0.1:8000/"), "http://127.0.0.1:8000")
        self.assertEqual(normalize_base_url("http://127.0.0.1:8000/api"), "http://127.0.0.1:8000")
        self.assertEqual(
            normalize_base_url("https://gateway.example.invalid/api/"),
            "https://gateway.example.invalid",
        )

    def test_token_exchange_and_caching(self) -> None:
        now = 1794661200.0  # 2026-11-14T13:00:00Z (well before 14:00:00Z)
        transport = FakeTransport()
        cfg = BridgeConfig(
            base_url="http://127.0.0.1:8000/api",
            agent_id="agt_01",
            agent_secret="aks_secret",
        )
        bridge = AimoMcpBridge(cfg, http_post=transport, clock=lambda: now)

        stdin = io.StringIO(
            json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
            + "\n"
            + json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"})
            + "\n"
            + json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
            + "\n"
        )
        stdout = io.StringIO()
        rc = bridge.run_stdio(stdin=stdin, stdout=stdout)
        self.assertEqual(rc, 0)

        lines = [json.loads(line) for line in stdout.getvalue().splitlines() if line.strip()]
        self.assertEqual(len(lines), 2)
        self.assertEqual(lines[0]["id"], 1)
        self.assertEqual(lines[1]["id"], 2)

        # Token endpoint called only once across all 3 MCP messages
        token_calls = [c for c in transport.calls if c[0].endswith("/api/agent-auth/token")]
        self.assertEqual(len(token_calls), 1)
        self.assertEqual(
            token_calls[0][2],
            {"agent_id": "agt_01", "agent_secret": "aks_secret"},
        )

        mcp_calls = [c for c in transport.calls if c[0].endswith("/api/mcp")]
        self.assertEqual(len(mcp_calls), 3)
        for url, headers, _ in mcp_calls:
            self.assertEqual(url, "http://127.0.0.1:8000/api/mcp")
            self.assertEqual(headers["Authorization"], "Bearer jwt_tok_1")
            self.assertEqual(headers["MCP-Protocol-Version"], MCP_PROTOCOL_VERSION)

    def test_proactive_refresh_near_expiry(self) -> None:
        clock_now = [1794661200.0]  # 13:00:00Z
        transport = FakeTransport()
        transport.expires_at_iso = "2026-11-14T13:10:00Z"  # 600s later
        cfg = BridgeConfig(
            base_url="http://127.0.0.1:8000",
            agent_id="agt_01",
            agent_secret="aks_secret",
        )
        bridge = AimoMcpBridge(cfg, http_post=transport, clock=lambda: clock_now[0])

        bridge.handle_message(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}))
        # Advance to within 30s of expiry (inside 60s skew window)
        clock_now[0] += 570.0
        bridge.handle_message(json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}))

        self.assertEqual(transport.token_counter, 2)
        mcp_calls = [c for c in transport.calls if c[0].endswith("/api/mcp")]
        self.assertEqual(mcp_calls[0][1]["Authorization"], "Bearer jwt_tok_1")
        self.assertEqual(mcp_calls[1][1]["Authorization"], "Bearer jwt_tok_2")

    def test_reactive_refresh_on_401(self) -> None:
        now = 1794661200.0
        transport = FakeTransport()
        cfg = BridgeConfig(
            base_url="http://127.0.0.1:8000",
            agent_id="agt_01",
            agent_secret="aks_secret",
        )
        bridge = AimoMcpBridge(cfg, http_post=transport, clock=lambda: now)

        # Prime token 1
        bridge.handle_message(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize"}))
        # Simulate server rejecting token 1 with 401
        transport.fail_next_mcp_401 = True
        raw = bridge.handle_message(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {"name": "search_products", "arguments": {"q": "maito"}},
                }
            )
        )
        self.assertIsNotNone(raw)
        resp = json.loads(raw)
        self.assertEqual(resp["id"], 2)
        self.assertEqual(resp["result"]["echo_method"], "tools/call")
        self.assertEqual(transport.token_counter, 2)
        mcp_calls = [c for c in transport.calls if c[0].endswith("/api/mcp")]
        self.assertEqual(mcp_calls[-1][1]["Authorization"], "Bearer jwt_tok_2")

    def test_missing_credentials_returns_jsonrpc_error(self) -> None:
        transport = FakeTransport()
        bridge = AimoMcpBridge(BridgeConfig(), http_post=transport)
        raw = bridge.handle_message(json.dumps({"jsonrpc": "2.0", "id": 9, "method": "tools/list"}))
        self.assertIsNotNone(raw)
        resp = json.loads(raw)
        self.assertEqual(resp["id"], 9)
        self.assertEqual(resp["error"]["code"], -32001)
        self.assertIn("AIMO_AGENT_ID", resp["error"]["message"])

    def test_malformed_json_returns_parse_error(self) -> None:
        transport = FakeTransport()
        bridge = AimoMcpBridge(
            BridgeConfig(bearer_token="preissued_jwt"),
            http_post=transport,
        )
        raw = bridge.handle_message("{bad json")
        self.assertIsNotNone(raw)
        resp = json.loads(raw)
        self.assertEqual(resp["error"]["code"], -32700)
        self.assertIsNone(resp["id"])

    def test_check_connection_success_and_failure(self) -> None:
        now = 1794661200.0
        transport = FakeTransport()
        cfg = BridgeConfig(
            base_url="http://127.0.0.1:8000",
            agent_id="agt_01",
            agent_secret="aks_secret",
        )
        summary = check_connection(cfg, http_post=transport, clock=lambda: now)
        self.assertEqual(summary["base_url"], "http://127.0.0.1:8000")
        self.assertEqual(summary["initialize"]["result"]["protocolVersion"], MCP_PROTOCOL_VERSION)
        self.assertEqual(summary["tools_list"]["result"]["echo_method"], "tools/list")

        def failing_auth_transport(
            url: str, headers: dict[str, str], body: bytes, timeout: float
        ) -> FakeResponse:
            return FakeResponse(
                401,
                {"error": {"code": "UNAUTHENTICATED", "message": "Invalid agent credentials"}},
            )

        with self.assertRaises(BridgeError) as ctx:
            check_connection(cfg, http_post=failing_auth_transport, clock=lambda: now)
        self.assertIn("MCP initialize failed", str(ctx.exception))

    def test_main_check_missing_creds_exits_nonzero(self) -> None:
        import contextlib
        import os
        from unittest import mock

        stderr = io.StringIO()
        stdout = io.StringIO()
        with mock.patch.dict(os.environ, {}, clear=True):
            with contextlib.redirect_stderr(stderr), contextlib.redirect_stdout(stdout):
                rc = main(["--check"])
        self.assertEqual(rc, 1)
        self.assertEqual(stdout.getvalue(), "")
        err_payload = json.loads(stderr.getvalue())
        self.assertFalse(err_payload["ok"])
        self.assertIn("Missing authentication credentials", err_payload["error"])

    def test_from_env_with_mcp_config_and_errors(self) -> None:
        import contextlib
        import tempfile
        from unittest import mock

        with tempfile.TemporaryDirectory() as tmpdir:
            cfg_path = pathlib.Path(tmpdir) / ".mcp.json"
            cfg_path.write_text(
                json.dumps(
                    {
                        "mcpServers": {
                            "aimoannos": {
                                "command": "python3",
                                "args": ["${CLAUDE_PLUGIN_ROOT}/mcp/aimo_mcp_bridge.py"],
                                "env": {
                                    "AIMO_BASE_URL": "https://gateway.example.invalid/",
                                    "AIMO_AGENT_ID": "agt_from_mcp",
                                    "AIMO_AGENT_SECRET": "aks_from_mcp",
                                },
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            cfg = BridgeConfig.from_env(env={}, mcp_config_path=cfg_path)
            self.assertEqual(cfg.base_url, "https://gateway.example.invalid")
            self.assertEqual(cfg.agent_id, "agt_from_mcp")
            self.assertEqual(cfg.agent_secret, "aks_from_mcp")

            # Missing config file produces clean error on --check without leaking env.
            stderr = io.StringIO()
            stdout = io.StringIO()
            with mock.patch.dict("os.environ", {"AIMO_AGENT_SECRET": "aks_super_secret"}, clear=True):
                with contextlib.redirect_stderr(stderr), contextlib.redirect_stdout(stdout):
                    rc = main(["--check", "--mcp-config", str(pathlib.Path(tmpdir) / "missing.json")])
            self.assertEqual(rc, 1)
            self.assertNotIn("aks_super_secret", stderr.getvalue())
            self.assertFalse(json.loads(stderr.getvalue())["ok"])

    def test_judge_access_required_hint_and_reactive_refresh(self) -> None:
        now = 1794661200.0
        transport = FakeTransport()

        def judge_gate_handler(url: str, headers: dict[str, str], parsed: dict) -> FakeResponse:
            if headers.get("Authorization") != "Bearer jwt_tok_1":
                return FakeResponse(
                    401,
                    {
                        "error": {
                            "code": "JUDGE_ACCESS_REQUIRED",
                            "message": "Judge access password required",
                        }
                    },
                )
            return FakeResponse(
                200,
                {"jsonrpc": "2.0", "id": parsed.get("id"), "result": {"ok": True}},
            )

        transport.custom_mcp_handler = judge_gate_handler

        # Without agent_id/agent_secret, a stale bearer surfaces actionable M2M guidance.
        bearer_only_bridge = AimoMcpBridge(
            BridgeConfig(base_url="https://gateway.example.invalid", bearer_token="stale_tok"),
            http_post=transport,
            clock=lambda: now,
        )
        err_raw = bearer_only_bridge.handle_message(
            json.dumps({"jsonrpc": "2.0", "id": 11, "method": "tools/list"})
        )
        self.assertIsNotNone(err_raw)
        err_resp = json.loads(err_raw)
        self.assertIn("JUDGE_ACCESS_REQUIRED", err_resp["error"]["message"])
        self.assertIn("POST /api/agent-auth/token", err_resp["error"]["message"])
        self.assertIn("no judge password needed for M2M", err_resp["error"]["message"])

        # With agent_id + agent_secret, 401 JUDGE_ACCESS_REQUIRED refreshes JWT automatically.
        exchange_bridge = AimoMcpBridge(
            BridgeConfig(
                base_url="https://gateway.example.invalid",
                agent_id="agt_01",
                agent_secret="aks_secret",
                bearer_token="stale_tok",
            ),
            http_post=transport,
            clock=lambda: now,
        )
        ok_raw = exchange_bridge.handle_message(
            json.dumps({"jsonrpc": "2.0", "id": 12, "method": "tools/list"})
        )
        self.assertIsNotNone(ok_raw)
        ok_resp = json.loads(ok_raw)
        self.assertEqual(ok_resp["result"], {"ok": True})

    def test_bundled_tokens_auto_discovery_and_jwt_exp_refresh(self) -> None:
        import base64
        import tempfile
        from unittest import mock
        import aimo_mcp_bridge

        # Build a synthetic 3-segment JWT with exp = 1794661500 (300s after 13:00:00Z)
        hdr = base64.urlsafe_b64encode(b'{"alg":"HS256","typ":"JWT"}').decode().rstrip("=")
        pay = base64.urlsafe_b64encode(b'{"sub":"agt_bundled","exp":1794661500}').decode().rstrip("=")
        bundled_jwt = f"{hdr}.{pay}.sig"
        self.assertEqual(parse_jwt_exp_epoch(bundled_jwt), 1794661500.0)

        # 1) Auto-discovery from non-dotfile mcp/aimo_credentials.json (even when .mcp.json is absent)
        with tempfile.TemporaryDirectory() as tmpdir:
            mcp_dir = pathlib.Path(tmpdir) / "mcp"
            mcp_dir.mkdir()
            (mcp_dir / "aimo_credentials.json").write_text(
                json.dumps(
                    {
                        "AIMO_BASE_URL": "https://gateway.example.invalid/",
                        "AIMO_AGENT_ID": "agt_bundled_01",
                        "AIMO_AGENT_SECRET": "aks_bundled_secret",
                        "AIMO_BEARER_TOKEN": bundled_jwt,
                    }
                ),
                encoding="utf-8",
            )
            cfg = BridgeConfig.from_env(env={}, bridge_dir=mcp_dir)
            self.assertEqual(cfg.base_url, "https://gateway.example.invalid")
            self.assertEqual(cfg.agent_id, "agt_bundled_01")
            self.assertEqual(cfg.agent_secret, "aks_bundled_secret")
            self.assertEqual(cfg.bearer_token, bundled_jwt)

        # 2) Fallback to BUNDLED_* constants baked directly into aimo_mcp_bridge.py
        with tempfile.TemporaryDirectory() as empty_dir, mock.patch.multiple(
            aimo_mcp_bridge,
            BUNDLED_BASE_URL="https://bundled.example.invalid/",
            BUNDLED_AGENT_ID="agt_baked",
            BUNDLED_AGENT_SECRET="aks_baked",
            BUNDLED_BEARER_TOKEN=bundled_jwt,
        ):
            baked_cfg = BridgeConfig.from_env(env={}, bridge_dir=empty_dir)
            self.assertEqual(baked_cfg.base_url, "https://bundled.example.invalid")
            self.assertEqual(baked_cfg.agent_id, "agt_baked")
            self.assertEqual(baked_cfg.agent_secret, "aks_baked")
            self.assertEqual(baked_cfg.bearer_token, bundled_jwt)

            # Uses pre-minted JWT immediately with 0 token exchanges while fresh, then refreshes near exp!
            clock_now = [1794661200.0]
            transport = FakeTransport()
            bridge = AimoMcpBridge(baked_cfg, http_post=transport, clock=lambda: clock_now[0])
            bridge.handle_message(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}))
            self.assertEqual(transport.token_counter, 0)
            self.assertEqual(transport.calls[-1][1]["Authorization"], f"Bearer {bundled_jwt}")

            # Advance to within 60s of bundled_jwt exp -> proactively exchanges fresh token!
            clock_now[0] = 1794661460.0
            bridge.handle_message(json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}))
            self.assertEqual(transport.token_counter, 1)
            self.assertEqual(transport.calls[-1][1]["Authorization"], "Bearer jwt_tok_1")

    def test_one_shot_call_and_rest_helpers(self) -> None:
        now = 1794661200.0
        transport = FakeTransport()

        def structured_handler(url: str, headers: dict[str, str], parsed: dict) -> FakeResponse:
            return FakeResponse(
                200,
                {
                    "jsonrpc": "2.0",
                    "id": parsed.get("id"),
                    "result": {
                        "structuredContent": {"items": [{"id": "464306"}], "total": 1},
                        "isError": False,
                    },
                },
            )

        transport.custom_mcp_handler = structured_handler
        cfg = BridgeConfig(
            base_url="https://gateway.example.invalid",
            agent_id="agt_01",
            agent_secret="aks_secret",
        )
        payload, is_err = call_tool_one_shot(
            cfg,
            "search_products",
            {"q": "maito", "diet": "L"},
            http_post=transport,
            clock=lambda: now,
        )
        self.assertFalse(is_err)
        self.assertEqual(payload["items"][0]["id"], "464306")

        seen_rest: list[tuple[str, str, dict[str, str]]] = []

        def fake_req(
            method: str, url: str, headers: dict[str, str], body: bytes | None, timeout: float
        ) -> FakeResponse:
            if url.endswith("/api/agent-auth/token"):
                return transport(url, headers, body or b"{}", timeout)
            seen_rest.append((method, url, dict(headers)))
            return FakeResponse(200, {"status": "ok", "aimo_mode": "live"})

        status_code, health_json = call_rest_one_shot(
            cfg,
            "GET",
            "/api/health",
            http_request=fake_req,
            clock=lambda: now,
        )
        self.assertEqual(status_code, 200)
        self.assertEqual(health_json["status"], "ok")
        self.assertEqual(seen_rest[0][0], "GET")
        self.assertEqual(seen_rest[0][1], "https://gateway.example.invalid/api/health")
        self.assertTrue(seen_rest[0][2]["Authorization"].startswith("Bearer jwt_tok_"))

        # Adversarial cross-origin / scheme-downgrade / protocol-relative exfiltration guards
        for bad_target in (
            "https://evil.example/exfil",
            "http://gateway.example.invalid/api/health",
            "//evil.example/api/health",
            "/\\evil.example/api/health",
            "https://user@gateway.example.invalid/api/health",
        ):
            with self.assertRaises(BridgeError):
                call_rest_one_shot(
                    cfg,
                    "GET",
                    bad_target,
                    http_request=fake_req,
                    clock=lambda: now,
                )
        self.assertEqual(len(seen_rest), 1)

    def test_initialize_result_mcp_schema_validation_and_legacy_array_normalization(self) -> None:
        # 1) Valid MCP InitializeResult (capabilities.experimental.supportedMatters is a keyed record)
        valid_init = {
            "protocolVersion": MCP_PROTOCOL_VERSION,
            "capabilities": {
                "tools": {"listChanged": False},
                "experimental": {
                    "supportedMatters": {
                        "menu_planning": {"enabled": True, "title": "Menu Planning"},
                        "order_execution": {"enabled": True, "title": "Order Execution"},
                        "preliminary_tendering": {
                            "enabled": True,
                            "title": "Preliminary Tendering",
                        },
                        "exception_handling": {"enabled": True, "title": "Exception Handling"},
                    }
                },
            },
            "serverInfo": {"name": "aimoannos-mcp", "version": "0.1.0"},
            "instructions": "Connected to Aimo Annos Purchasing Gateway.",
        }
        validate_mcp_initialize_result(valid_init)
        self.assertIs(extract_allow_price_negotiation(valid_init), True)
        self.assertEqual(normalize_mcp_initialize_result(valid_init), valid_init)

        # 2) Strict MCP InitializeResult validator rejects array/scalar supportedMatters,
        # non-object capability entries, and non-standard top-level supportedMatters.
        array_init = {
            **valid_init,
            "capabilities": {
                "tools": {"listChanged": False},
                "experimental": {
                    "supportedMatters": [
                        {"id": "menu_planning", "enabled": True},
                        {"id": "preliminary_tendering", "enabled": False},
                    ]
                },
            },
        }
        with self.assertRaises(BridgeError) as arr_ctx:
            validate_mcp_initialize_result(array_init)
        self.assertIn("capabilities.experimental.supportedMatters", str(arr_ctx.exception))

        for bad_init in (
            {**valid_init, "supportedMatters": []},
            {**valid_init, "capabilities": ["tools"]},
            {**valid_init, "capabilities": {"tools": True}},
            {**valid_init, "capabilities": {"experimental": {"flag": True}}},
            {
                **valid_init,
                "capabilities": {"experimental": {"supportedMatters": {"menu_planning": True}}},
            },
        ):
            with self.assertRaises(BridgeError):
                validate_mcp_initialize_result(bad_init)

        # 3) Bridge defense-in-depth normalizes legacy array supportedMatters + stray top-level
        # supportedMatters so strict MCP clients (e.g., Claude Desktop v5.11492.5) never fail handshake.
        now = 1794661200.0
        transport = FakeTransport()

        def legacy_init_handler(url: str, headers: dict[str, str], parsed: dict) -> FakeResponse:
            if parsed.get("method") == "initialize":
                legacy_list = [
                    {"id": "menu_planning", "title": "Menu Planning", "enabled": True},
                    {"id": "order_execution", "title": "Order Execution", "enabled": True},
                    {
                        "id": "preliminary_tendering",
                        "title": "Preliminary Tendering",
                        "enabled": False,
                    },
                    {"id": "exception_handling", "title": "Exception Handling", "enabled": True},
                ]
                return FakeResponse(
                    200,
                    {
                        "jsonrpc": "2.0",
                        "id": parsed.get("id"),
                        "result": {
                            "protocolVersion": MCP_PROTOCOL_VERSION,
                            "capabilities": {
                                "tools": {"listChanged": False},
                                "experimental": {"supportedMatters": legacy_list},
                            },
                            "serverInfo": {"name": "aimoannos-mcp", "version": "0.1.0"},
                            "instructions": "Legacy upstream payload.",
                            "supportedMatters": legacy_list,
                        },
                    },
                )
            return FakeResponse(
                200,
                {"jsonrpc": "2.0", "id": parsed.get("id"), "result": {"tools": []}},
            )

        transport.custom_mcp_handler = legacy_init_handler
        cfg = BridgeConfig(
            base_url="http://127.0.0.1:8000",
            agent_id="agt_01",
            agent_secret="aks_secret",
        )
        summary = check_connection(cfg, http_post=transport, clock=lambda: now)
        init_res = summary["initialize"]["result"]
        validate_mcp_initialize_result(init_res)
        self.assertNotIn("supportedMatters", init_res)
        self.assertIs(summary["allow_price_negotiation"], False)
        self.assertEqual(
            set(summary["supported_matters"].keys()),
            {"menu_planning", "order_execution", "preliminary_tendering", "exception_handling"},
        )
        self.assertFalse(summary["supported_matters"]["preliminary_tendering"]["enabled"])


if __name__ == "__main__":
    unittest.main()
