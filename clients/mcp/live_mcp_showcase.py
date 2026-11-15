#!/usr/bin/env python3
"""Live external MCP buyer showcase for AimoAnnos (Python 3 stdlib only).

Connects as an independent external buyer agent by spawning
``clients/mcp/aimo_mcp_bridge.py`` over real stdio JSON-RPC 2.0
(``MCP-Protocol-Version: 2025-03-26``) alongside authenticated REST fallback
(``POST /api/products/substitutes``) and server-clock alignment
(``GET /api/health``).

Zero external dependencies (Python 3.11+ stdlib only).
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timedelta
import json
import os
import pathlib
import re
import ssl
import subprocess
import sys
import time
from typing import Any, Mapping, TextIO
import urllib.error
import urllib.request
import uuid
from zoneinfo import ZoneInfo

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from aimo_mcp_bridge import (  # noqa: E402
    DEFAULT_BASE_URL,
    DEFAULT_TIMEOUT_SECONDS,
    MCP_PROTOCOL_VERSION,
    TOKEN_REFRESH_SKEW_SECONDS,
    extract_allow_price_negotiation,
    normalize_base_url,
    parse_iso_timestamp,
    validate_mcp_initialize_result,
)

HELSINKI_TZ = ZoneInfo("Europe/Helsinki")
DEFAULT_BRIDGE_PATH = pathlib.Path(__file__).resolve().with_name("aimo_mcp_bridge.py")

_PLACEHOLDER_RE = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)\}$")
_JWT_RE = re.compile(r"eyJ[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{4,}")
_AGENT_SECRET_RE = re.compile(r"\baks_[A-Za-z0-9._-]{4,}\b")
_BEARER_RE = re.compile(r"(Bearer\s+)[A-Za-z0-9._~+/=-]{8,}", re.IGNORECASE)
_JSON_SECRET_FIELD_RE = re.compile(
    r'("(?:agent_secret|AIMO_AGENT_SECRET|AIMO_BEARER_TOKEN|token|secret)"\s*:\s*")([^"]+)(")',
    re.IGNORECASE,
)


class ShowcaseError(Exception):
    """Sanitized failure during configuration loading or live showcase execution."""


class SecretRedactor:
    """Redacts known secrets and credential patterns from all logs and errors."""

    def __init__(self, initial_secrets: list[str | None] | None = None) -> None:
        self._secrets: set[str] = set()
        for value in initial_secrets or []:
            self.add_secret(value)

    def add_secret(self, value: str | None) -> None:
        if not value:
            return
        cleaned = value.strip()
        if len(cleaned) >= 4:
            self._secrets.add(cleaned)

    def redact(self, text: str) -> str:
        result = text
        for secret in sorted(self._secrets, key=len, reverse=True):
            result = result.replace(secret, "[REDACTED]")
        result = _JSON_SECRET_FIELD_RE.sub(r"\1[REDACTED]\3", result)
        result = _AGENT_SECRET_RE.sub("aks_[REDACTED]", result)
        result = _BEARER_RE.sub(r"\1[REDACTED]", result)
        result = _JWT_RE.sub("[REDACTED_JWT]", result)
        return result


@dataclass(frozen=True)
class ShowcaseConfig:
    """In-memory runtime configuration for the live MCP showcase."""

    base_url: str
    agent_id: str | None
    agent_secret: str | None
    bearer_token: str | None
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    insecure_skip_tls_verify: bool = False
    config_source: str = "environment"


def _resolve_env_value(raw_value: Any, environ: Mapping[str, str]) -> str | None:
    if raw_value is None:
        return None
    text = str(raw_value).strip()
    if not text:
        return None
    match = _PLACEHOLDER_RE.match(text)
    if match:
        env_key = match.group(1)
        resolved = (environ.get(env_key) or "").strip()
        return resolved or None
    return text


def _extract_mcp_env_map(config_path: pathlib.Path, environ: Mapping[str, str]) -> dict[str, str]:
    try:
        raw_text = config_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ShowcaseError(f"Unable to read --mcp-config {config_path}: {exc}") from exc

    try:
        parsed = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise ShowcaseError(f"Invalid JSON in --mcp-config {config_path}: {exc.msg}") from exc

    if not isinstance(parsed, dict):
        raise ShowcaseError(f"Invalid --mcp-config {config_path}: expected JSON object")

    servers = parsed.get("mcpServers")
    if not isinstance(servers, dict) or not servers:
        raise ShowcaseError(
            f"Invalid --mcp-config {config_path}: missing non-empty 'mcpServers' object"
        )

    selected: Any = servers.get("aimoannos")
    if not isinstance(selected, dict):
        for candidate in servers.values():
            if isinstance(candidate, dict) and isinstance(candidate.get("env"), dict):
                selected = candidate
                break

    if not isinstance(selected, dict) or not isinstance(selected.get("env"), dict):
        raise ShowcaseError(
            f"Invalid --mcp-config {config_path}: missing server 'env' dictionary"
        )

    extracted: dict[str, str] = {}
    for key, value in selected["env"].items():
        resolved = _resolve_env_value(value, environ)
        if resolved:
            extracted[str(key)] = resolved
    return extracted


def load_showcase_config(
    *,
    mcp_config_path: str | pathlib.Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> ShowcaseConfig:
    """Load credentials strictly in-memory from ``.mcp.json`` and/or env vars."""
    env = os.environ if environ is None else environ
    merged: dict[str, str] = {}
    source = "environment"

    if mcp_config_path is not None:
        config_path = pathlib.Path(mcp_config_path)
        merged.update(_extract_mcp_env_map(config_path, env))
        source = f"mcp-config:{config_path}"

    for key in (
        "AIMO_BASE_URL",
        "AIMO_AGENT_ID",
        "AIMO_AGENT_SECRET",
        "AIMO_BEARER_TOKEN",
        "AIMO_TIMEOUT_SECONDS",
        "AIMO_INSECURE_SKIP_TLS_VERIFY",
    ):
        if not merged.get(key):
            env_val = _resolve_env_value(env.get(key), env)
            if env_val:
                merged[key] = env_val

    base_url = normalize_base_url(merged.get("AIMO_BASE_URL") or DEFAULT_BASE_URL)
    agent_id = merged.get("AIMO_AGENT_ID") or None
    agent_secret = merged.get("AIMO_AGENT_SECRET") or None
    bearer_token = merged.get("AIMO_BEARER_TOKEN") or None

    raw_timeout = merged.get("AIMO_TIMEOUT_SECONDS") or ""
    try:
        timeout_seconds = max(1.0, float(raw_timeout)) if raw_timeout else DEFAULT_TIMEOUT_SECONDS
    except ValueError as exc:
        raise ShowcaseError("AIMO_TIMEOUT_SECONDS must be a valid number") from exc

    skip_tls = (merged.get("AIMO_INSECURE_SKIP_TLS_VERIFY") or "").lower() in {
        "1",
        "true",
        "yes",
    }

    if not bearer_token and not (agent_id and agent_secret):
        raise ShowcaseError(
            "Missing credentials: provide --mcp-config with AIMO_AGENT_ID and AIMO_AGENT_SECRET "
            "(or set AIMO_AGENT_ID + AIMO_AGENT_SECRET / AIMO_BEARER_TOKEN in the environment)."
        )

    return ShowcaseConfig(
        base_url=base_url,
        agent_id=agent_id,
        agent_secret=agent_secret,
        bearer_token=bearer_token,
        timeout_seconds=timeout_seconds,
        insecure_skip_tls_verify=skip_tls,
        config_source=source,
    )


def parse_helsinki_datetime(raw_value: str) -> datetime:
    """Parse an ISO-8601 timestamp into Europe/Helsinki timezone."""
    normalized = raw_value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ShowcaseError(f"Invalid server_time timestamp: {raw_value}") from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=HELSINKI_TZ)
    return dt.astimezone(HELSINKI_TZ)


def compute_next_monday_helsinki(server_time_iso: str) -> str:
    """Return the upcoming Monday (YYYY-MM-DD) in Europe/Helsinki relative to server time."""
    server_dt = parse_helsinki_datetime(server_time_iso)
    days_ahead = (0 - server_dt.weekday()) % 7
    if days_ahead == 0:
        days_ahead = 7
    return (server_dt + timedelta(days=days_ahead)).date().isoformat()


def validate_delivery_date(raw_date: str) -> str:
    """Validate YYYY-MM-DD delivery date strings."""
    cleaned = raw_date.strip()
    try:
        datetime.strptime(cleaned, "%Y-%m-%d")
    except ValueError as exc:
        raise ShowcaseError(
            f"Invalid --delivery-date '{raw_date}': expected YYYY-MM-DD"
        ) from exc
    return cleaned


class ShowcaseRestClient:
    """Minimal stdlib HTTP client for ``/api/health``, agent auth, and REST substitutes."""

    def __init__(self, config: ShowcaseConfig, redactor: SecretRedactor) -> None:
        self._config = config
        self._redactor = redactor
        self._token: str | None = config.bearer_token
        self._expires_at: float | None = None
        if self._token:
            self._redactor.add_secret(self._token)
        self._ssl_context: ssl.SSLContext | None = None
        if config.insecure_skip_tls_verify:
            self._ssl_context = ssl._create_unverified_context()

    def _request(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, str], dict[str, Any]]:
        url = f"{self._config.base_url}{path}"
        req_headers = {"Accept": "application/json", **(headers or {})}
        data: bytes | None = None
        if body is not None:
            req_headers["Content-Type"] = "application/json"
            data = json.dumps(body).encode("utf-8")

        req = urllib.request.Request(url=url, data=data, headers=req_headers, method=method)
        try:
            with urllib.request.urlopen(
                req,
                timeout=self._config.timeout_seconds,
                context=self._ssl_context,
            ) as resp:
                raw_bytes = resp.read()
                resp_headers = {k.lower(): v for k, v in resp.headers.items()}
                status = int(resp.status)
        except urllib.error.HTTPError as exc:
            raw_bytes = exc.read()
            resp_headers = {k.lower(): v for k, v in exc.headers.items()} if exc.headers else {}
            status = int(exc.code)
        except urllib.error.URLError as exc:
            raise ShowcaseError(
                self._redactor.redact(f"HTTP request to {path} failed: {exc.reason}")
            ) from exc

        text = raw_bytes.decode("utf-8", errors="replace").strip()
        parsed: dict[str, Any] = {}
        if text:
            try:
                loaded = json.loads(text)
                if isinstance(loaded, dict):
                    parsed = loaded
            except json.JSONDecodeError:
                parsed = {"raw": self._redactor.redact(text)}
        return status, resp_headers, parsed

    def get_health(self) -> tuple[dict[str, Any], dict[str, str]]:
        token = self._ensure_token(force_refresh=False)
        status, headers, payload = self._request(
            "GET",
            "/api/health",
            headers={"Authorization": f"Bearer {token}"},
        )
        if status == 401 and self._config.agent_id and self._config.agent_secret:
            token = self._ensure_token(force_refresh=True)
            status, headers, payload = self._request(
                "GET",
                "/api/health",
                headers={"Authorization": f"Bearer {token}"},
            )
        if status < 200 or status >= 300:
            raise ShowcaseError(
                self._redactor.redact(f"GET /api/health failed (HTTP {status}): {payload}")
            )
        return payload, headers

    def _ensure_token(self, *, force_refresh: bool = False) -> str:
        now = time.time()
        if (
            not force_refresh
            and self._token
            and (self._expires_at is None or (self._expires_at - TOKEN_REFRESH_SKEW_SECONDS) > now)
        ):
            return self._token

        if not (self._config.agent_id and self._config.agent_secret):
            if self._token and not force_refresh:
                return self._token
            raise ShowcaseError("Cannot authenticate REST request without agent credentials.")

        status, _, payload = self._request(
            "POST",
            "/api/agent-auth/token",
            body={
                "agent_id": self._config.agent_id,
                "agent_secret": self._config.agent_secret,
            },
        )
        if status < 200 or status >= 300 or not isinstance(payload.get("token"), str):
            raise ShowcaseError(
                self._redactor.redact(
                    f"REST agent auth failed (HTTP {status}): {payload}"
                )
            )
        token = str(payload["token"]).strip()
        self._token = token
        self._expires_at = parse_iso_timestamp(payload.get("expires_at"))
        self._redactor.add_secret(token)
        return token

    def find_substitutes(
        self,
        *,
        product_id: str,
        quantity: float,
        requested_delivery_date: str,
        diet: str | None = None,
    ) -> tuple[dict[str, Any], str | None]:
        req_body: dict[str, Any] = {
            "product_id": product_id,
            "quantity": quantity,
            "requested_delivery_date": requested_delivery_date,
        }
        if diet:
            req_body["diet"] = diet

        token = self._ensure_token(force_refresh=False)
        status, headers, payload = self._request(
            "POST",
            "/api/products/substitutes",
            body=req_body,
            headers={"Authorization": f"Bearer {token}"},
        )
        if status == 401 and self._config.agent_id and self._config.agent_secret:
            token = self._ensure_token(force_refresh=True)
            status, headers, payload = self._request(
                "POST",
                "/api/products/substitutes",
                body=req_body,
                headers={"Authorization": f"Bearer {token}"},
            )

        if status < 200 or status >= 300:
            raise ShowcaseError(
                self._redactor.redact(
                    f"POST /api/products/substitutes failed (HTTP {status}): {payload}"
                )
            )
        return payload, headers.get("x-request-id")


class McpStdioClient:
    """Subprocess stdio JSON-RPC 2.0 client driving ``aimo_mcp_bridge.py``."""

    def __init__(
        self,
        config: ShowcaseConfig,
        redactor: SecretRedactor,
        *,
        bridge_path: pathlib.Path = DEFAULT_BRIDGE_PATH,
        verbose: bool = False,
        err_stream: TextIO = sys.stderr,
    ) -> None:
        self._config = config
        self._redactor = redactor
        self._bridge_path = bridge_path
        self._verbose = verbose
        self._err_stream = err_stream
        self._proc: subprocess.Popen[str] | None = None
        self._next_id = 1
        self.available_tools: list[str] = []
        self.supported_matters: dict[str, dict[str, Any]] = {}
        self.allow_price_negotiation: bool | None = None

    def __enter__(self) -> "McpStdioClient":
        if not self._bridge_path.is_file():
            raise ShowcaseError(f"MCP bridge script not found at {self._bridge_path}")

        child_env = dict(os.environ)
        child_env["AIMO_BASE_URL"] = self._config.base_url
        child_env["AIMO_TIMEOUT_SECONDS"] = str(self._config.timeout_seconds)
        if self._config.agent_id:
            child_env["AIMO_AGENT_ID"] = self._config.agent_id
        else:
            child_env.pop("AIMO_AGENT_ID", None)
        if self._config.agent_secret:
            child_env["AIMO_AGENT_SECRET"] = self._config.agent_secret
        else:
            child_env.pop("AIMO_AGENT_SECRET", None)
        if self._config.bearer_token:
            child_env["AIMO_BEARER_TOKEN"] = self._config.bearer_token
        else:
            child_env.pop("AIMO_BEARER_TOKEN", None)
        if self._config.insecure_skip_tls_verify:
            child_env["AIMO_INSECURE_SKIP_TLS_VERIFY"] = "true"

        self._proc = subprocess.Popen(
            [sys.executable, str(self._bridge_path)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            env=child_env,
        )
        try:
            self._initialize_handshake()
        except Exception:
            self.__exit__(None, None, None)
            raise
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        if self._proc is None:
            return
        try:
            if self._proc.stdin and not self._proc.stdin.closed:
                self._proc.stdin.close()
            self._proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self._proc.kill()
            self._proc.wait(timeout=2)
        finally:
            stderr_left = ""
            if self._proc.stderr and not self._proc.stderr.closed:
                stderr_left = self._proc.stderr.read().strip()
                self._proc.stderr.close()
            if self._proc.stdout and not self._proc.stdout.closed:
                self._proc.stdout.close()
            if stderr_left and self._verbose:
                self._err_stream.write(
                    self._redactor.redact(f"[bridge stderr] {stderr_left}\n")
                )
                self._err_stream.flush()

    def _log_verbose(self, label: str, payload: Any) -> None:
        if not self._verbose:
            return
        rendered = json.dumps(payload, ensure_ascii=False)
        self._err_stream.write(self._redactor.redact(f"[mcp:{label}] {rendered}\n"))
        self._err_stream.flush()

    def _send_notification(self, method: str, params: dict[str, Any] | None = None) -> None:
        if self._proc is None or self._proc.stdin is None:
            raise ShowcaseError("MCP subprocess is not running")
        message: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            message["params"] = params
        self._log_verbose("notify", message)
        self._proc.stdin.write(json.dumps(message) + "\n")
        self._proc.stdin.flush()

    def _send_request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        if self._proc is None or self._proc.stdin is None or self._proc.stdout is None:
            raise ShowcaseError("MCP subprocess is not running")

        rpc_id = self._next_id
        self._next_id += 1
        request: dict[str, Any] = {
            "jsonrpc": "2.0",
            "id": rpc_id,
            "method": method,
            "params": params or {},
        }
        self._log_verbose("send", request)
        self._proc.stdin.write(json.dumps(request) + "\n")
        self._proc.stdin.flush()

        raw_line = self._proc.stdout.readline()
        if not raw_line:
            stderr_msg = ""
            if self._proc.stderr and not self._proc.stderr.closed:
                stderr_msg = self._proc.stderr.read().strip()
            raise ShowcaseError(
                self._redactor.redact(
                    f"MCP bridge terminated unexpectedly during '{method}': {stderr_msg}"
                )
            )

        try:
            response = json.loads(raw_line)
        except json.JSONDecodeError as exc:
            raise ShowcaseError(
                self._redactor.redact(
                    f"Malformed JSON from MCP bridge during '{method}': {exc.msg}"
                )
            ) from exc

        self._log_verbose("recv", response)
        if not isinstance(response, dict):
            raise ShowcaseError(f"Invalid MCP response type during '{method}'")
        if "error" in response:
            raise ShowcaseError(
                self._redactor.redact(
                    f"MCP JSON-RPC error during '{method}': {response['error']}"
                )
            )
        result = response.get("result")
        if not isinstance(result, dict):
            raise ShowcaseError(f"MCP response missing result during '{method}'")
        return result

    def _initialize_handshake(self) -> None:
        init_result = self._send_request(
            "initialize",
            {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "aimo-live-mcp-showcase", "version": "1.0.0"},
            },
        )
        try:
            validate_mcp_initialize_result(init_result)
        except Exception as exc:  # noqa: BLE001
            raise ShowcaseError(f"Invalid MCP InitializeResult: {exc}") from exc
        exp_matters = (
            (init_result.get("capabilities") or {})
            .get("experimental", {})
            .get("supportedMatters")
        )
        if isinstance(exp_matters, dict):
            self.supported_matters = exp_matters
        self.allow_price_negotiation = extract_allow_price_negotiation(init_result)
        self._send_notification("notifications/initialized", {})
        tools_result = self._send_request("tools/list", {})
        raw_tools = tools_result.get("tools") or []
        self.available_tools = [
            str(tool["name"])
            for tool in raw_tools
            if isinstance(tool, dict) and tool.get("name")
        ]

    def call_tool(self, name: str, arguments: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        result = self._send_request("tools/call", {"name": name, "arguments": arguments})
        is_error = bool(result.get("isError", False))
        structured = result.get("structuredContent")
        if isinstance(structured, dict):
            return structured, is_error

        content = result.get("content") or []
        for item in content:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                try:
                    parsed = json.loads(item["text"])
                    if isinstance(parsed, dict):
                        return parsed, is_error
                except json.JSONDecodeError:
                    return {"raw_text": item["text"]}, is_error
        return {}, is_error


def _emit(stream: TextIO, redactor: SecretRedactor, line: str = "") -> None:
    stream.write(redactor.redact(line) + "\n")
    stream.flush()


def _fired_rule_ids(policy: dict[str, Any] | None) -> list[str]:
    if not isinstance(policy, dict):
        return []
    rules = policy.get("rules") or []
    return [str(r["rule_id"]) for r in rules if isinstance(r, dict) and r.get("rule_id")]


def _format_money(val: Any) -> str:
    if isinstance(val, (int, float)):
        return f"{val:.2f} EUR"
    return "n/a"


def _format_co2(val: Any, basis: str | None = None) -> str:
    if isinstance(val, (int, float)):
        suffix = f" ({basis})" if basis else ""
        return f"{val:.2f} kg CO2e{suffix}"
    return "Unavailable"


def _call_tool_ok(
    mcp: McpStdioClient,
    redactor: SecretRedactor,
    tool_name: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    payload, is_error = mcp.call_tool(tool_name, arguments)
    if is_error:
        raise ShowcaseError(
            redactor.redact(f"MCP tool '{tool_name}' failed: {payload}")
        )
    return payload


def run_act_1(
    mcp: McpStdioClient,
    redactor: SecretRedactor,
    *,
    delivery_date: str,
    wait_approval_seconds: int,
    no_wait: bool,
    out: TextIO,
    sku: str = "464306",
    search_query: str = "maitojuoma",
) -> None:
    _emit(out, redactor, f"\n=== Act 1: Happy Path + Live Phone Approval (1,000 x {sku}) ===")

    search_res = _call_tool_ok(
        mcp,
        redactor,
        "search_products",
        {"q": search_query, "diet": "L", "limit": 10},
    )
    items = search_res.get("items") or []
    match_sku = next(
        (item for item in items if isinstance(item, dict) and item.get("id") == sku),
        None,
    )
    if match_sku is None:
        raise ShowcaseError(
            f"Act 1 expected product '{sku}' in search_products(q={search_query!r}, diet='L'), "
            f"got {len(items)} items"
        )
    _emit(
        out,
        redactor,
        f"1) search_products found {match_sku.get('id')} — {match_sku.get('name')} "
        f"(total matches: {search_res.get('total', len(items))})",
    )

    quote = _call_tool_ok(
        mcp,
        redactor,
        "create_quote",
        {
            "requested_delivery_date": delivery_date,
            "lines": [{"product_id": sku, "quantity": 1000}],
            "diet": "L",
        },
    )
    quote_lines = quote.get("lines") or []
    first_line = quote_lines[0] if quote_lines and isinstance(quote_lines[0], dict) else {}
    quote_rules = _fired_rule_ids(quote.get("policy_preview"))
    _emit(
        out,
        redactor,
        f"2) create_quote {quote.get('id')} for {delivery_date}: "
        f"total_excl_vat={_format_money(quote.get('total_excl_vat_eur'))}, "
        f"CO2={_format_co2(quote.get('total_co2_kg'), first_line.get('co2_basis'))}, "
        f"delivery={quote.get('overall_delivery_status')}, "
        f"policy_preview={(quote.get('policy_preview') or {}).get('outcome')} ({quote_rules})",
    )

    idem_key = f"showcase-act1-{uuid.uuid4()}"
    order = _call_tool_ok(
        mcp,
        redactor,
        "place_order",
        {
            "quote_id": quote["id"],
            "note": f"Live MCP showcase Act 1 (1,000 x {sku})",
            "diet": "L",
            "idempotency_key": idem_key,
        },
    )
    order_id = str(order.get("id") or "")
    status = str(order.get("status") or "")
    content_hash = str(order.get("content_hash") or "")
    order_rules = _fired_rule_ids(order.get("policy"))
    events = order.get("events") or []
    last_req_id = events[-1].get("request_id") if events and isinstance(events[-1], dict) else None

    _emit(
        out,
        redactor,
        f"3) place_order -> order_id={order_id}, status={status}, "
        f"content_hash={content_hash}, policy_rules={order_rules}, request_id={last_req_id}",
    )

    if status != "PENDING_APPROVAL":
        raise ShowcaseError(
            f"Act 1 expected status='PENDING_APPROVAL' for 1,000 x {sku}, got '{status}' "
            f"(rules={order_rules}). Ensure the agent's auto_approve_limit_eur is below the quote total."
        )
    if not content_hash:
        raise ShowcaseError("Act 1 missing non-empty content_hash on PENDING_APPROVAL order")
    if "R-AUTO-LIMIT" not in order_rules:
        raise ShowcaseError(
            f"Act 1 expected 'R-AUTO-LIMIT' in order policy rules, got {order_rules}"
        )

    if no_wait:
        _emit(
            out,
            redactor,
            "4) [--no-wait] Verified PENDING_APPROVAL + content_hash + R-AUTO-LIMIT; skipping poll.",
        )
        return

    _emit(
        out,
        redactor,
        f"4) Tap APPROVE on {order_id} in the AimoAnnos Approvals UI/phone "
        f"(polling every 2s for up to {wait_approval_seconds}s)...",
    )
    deadline = time.monotonic() + max(1, wait_approval_seconds)
    current = order
    while time.monotonic() < deadline:
        time.sleep(2.0)
        current = _call_tool_ok(mcp, redactor, "get_order", {"order_id": order_id})
        status = str(current.get("status") or "")
        if status not in {"PENDING_APPROVAL", "SUBMITTING"}:
            break

    gateway_ref = current.get("gateway_reference")
    _emit(
        out,
        redactor,
        f"   Final order state: status={status}, gateway_reference={gateway_ref}",
    )
    if status != "PLACED":
        raise ShowcaseError(
            f"Act 1 order {order_id} ended with status='{status}' instead of 'PLACED'"
        )


def run_act_2(
    mcp: McpStdioClient,
    rest: ShowcaseRestClient,
    redactor: SecretRedactor,
    *,
    delivery_date: str,
    out: TextIO,
    partial_sku: str = "469363",
) -> None:
    _emit(out, redactor, f"\n=== Act 2: Partial Stock Self-Healing (1,000 x {partial_sku}) ===")

    quote = _call_tool_ok(
        mcp,
        redactor,
        "create_quote",
        {
            "requested_delivery_date": delivery_date,
            "lines": [{"product_id": partial_sku, "quantity": 1000}],
            "diet": "L",
        },
    )
    lines = quote.get("lines") or []
    line = lines[0] if lines and isinstance(lines[0], dict) else {}
    quote_rules = _fired_rule_ids(quote.get("policy_preview"))
    overall_status = quote.get("overall_delivery_status")
    line_status = line.get("delivery_status")
    flags = line.get("flags") or []
    _emit(
        out,
        redactor,
        f"1) create_quote {quote.get('id')}: overall={overall_status}, line={line_status}, "
        f"available_amount={line.get('available_amount')}, flags={flags}, "
        f"policy_rules={quote_rules}",
    )
    if "PARTIAL" not in flags or (
        overall_status not in {"AT_RISK", "LATE"} and line_status not in {"AT_RISK", "LATE"}
    ):
        raise ShowcaseError(
            f"Act 2 expected partial-stock AT_RISK/LATE delivery with 'PARTIAL' flag "
            f"for 1,000 x {partial_sku}, got overall={overall_status}, line={line_status}, flags={flags}"
        )
    if "R-DELIVERY-RISK" not in quote_rules:
        raise ShowcaseError(f"Act 2 expected R-DELIVERY-RISK in policy rules, got {quote_rules}")

    subs_res, req_id = rest.find_substitutes(
        product_id=partial_sku,
        quantity=1000,
        requested_delivery_date=delivery_date,
        diet="L",
    )
    candidates = subs_res.get("items") or []
    _emit(
        out,
        redactor,
        f"2) POST /api/products/substitutes returned {len(candidates)} candidate(s) "
        f"(X-Request-ID={req_id}):",
    )
    if not candidates:
        raise ShowcaseError(f"Act 2 expected at least one substitute candidate for {partial_sku}")

    for idx, cand in enumerate(candidates, start=1):
        prod = cand.get("product") or {}
        qline = cand.get("quote_line") or {}
        delta = cand.get("price_delta_eur")
        delta_str = f"{delta:+.2f} EUR" if isinstance(delta, (int, float)) else "n/a"
        tradeoffs = ", ".join(cand.get("tradeoffs") or []) or "none"
        _emit(
            out,
            redactor,
            f"   [{idx}] {prod.get('id')} — {prod.get('name')} | "
            f"delivery={qline.get('delivery_status')} (avail={qline.get('available_amount')}) | "
            f"price_delta={delta_str} | CO2={_format_co2(qline.get('co2_kg'), qline.get('co2_basis'))} | "
            f"tradeoffs=[{tradeoffs}] | reason={cand.get('reason')}",
        )


def run_act_3(
    mcp: McpStdioClient,
    redactor: SecretRedactor,
    *,
    delivery_date: str,
    out: TextIO,
    cutoff_sku: str = "463038",
) -> None:
    _emit(out, redactor, f"\n=== Act 3: Missed Terminal Cut-Off Guard ({cutoff_sku}) ===")

    quote = _call_tool_ok(
        mcp,
        redactor,
        "create_quote",
        {
            "requested_delivery_date": delivery_date,
            "lines": [{"product_id": cutoff_sku, "quantity": 10}],
        },
    )
    lines = quote.get("lines") or []
    line = lines[0] if lines and isinstance(lines[0], dict) else {}
    quote_rules = _fired_rule_ids(quote.get("policy_preview"))
    flags = line.get("flags") or []
    line_status = line.get("delivery_status")

    _emit(
        out,
        redactor,
        f"1) create_quote {quote.get('id')}: delivery_status={line_status}, "
        f"closing_time={line.get('closing_time')}, flags={flags}, "
        f"policy_preview={(quote.get('policy_preview') or {}).get('outcome')} ({quote_rules})",
    )
    if line_status != "LATE":
        raise ShowcaseError(f"Act 3 expected delivery_status='LATE' for {cutoff_sku}, got {line_status}")
    if "CUTOFF_PASSED" not in flags:
        raise ShowcaseError(f"Act 3 expected 'CUTOFF_PASSED' in flags for {cutoff_sku}, got {flags}")
    if "R-DELIVERY-RISK" not in quote_rules:
        raise ShowcaseError(f"Act 3 expected 'R-DELIVERY-RISK' in policy_preview, got {quote_rules}")

    _emit(
        out,
        redactor,
        "2) Cut-off passed: did NOT call place_order without explicit human override.",
    )


def run_act_4(
    mcp: McpStdioClient,
    redactor: SecretRedactor,
    *,
    delivery_date: str,
    out: TextIO,
    sku: str = "464306",
) -> None:
    _emit(out, redactor, f"\n=== Act 4: Hard Diet Safety Block R-HARD-DIET ({sku} with diet='M') ===")

    quote = _call_tool_ok(
        mcp,
        redactor,
        "create_quote",
        {
            "requested_delivery_date": delivery_date,
            "lines": [{"product_id": sku, "quantity": 10}],
            "diet": "M",
        },
    )
    preview = quote.get("policy_preview") or {}
    preview_rules = _fired_rule_ids(preview)
    _emit(
        out,
        redactor,
        f"1) create_quote {quote.get('id')} (diet='M'): "
        f"policy_preview={preview.get('outcome')} ({preview_rules})",
    )

    err_payload, is_error = mcp.call_tool(
        "place_order",
        {
            "quote_id": quote["id"],
            "note": "Live MCP showcase Act 4 (expect R-HARD-DIET block)",
            "diet": "M",
            "idempotency_key": f"showcase-act4-{uuid.uuid4()}",
        },
    )
    if not is_error:
        raise ShowcaseError(
            f"Act 4 expected place_order to be blocked with POLICY_BLOCKED, got success: {err_payload}"
        )

    err_obj = err_payload.get("error") if isinstance(err_payload, dict) else None
    if not isinstance(err_obj, dict):
        raise ShowcaseError(f"Act 4 unexpected error envelope: {err_payload}")

    err_code = err_obj.get("code")
    status_code = err_obj.get("status_code")
    details = err_obj.get("details") if isinstance(err_obj.get("details"), dict) else {}
    rule_ids = _fired_rule_ids(details)
    req_id = details.get("request_id")

    _emit(
        out,
        redactor,
        f"2) place_order deterministically blocked -> code={err_code}, status={status_code}, "
        f"rules={rule_ids}, request_id={req_id}",
    )
    if err_code != "POLICY_BLOCKED":
        raise ShowcaseError(f"Act 4 expected error.code='POLICY_BLOCKED', got {err_code!r}")
    if "R-HARD-DIET" not in rule_ids:
        raise ShowcaseError(f"Act 4 expected 'R-HARD-DIET' in blocked rules, got {rule_ids}")


def run_showcase(
    argv: list[str] | None = None,
    *,
    out: TextIO = sys.stdout,
    err: TextIO = sys.stderr,
    bridge_path: pathlib.Path = DEFAULT_BRIDGE_PATH,
) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run the live 4-act AimoAnnos external MCP buyer showcase via aimo_mcp_bridge.py."
        )
    )
    parser.add_argument(
        "--mcp-config",
        help="Optional path to .mcp.json (reads mcpServers.*.env strictly in-memory).",
    )
    parser.add_argument(
        "--act",
        choices=("1", "2", "3", "4", "all"),
        default="all",
        help="Which demo act to run (default: all).",
    )
    parser.add_argument(
        "--delivery-date",
        help="Override delivery date YYYY-MM-DD (default: next Monday from /api/health server_time).",
    )
    parser.add_argument(
        "--sku",
        default="464306",
        help="Primary lactose-free dairy SKU from your seeded catalog for Acts 1 & 4 (default: 464306).",
    )
    parser.add_argument(
        "--search-query",
        default="maitojuoma",
        help="Catalog search keyword for Act 1 search_products verification (default: maitojuoma).",
    )
    parser.add_argument(
        "--partial-sku",
        default="469363",
        help="Partial-stock SKU from your seeded catalog for Act 2 self-healing (default: 469363).",
    )
    parser.add_argument(
        "--cutoff-sku",
        default="463038",
        help="Passed-cutoff terminal SKU from your seeded catalog for Act 3 guard (default: 463038).",
    )
    parser.add_argument(
        "--wait-approval-seconds",
        type=int,
        default=90,
        help="Seconds to poll Act 1 get_order waiting for phone approval (default: 90).",
    )
    parser.add_argument(
        "--no-wait",
        action="store_true",
        help="Exit after Act 1 verifies PENDING_APPROVAL + content_hash + R-AUTO-LIMIT (CI/test mode).",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Log redacted MCP JSON-RPC frames and bridge stderr to stderr.",
    )
    args = parser.parse_args(argv)

    redactor = SecretRedactor()
    try:
        config = load_showcase_config(mcp_config_path=args.mcp_config)
        redactor.add_secret(config.agent_secret)
        redactor.add_secret(config.bearer_token)

        rest = ShowcaseRestClient(config, redactor)
        health_payload, health_headers = rest.get_health()
        server_time = health_payload.get("server_time") or health_headers.get("x-server-time")
        if not isinstance(server_time, str) or not server_time.strip():
            raise ShowcaseError("GET /api/health did not include server_time or X-Server-Time")

        delivery_date = (
            validate_delivery_date(args.delivery_date)
            if args.delivery_date
            else compute_next_monday_helsinki(server_time)
        )

        _emit(
            out,
            redactor,
            f"AimoAnnos Live MCP Showcase | base_url={config.base_url} | "
            f"agent_id={config.agent_id or 'bearer'} | source={config.config_source}",
        )
        _emit(
            out,
            redactor,
            f"Server clock={server_time} -> delivery_date={delivery_date}",
        )

        with McpStdioClient(
            config,
            redactor,
            bridge_path=bridge_path,
            verbose=args.verbose,
            err_stream=err,
        ) as mcp:
            _emit(
                out,
                redactor,
                f"MCP stdio ready ({MCP_PROTOCOL_VERSION}); tools={len(mcp.available_tools)} "
                f"[{', '.join(mcp.available_tools)}]",
            )

            acts = ("1", "2", "3", "4") if args.act == "all" else (args.act,)
            if "1" in acts:
                run_act_1(
                    mcp,
                    redactor,
                    delivery_date=delivery_date,
                    wait_approval_seconds=args.wait_approval_seconds,
                    no_wait=args.no_wait,
                    out=out,
                    sku=args.sku,
                    search_query=args.search_query,
                )
            if "2" in acts:
                run_act_2(
                    mcp,
                    rest,
                    redactor,
                    delivery_date=delivery_date,
                    out=out,
                    partial_sku=args.partial_sku,
                )
            if "3" in acts:
                run_act_3(
                    mcp,
                    redactor,
                    delivery_date=delivery_date,
                    out=out,
                    cutoff_sku=args.cutoff_sku,
                )
            if "4" in acts:
                run_act_4(
                    mcp,
                    redactor,
                    delivery_date=delivery_date,
                    out=out,
                    sku=args.sku,
                )

        _emit(out, redactor, "\nShowcase completed successfully.")
        return 0
    except ShowcaseError as exc:
        _emit(err, redactor, f"ERROR: {exc}")
        return 1
    except Exception as exc:  # noqa: BLE001
        _emit(err, redactor, f"UNEXPECTED ERROR: {exc}")
        return 1


def main(argv: list[str] | None = None) -> int:
    return run_showcase(argv)


if __name__ == "__main__":
    raise SystemExit(main())
