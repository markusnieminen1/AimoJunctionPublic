#!/usr/bin/env python3
"""Lightweight stdio-to-Streamable-HTTP MCP bridge for AimoAnnos.

Translates newline-delimited JSON-RPC 2.0 MCP messages on stdin/stdout to
``POST ${AIMO_BASE_URL}/api/mcp`` (MCP protocol ``2025-03-26``) with automatic
agent JWT exchange and refresh via ``POST ${AIMO_BASE_URL}/api/agent-auth/token``.

Zero external dependencies (Python 3.11+ stdlib only).
"""

from __future__ import annotations

import argparse
import base64
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
import pathlib
import re
import ssl
import sys
import time
from typing import Any, Callable, Mapping, Protocol, TextIO
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_BASE_URL = "http://127.0.0.1:8000"
DEFAULT_TIMEOUT_SECONDS = 30.0
TOKEN_REFRESH_SKEW_SECONDS = 60.0
MCP_PROTOCOL_VERSION = "2025-03-26"
_ENV_PLACEHOLDER_RE = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)\}$")

# Substituted purely in-memory by buildClaudePluginZip() when a user downloads the Claude plugin.
# In the git repository these sentinel values remain inert (treated as None).
BUNDLED_BASE_URL = "__AIMO_BUNDLED_BASE_URL__"
BUNDLED_AGENT_ID = "__AIMO_BUNDLED_AGENT_ID__"
BUNDLED_AGENT_SECRET = "__AIMO_BUNDLED_AGENT_SECRET__"
BUNDLED_BEARER_TOKEN = "__AIMO_BUNDLED_BEARER_TOKEN__"
BUNDLED_TOKEN_EXPIRES_AT = "__AIMO_BUNDLED_TOKEN_EXPIRES_AT__"


def _clean_bundled_value(raw_value: str | None) -> str | None:
    """Return stripped value if populated, ignoring unreplaced build placeholders."""
    if raw_value is None:
        return None
    text = str(raw_value).strip()
    if (
        not text
        or text.startswith("__AIMO_BUNDLED_")
        or _ENV_PLACEHOLDER_RE.match(text)
    ):
        return None
    return text


def _resolve_mcp_env_value(raw_value: Any, environ: Mapping[str, str]) -> str:
    """Resolve optional ``${VAR}`` placeholders in ``.mcp.json`` against ``environ``."""
    if raw_value is None:
        return ""
    text = str(raw_value).strip()
    match = _ENV_PLACEHOLDER_RE.match(text)
    if match:
        return (environ.get(match.group(1)) or "").strip()
    return text


def load_mcp_config_env(
    config_path: str | pathlib.Path,
    environ: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Read ``mcpServers.*.env`` from a ``.mcp.json`` file without logging secrets."""
    env = os.environ if environ is None else environ
    path = pathlib.Path(config_path)
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise BridgeError(f"Failed to read --mcp-config {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise BridgeError(f"Invalid JSON in --mcp-config {path}: {exc.msg}") from exc

    servers = parsed.get("mcpServers") if isinstance(parsed, dict) else None
    if not isinstance(servers, dict) or not servers:
        raise BridgeError(f"Invalid --mcp-config {path}: missing non-empty 'mcpServers' object")

    server_cfg = servers.get("aimoannos")
    if not isinstance(server_cfg, dict):
        first_cfg = next(iter(servers.values()), None)
        server_cfg = first_cfg if isinstance(first_cfg, dict) else None
    if not isinstance(server_cfg, dict):
        raise BridgeError(f"Invalid --mcp-config {path}: missing server configuration object")

    raw_env = server_cfg.get("env")
    if not isinstance(raw_env, dict):
        return {}

    extracted: dict[str, str] = {}
    for key, raw_val in raw_env.items():
        val = _resolve_mcp_env_value(raw_val, env)
        if val and not val.startswith("__AIMO_BUNDLED_"):
            extracted[str(key)] = val
    return extracted


_CREDENTIAL_KEY_ALIASES: dict[str, str] = {
    "AIMO_BASE_URL": "AIMO_BASE_URL",
    "base_url": "AIMO_BASE_URL",
    "AIMO_AGENT_ID": "AIMO_AGENT_ID",
    "agent_id": "AIMO_AGENT_ID",
    "AIMO_AGENT_SECRET": "AIMO_AGENT_SECRET",
    "agent_secret": "AIMO_AGENT_SECRET",
    "AIMO_BEARER_TOKEN": "AIMO_BEARER_TOKEN",
    "bearer_token": "AIMO_BEARER_TOKEN",
    "token": "AIMO_BEARER_TOKEN",
    "AIMO_TOKEN_EXPIRES_AT": "AIMO_TOKEN_EXPIRES_AT",
    "token_expires_at": "AIMO_TOKEN_EXPIRES_AT",
    "expires_at": "AIMO_TOKEN_EXPIRES_AT",
}


def _try_load_optional_credentials_file(
    path: pathlib.Path,
    environ: Mapping[str, str],
) -> dict[str, str]:
    """Best-effort loader for bundled ``aimo_credentials.json`` or sibling ``.mcp.json``."""
    if not path.is_file():
        return {}
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(parsed, dict):
        return {}
    if isinstance(parsed.get("mcpServers"), dict):
        try:
            return load_mcp_config_env(path, environ)
        except BridgeError:
            return {}

    extracted: dict[str, str] = {}
    for raw_key, raw_val in parsed.items():
        canonical = _CREDENTIAL_KEY_ALIASES.get(str(raw_key))
        if not canonical:
            continue
        resolved = _resolve_mcp_env_value(raw_val, environ)
        cleaned = _clean_bundled_value(resolved)
        if cleaned:
            extracted[canonical] = cleaned
    return extracted


class HttpResponseData(Protocol):
    """Minimal HTTP response interface used by the bridge and unit tests."""

    status: int

    def read(self) -> bytes: ...


HttpPostFn = Callable[[str, dict[str, str], bytes, float], HttpResponseData]
HttpRequestFn = Callable[[str, str, dict[str, str], bytes | None, float], HttpResponseData]


@dataclass(frozen=True)
class BridgeConfig:
    """Runtime configuration for the AimoAnnos stdio MCP bridge."""

    base_url: str = DEFAULT_BASE_URL
    agent_id: str | None = None
    agent_secret: str | None = None
    bearer_token: str | None = None
    bearer_expires_at: str | None = None
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    insecure_skip_tls_verify: bool = False

    @classmethod
    def from_env(
        cls,
        env: Mapping[str, str] | None = None,
        *,
        mcp_config_path: str | pathlib.Path | None = None,
        bridge_dir: str | pathlib.Path | None = None,
    ) -> "BridgeConfig":
        """Build configuration from `.mcp.json`, env vars, sibling credentials, or bundled defaults."""
        env_map = os.environ if env is None else env
        source: dict[str, str] = {}
        if mcp_config_path is not None:
            source.update(load_mcp_config_env(mcp_config_path, env_map))
        for key in (
            "AIMO_BASE_URL",
            "AIMO_AGENT_ID",
            "AIMO_AGENT_SECRET",
            "AIMO_BEARER_TOKEN",
            "AIMO_TOKEN_EXPIRES_AT",
            "AIMO_TIMEOUT_SECONDS",
            "AIMO_INSECURE_SKIP_TLS_VERIFY",
        ):
            if not source.get(key):
                raw = (env_map.get(key) or "").strip()
                if raw:
                    source[key] = raw

        if mcp_config_path is None and (env is None or bridge_dir is not None):
            resolved_dir = (
                pathlib.Path(bridge_dir)
                if bridge_dir is not None
                else pathlib.Path(__file__).resolve().parent
            )
            for candidate in (
                resolved_dir / "aimo_credentials.json",
                resolved_dir.parent / "aimo_credentials.json",
                resolved_dir / ".mcp.json",
                resolved_dir.parent / ".mcp.json",
            ):
                for k, v in _try_load_optional_credentials_file(candidate, env_map).items():
                    if not source.get(k):
                        source[k] = v

        bundled_defaults = {
            "AIMO_BASE_URL": _clean_bundled_value(BUNDLED_BASE_URL),
            "AIMO_AGENT_ID": _clean_bundled_value(BUNDLED_AGENT_ID),
            "AIMO_AGENT_SECRET": _clean_bundled_value(BUNDLED_AGENT_SECRET),
            "AIMO_BEARER_TOKEN": _clean_bundled_value(BUNDLED_BEARER_TOKEN),
            "AIMO_TOKEN_EXPIRES_AT": _clean_bundled_value(BUNDLED_TOKEN_EXPIRES_AT),
        }
        for k, bundled_val in bundled_defaults.items():
            if bundled_val and not source.get(k):
                source[k] = bundled_val

        raw_url = (source.get("AIMO_BASE_URL") or DEFAULT_BASE_URL).strip()
        raw_timeout = (source.get("AIMO_TIMEOUT_SECONDS") or "").strip()
        timeout = float(raw_timeout) if raw_timeout else DEFAULT_TIMEOUT_SECONDS
        skip_tls = (source.get("AIMO_INSECURE_SKIP_TLS_VERIFY") or "").strip().lower() in {
            "1",
            "true",
            "yes",
        }
        return cls(
            base_url=normalize_base_url(raw_url),
            agent_id=(source.get("AIMO_AGENT_ID") or "").strip() or None,
            agent_secret=(source.get("AIMO_AGENT_SECRET") or "").strip() or None,
            bearer_token=(source.get("AIMO_BEARER_TOKEN") or "").strip() or None,
            bearer_expires_at=(source.get("AIMO_TOKEN_EXPIRES_AT") or "").strip() or None,
            timeout_seconds=max(1.0, timeout),
            insecure_skip_tls_verify=skip_tls,
        )


def normalize_base_url(raw_url: str) -> str:
    """Normalize base URL without trailing slash or duplicate ``/api`` suffix."""
    cleaned = raw_url.strip().rstrip("/")
    if cleaned.endswith("/api"):
        cleaned = cleaned[:-4]
    return cleaned or DEFAULT_BASE_URL


def parse_iso_timestamp(value: str | None) -> float | None:
    """Parse an ISO 8601 timestamp into UTC epoch seconds, returning None if invalid."""
    if not value:
        return None
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def parse_jwt_exp_epoch(token: str | None) -> float | None:
    """Extract unverified numeric ``exp`` epoch seconds from a 3-segment JWT, if present."""
    if not token:
        return None
    parts = token.strip().split(".")
    if len(parts) != 3 or not parts[1]:
        return None
    padded = parts[1] + "=" * (-len(parts[1]) % 4)
    try:
        decoded = base64.urlsafe_b64decode(padded.encode("ascii"))
        payload = json.loads(decoded.decode("utf-8"))
    except Exception:  # noqa: BLE001
        return None
    exp = payload.get("exp") if isinstance(payload, dict) else None
    if isinstance(exp, (int, float)) and not isinstance(exp, bool):
        return float(exp)
    return None


class BridgeError(Exception):
    """Structured error raised during auth or HTTP transport."""

    def __init__(self, message: str, *, code: int = -32000, status_code: int | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.status_code = status_code


@dataclass
class _SimpleHttpResponse:
    status: int
    body: bytes

    def read(self) -> bytes:
        return self.body


def make_urllib_request(*, insecure_skip_tls_verify: bool = False) -> HttpRequestFn:
    """Build a stdlib ``urllib.request`` HTTP method dispatcher."""
    ssl_context: ssl.SSLContext | None = None
    if insecure_skip_tls_verify:
        ssl_context = ssl._create_unverified_context()  # noqa: SLF001

    def _request(
        method: str,
        url: str,
        headers: dict[str, str],
        body: bytes | None,
        timeout: float,
    ) -> HttpResponseData:
        req = urllib.request.Request(
            url=url, data=body, headers=headers, method=method.upper()
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=ssl_context) as resp:
                return _SimpleHttpResponse(status=int(resp.status), body=resp.read())
        except urllib.error.HTTPError as exc:
            err_body = exc.read() if exc.fp is not None else b""
            return _SimpleHttpResponse(status=int(exc.code), body=err_body)
        except urllib.error.URLError as exc:
            raise BridgeError(f"Network error contacting {url}: {exc.reason}") from exc
        except TimeoutError as exc:
            raise BridgeError(f"Request timed out contacting {url}") from exc

    return _request


def make_urllib_post(*, insecure_skip_tls_verify: bool = False) -> HttpPostFn:
    """Build a stdlib ``urllib.request`` POST function."""
    req_fn = make_urllib_request(insecure_skip_tls_verify=insecure_skip_tls_verify)

    def _post(url: str, headers: dict[str, str], body: bytes, timeout: float) -> HttpResponseData:
        return req_fn("POST", url, headers, body, timeout)

    return _post


class TokenManager:
    """Caches an agent session JWT and refreshes it proactively or on 401."""

    def __init__(
        self,
        config: BridgeConfig,
        http_post: HttpPostFn,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._config = config
        self._http_post = http_post
        self._clock = clock
        self._cached_token: str | None = config.bearer_token
        self._expires_at_epoch: float | None = (
            parse_iso_timestamp(config.bearer_expires_at)
            or parse_jwt_exp_epoch(config.bearer_token)
        )

    @property
    def can_exchange(self) -> bool:
        return bool(self._config.agent_id and self._config.agent_secret)

    def get_token(self, *, force_refresh: bool = False) -> str:
        """Return a valid Bearer token, exchanging agent credentials if needed."""
        now = self._clock()
        if not force_refresh and self._cached_token:
            if (
                self._expires_at_epoch is None
                or now + TOKEN_REFRESH_SKEW_SECONDS < self._expires_at_epoch
            ):
                return self._cached_token

        if not self.can_exchange:
            if self._cached_token and not force_refresh:
                return self._cached_token
            raise BridgeError(
                "Missing authentication credentials. Set AIMO_AGENT_ID and AIMO_AGENT_SECRET "
                "(or AIMO_BEARER_TOKEN).",
                code=-32001,
            )

        token_url = f"{normalize_base_url(self._config.base_url)}/api/agent-auth/token"
        payload = json.dumps(
            {
                "agent_id": self._config.agent_id,
                "agent_secret": self._config.agent_secret,
            }
        ).encode("utf-8")
        resp = self._http_post(
            token_url,
            {"Content-Type": "application/json", "Accept": "application/json"},
            payload,
            self._config.timeout_seconds,
        )
        raw_body = resp.read().decode("utf-8", errors="replace")
        if resp.status < 200 or resp.status >= 300:
            raise BridgeError(
                f"Agent token exchange failed (HTTP {resp.status}): {_extract_error_message(raw_body)}",
                code=-32001,
                status_code=resp.status,
            )

        try:
            data = json.loads(raw_body)
        except json.JSONDecodeError as exc:
            raise BridgeError("Invalid JSON from /api/agent-auth/token", code=-32001) from exc

        token = data.get("token")
        if not isinstance(token, str) or not token.strip():
            raise BridgeError("Token response did not include 'token'", code=-32001)

        self._cached_token = token.strip()
        parsed_exp = parse_iso_timestamp(data.get("expires_at"))
        # Fallback to 55 minutes if expires_at is absent or unparseable.
        self._expires_at_epoch = parsed_exp if parsed_exp is not None else now + 3300.0
        return self._cached_token


def _extract_error_message(raw_body: str) -> str:
    if not raw_body.strip():
        return "empty response"
    try:
        parsed = json.loads(raw_body)
    except json.JSONDecodeError:
        return raw_body.strip()[:300]
    err = parsed.get("error")
    if isinstance(err, dict):
        code = err.get("code")
        msg = err.get("message")
        req_id = parsed.get("request_id")
        hint = (
            "exchange AIMO_AGENT_ID + AIMO_AGENT_SECRET via POST /api/agent-auth/token "
            "and pass Authorization: Bearer <token> (no judge password needed for M2M)"
            if code == "JUDGE_ACCESS_REQUIRED"
            else None
        )
        parts = [p for p in [code, msg, hint, f"request_id={req_id}" if req_id else None] if p]
        if parts:
            return " - ".join(str(p) for p in parts)
    detail = parsed.get("detail")
    if detail:
        return str(detail)
    return raw_body.strip()[:300]


def _jsonrpc_error(rpc_id: Any, *, code: int, message: str, data: Any = None) -> dict[str, Any]:
    error_obj: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error_obj["data"] = data
    return {"jsonrpc": "2.0", "id": rpc_id, "error": error_obj}


ALLOWED_INITIALIZE_RESULT_KEYS = frozenset(
    {"protocolVersion", "capabilities", "serverInfo", "instructions", "_meta"}
)


def normalize_supported_matters_map(raw_matters: Any) -> dict[str, dict[str, Any]]:
    """Normalize ``supportedMatters`` into a strict ``{matter_id: {enabled: bool, ...}}`` record."""
    normalized: dict[str, dict[str, Any]] = {}
    if isinstance(raw_matters, Mapping):
        for raw_key, raw_val in raw_matters.items():
            key = str(raw_key).strip()
            if not key:
                continue
            if isinstance(raw_val, Mapping):
                entry = dict(raw_val)
                entry["enabled"] = bool(entry.get("enabled", True))
                normalized[key] = entry
            elif isinstance(raw_val, bool):
                normalized[key] = {"enabled": raw_val}
            else:
                normalized[key] = {"enabled": True, "value": raw_val}
        return normalized

    if isinstance(raw_matters, list):
        for item in raw_matters:
            if isinstance(item, Mapping):
                matter_id = str(item.get("id") or "").strip()
                if not matter_id:
                    continue
                entry = {k: v for k, v in item.items() if k != "id"}
                entry["enabled"] = bool(item.get("enabled", True))
                normalized[matter_id] = entry
            elif isinstance(item, str) and item.strip():
                normalized[item.strip()] = {"enabled": True}
    return normalized


def normalize_mcp_initialize_result(result: Mapping[str, Any]) -> dict[str, Any]:
    """Ensure ``initialize`` result conforms to strict MCP ``InitializeResult`` object schemas."""
    cleaned: dict[str, Any] = {
        k: v for k, v in result.items() if k in ALLOWED_INITIALIZE_RESULT_KEYS
    }
    legacy_top_matters = result.get("supportedMatters")

    raw_caps = cleaned.get("capabilities")
    caps_in = dict(raw_caps) if isinstance(raw_caps, Mapping) else {}
    caps_out: dict[str, dict[str, Any]] = {}
    for cap_key, cap_val in caps_in.items():
        if cap_key == "experimental":
            continue
        caps_out[str(cap_key)] = dict(cap_val) if isinstance(cap_val, Mapping) else {}

    raw_exp = caps_in.get("experimental")
    exp_out: dict[str, dict[str, Any]] = {}
    if isinstance(raw_exp, Mapping):
        for exp_key, exp_val in raw_exp.items():
            key = str(exp_key)
            if key == "supportedMatters":
                exp_out[key] = normalize_supported_matters_map(exp_val)
            elif isinstance(exp_val, Mapping):
                exp_out[key] = dict(exp_val)
            elif isinstance(exp_val, list):
                exp_out[key] = normalize_supported_matters_map(exp_val)
            else:
                exp_out[key] = {"value": exp_val}

    if "supportedMatters" not in exp_out and legacy_top_matters is not None:
        exp_out["supportedMatters"] = normalize_supported_matters_map(legacy_top_matters)

    if exp_out:
        caps_out["experimental"] = exp_out

    cleaned["capabilities"] = caps_out
    return cleaned


def validate_mcp_initialize_result(result: Any) -> None:
    """Validate ``result`` against the strict MCP ``2025-03-26`` ``InitializeResult`` schema."""
    if not isinstance(result, Mapping):
        raise BridgeError("Invalid MCP InitializeResult: expected JSON object")

    unexpected_keys = set(result.keys()) - ALLOWED_INITIALIZE_RESULT_KEYS
    if unexpected_keys:
        raise BridgeError(
            f"Invalid MCP InitializeResult: unexpected top-level keys {sorted(unexpected_keys)}"
        )

    proto = result.get("protocolVersion")
    if not isinstance(proto, str) or not proto.strip():
        raise BridgeError("Invalid MCP InitializeResult: missing non-empty string 'protocolVersion'")

    server_info = result.get("serverInfo")
    if (
        not isinstance(server_info, Mapping)
        or not isinstance(server_info.get("name"), str)
        or not server_info["name"].strip()
        or not isinstance(server_info.get("version"), str)
        or not server_info["version"].strip()
    ):
        raise BridgeError(
            "Invalid MCP InitializeResult: 'serverInfo' must be an object with 'name' and 'version'"
        )

    if "instructions" in result and not isinstance(result["instructions"], str):
        raise BridgeError("Invalid MCP InitializeResult: 'instructions' must be a string")

    if "_meta" in result and not isinstance(result["_meta"], Mapping):
        raise BridgeError("Invalid MCP InitializeResult: '_meta' must be an object")

    capabilities = result.get("capabilities")
    if not isinstance(capabilities, Mapping):
        raise BridgeError("Invalid MCP InitializeResult: 'capabilities' must be an object")

    for cap_name, cap_val in capabilities.items():
        if not isinstance(cap_val, Mapping):
            raise BridgeError(
                f"Invalid MCP InitializeResult: capabilities.{cap_name} must be an object, "
                f"got {type(cap_val).__name__}"
            )

    tools_cap = capabilities.get("tools")
    if (
        isinstance(tools_cap, Mapping)
        and "listChanged" in tools_cap
        and not isinstance(tools_cap["listChanged"], bool)
    ):
        raise BridgeError(
            "Invalid MCP InitializeResult: capabilities.tools.listChanged must be a boolean"
        )

    experimental = capabilities.get("experimental")
    if experimental is not None:
        if not isinstance(experimental, Mapping):
            raise BridgeError(
                "Invalid MCP InitializeResult: capabilities.experimental must be an object"
            )
        for exp_name, exp_val in experimental.items():
            if not isinstance(exp_val, Mapping):
                raise BridgeError(
                    f"Invalid MCP InitializeResult: capabilities.experimental.{exp_name} "
                    f"must be an object (record), got {type(exp_val).__name__}"
                )
        supported_matters = experimental.get("supportedMatters")
        if supported_matters is not None:
            for matter_id, matter_obj in supported_matters.items():
                if not isinstance(matter_id, str) or not matter_id.strip():
                    raise BridgeError(
                        "Invalid MCP InitializeResult: supportedMatters keys must be non-empty strings"
                    )
                if not isinstance(matter_obj, Mapping) or not isinstance(
                    matter_obj.get("enabled"), bool
                ):
                    raise BridgeError(
                        f"Invalid MCP InitializeResult: supportedMatters.{matter_id} "
                        "must be an object with boolean 'enabled'"
                    )


def extract_allow_price_negotiation(initialize_result: Mapping[str, Any] | None) -> bool | None:
    """Read ``preliminary_tendering.enabled`` from ``capabilities.experimental.supportedMatters``."""
    if not isinstance(initialize_result, Mapping):
        return None
    caps = initialize_result.get("capabilities")
    exp = caps.get("experimental") if isinstance(caps, Mapping) else None
    matters = exp.get("supportedMatters") if isinstance(exp, Mapping) else None
    if isinstance(matters, Mapping):
        tendering = matters.get("preliminary_tendering")
        if isinstance(tendering, Mapping) and isinstance(tendering.get("enabled"), bool):
            return tendering["enabled"]
    elif isinstance(matters, list):
        for item in matters:
            if (
                isinstance(item, Mapping)
                and item.get("id") == "preliminary_tendering"
                and isinstance(item.get("enabled"), bool)
            ):
                return item["enabled"]
    return None


class AimoMcpBridge:
    """Forwards JSON-RPC 2.0 MCP calls from stdio to ``/api/mcp``."""

    def __init__(
        self,
        config: BridgeConfig,
        *,
        http_post: HttpPostFn | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._config = config
        self._http_post = http_post or make_urllib_post(
            insecure_skip_tls_verify=config.insecure_skip_tls_verify
        )
        self._token_manager = TokenManager(config, self._http_post, clock=clock)
        self._mcp_url = f"{normalize_base_url(config.base_url)}/api/mcp"

    def handle_message(self, raw_line: str) -> str | None:
        """Process one JSON-RPC line and return a JSON response line, or None for notifications."""
        stripped = raw_line.strip()
        if not stripped:
            return None

        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError as exc:
            return json.dumps(
                _jsonrpc_error(None, code=-32700, message=f"Parse error: {exc.msg}")
            )

        if not isinstance(payload, dict):
            return json.dumps(
                _jsonrpc_error(None, code=-32600, message="Invalid Request: expected JSON object")
            )

        rpc_id = payload.get("id")
        method = payload.get("method")
        is_notification = rpc_id is None or (
            isinstance(method, str) and method.startswith("notifications/")
        )

        try:
            resp_bytes, status = self._forward_with_auth_retry(
                json.dumps(payload).encode("utf-8")
            )
        except BridgeError as exc:
            if is_notification:
                return None
            return json.dumps(
                _jsonrpc_error(
                    rpc_id,
                    code=exc.code,
                    message=exc.message,
                    data={"status_code": exc.status_code} if exc.status_code else None,
                )
            )

        if status == 202 or not resp_bytes.strip():
            return None

        if is_notification:
            return None

        decoded = resp_bytes.decode("utf-8", errors="replace").strip()
        try:
            parsed_resp = json.loads(decoded)
        except json.JSONDecodeError:
            return json.dumps(
                _jsonrpc_error(
                    rpc_id,
                    code=-32603,
                    message=f"Upstream returned non-JSON response (HTTP {status})",
                )
            )
        if (
            method == "initialize"
            and isinstance(parsed_resp, dict)
            and isinstance(parsed_resp.get("result"), Mapping)
        ):
            normalized_resp = dict(parsed_resp)
            normalized_resp["result"] = normalize_mcp_initialize_result(parsed_resp["result"])
            return json.dumps(normalized_resp)
        if (
            method == "ping"
            and isinstance(parsed_resp, dict)
            and isinstance(parsed_resp.get("error"), Mapping)
            and parsed_resp["error"].get("code") == -32601
        ):
            return json.dumps({"jsonrpc": "2.0", "id": rpc_id, "result": {}})
        return decoded

    def _forward_with_auth_retry(self, body: bytes) -> tuple[bytes, int]:
        token = self._token_manager.get_token(force_refresh=False)
        resp = self._post_mcp(token, body)

        if resp.status == 401 and self._token_manager.can_exchange:
            token = self._token_manager.get_token(force_refresh=True)
            resp = self._post_mcp(token, body)

        raw = resp.read()
        if resp.status == 202:
            return raw, resp.status

        if resp.status < 200 or resp.status >= 300:
            msg = _extract_error_message(raw.decode("utf-8", errors="replace"))
            raise BridgeError(
                f"MCP request failed (HTTP {resp.status}): {msg}",
                code=-32002 if resp.status in (401, 403) else -32000,
                status_code=resp.status,
            )

        return raw, resp.status

    def _post_mcp(self, token: str, body: bytes) -> HttpResponseData:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "Authorization": f"Bearer {token}",
            "MCP-Protocol-Version": MCP_PROTOCOL_VERSION,
        }
        return self._http_post(self._mcp_url, headers, body, self._config.timeout_seconds)

    def run_stdio(self, stdin: TextIO = sys.stdin, stdout: TextIO = sys.stdout) -> int:
        """Read newline-delimited JSON-RPC messages from stdin and write responses to stdout."""
        for line in stdin:
            response_line = self.handle_message(line)
            if response_line is not None:
                stdout.write(response_line + "\n")
                stdout.flush()
        return 0


def check_connection(
    config: BridgeConfig,
    *,
    http_post: HttpPostFn | None = None,
    clock: Callable[[], float] = time.time,
) -> dict[str, Any]:
    """Authenticate and call ``initialize`` + ``tools/list`` against the MCP server."""
    bridge = AimoMcpBridge(config, http_post=http_post, clock=clock)
    init_raw = bridge.handle_message(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": MCP_PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "aimo-mcp-bridge-check", "version": "1.0.0"},
                },
            }
        )
    )
    init_resp = json.loads(init_raw) if init_raw else None
    if not isinstance(init_resp, dict) or "error" in init_resp or "result" not in init_resp:
        err = init_resp.get("error") if isinstance(init_resp, dict) else "empty response"
        raise BridgeError(f"MCP initialize failed: {err}")
    validate_mcp_initialize_result(init_resp["result"])

    list_raw = bridge.handle_message(
        json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
    )
    list_resp = json.loads(list_raw) if list_raw else None
    if not isinstance(list_resp, dict) or "error" in list_resp or "result" not in list_resp:
        err = list_resp.get("error") if isinstance(list_resp, dict) else "empty response"
        raise BridgeError(f"MCP tools/list failed: {err}")

    exp_matters = (
        (init_resp["result"].get("capabilities") or {})
        .get("experimental", {})
        .get("supportedMatters")
    )
    return {
        "base_url": normalize_base_url(config.base_url),
        "allow_price_negotiation": extract_allow_price_negotiation(init_resp["result"]),
        "supported_matters": exp_matters if isinstance(exp_matters, dict) else {},
        "initialize": init_resp,
        "tools_list": list_resp,
    }


def call_tool_one_shot(
    config: BridgeConfig,
    tool_name: str,
    arguments: dict[str, Any] | None = None,
    *,
    http_post: HttpPostFn | None = None,
    clock: Callable[[], float] = time.time,
) -> tuple[Any, bool]:
    """Invoke a single MCP tool via ``POST /api/mcp`` using bundled/configured credentials."""
    bridge = AimoMcpBridge(config, http_post=http_post, clock=clock)
    raw = bridge.handle_message(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": tool_name, "arguments": arguments or {}},
            }
        )
    )
    resp = json.loads(raw) if raw else None
    if not isinstance(resp, dict):
        raise BridgeError("Empty or invalid JSON-RPC response from /api/mcp")
    if "error" in resp:
        err_obj = resp["error"]
        msg = err_obj.get("message") if isinstance(err_obj, dict) else str(err_obj)
        raise BridgeError(f"MCP tools/call '{tool_name}' failed: {msg}")
    result = resp.get("result")
    if not isinstance(result, dict):
        return resp, False
    is_error = bool(result.get("isError", False))
    if "structuredContent" in result and result["structuredContent"] is not None:
        return result["structuredContent"], is_error
    content = result.get("content")
    if isinstance(content, list):
        for item in content:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                try:
                    return json.loads(item["text"]), is_error
                except json.JSONDecodeError:
                    return {"text": item["text"]}, is_error
    return result, is_error


def call_rest_one_shot(
    config: BridgeConfig,
    method: str,
    path: str,
    body: Any = None,
    *,
    idempotency_key: str | None = None,
    http_request: HttpRequestFn | None = None,
    http_post: HttpPostFn | None = None,
    clock: Callable[[], float] = time.time,
) -> tuple[int, Any]:
    """Call an authenticated REST/A2A path with automatic Bearer JWT exchange & 401 refresh."""
    req_fn = http_request or make_urllib_request(
        insecure_skip_tls_verify=config.insecure_skip_tls_verify
    )
    post_fn = http_post or (
        lambda u, h, b, t: req_fn("POST", u, h, b, t)
    )
    token_mgr = TokenManager(config, post_fn, clock=clock)

    base_origin = normalize_base_url(config.base_url)
    expected_url = urllib.parse.urlsplit(base_origin)
    clean_path = path.strip()
    if (
        not clean_path
        or clean_path.startswith(("//", "/\\"))
        or "\\" in clean_path
        or any(ord(ch) <= 32 or ord(ch) == 127 for ch in clean_path)
    ):
        raise BridgeError(f"Invalid --rest path: {path!r}")
    if clean_path.startswith(("http://", "https://")):
        url = clean_path
    else:
        if not clean_path.startswith("/"):
            clean_path = f"/{clean_path}"
        url = f"{base_origin}{clean_path}"

    target_url = urllib.parse.urlsplit(url)
    if (
        (target_url.scheme.lower(), target_url.netloc.lower())
        != (expected_url.scheme.lower(), expected_url.netloc.lower())
        or target_url.username is not None
        or target_url.password is not None
    ):
        raise BridgeError(
            f"Refusing cross-origin --rest request to {target_url.scheme}://{target_url.netloc} "
            f"(must match AIMO_BASE_URL {expected_url.scheme}://{expected_url.netloc})"
        )

    encoded_body = json.dumps(body).encode("utf-8") if body is not None else None
    upper_method = method.strip().upper()

    def _send(tok: str) -> HttpResponseData:
        hdrs: dict[str, str] = {
            "Accept": "application/json",
            "Authorization": f"Bearer {tok}",
        }
        if encoded_body is not None:
            hdrs["Content-Type"] = "application/json"
        if idempotency_key:
            hdrs["Idempotency-Key"] = idempotency_key
        return req_fn(upper_method, url, hdrs, encoded_body, config.timeout_seconds)

    token = token_mgr.get_token(force_refresh=False)
    resp = _send(token)
    if resp.status == 401 and token_mgr.can_exchange:
        token = token_mgr.get_token(force_refresh=True)
        resp = _send(token)

    raw_text = resp.read().decode("utf-8", errors="replace").strip()
    parsed: Any = {}
    if raw_text:
        try:
            parsed = json.loads(raw_text)
        except json.JSONDecodeError:
            parsed = {"raw": raw_text}
    return resp.status, parsed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Stdio-to-Streamable-HTTP MCP bridge & CLI helper for AimoAnnos (/api/mcp)."
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Verify agent auth + MCP initialize/tools/list and print JSON summary, then exit.",
    )
    parser.add_argument(
        "--mcp-config",
        default=None,
        help="Optional path to .mcp.json (reads mcpServers.*.env strictly in-memory).",
    )
    parser.add_argument(
        "--call",
        nargs="+",
        metavar="ARG",
        help="Call an MCP tool directly using bundled/configured tokens: --call <tool> ['{...}'].",
    )
    parser.add_argument(
        "--rest",
        nargs="+",
        metavar="ARG",
        help="Call an authenticated REST/A2A endpoint: --rest <METHOD> <PATH> ['{...}'].",
    )
    parser.add_argument(
        "--idempotency-key",
        default=None,
        help="Optional Idempotency-Key header when using --rest POST /api/orders.",
    )
    args = parser.parse_args(argv)
    is_cli_mode = bool(args.check or args.call or args.rest)
    try:
        config = BridgeConfig.from_env(mcp_config_path=args.mcp_config)
    except Exception as exc:  # noqa: BLE001
        if is_cli_mode:
            print(json.dumps({"ok": False, "error": str(exc)}), file=sys.stderr)
            return 1
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 1

    if args.check:
        try:
            summary = check_connection(config)
        except Exception as exc:  # noqa: BLE001
            print(json.dumps({"ok": False, "error": str(exc)}), file=sys.stderr)
            return 1
        print(json.dumps({"ok": True, **summary}, indent=2))
        return 0

    if args.call:
        if len(args.call) > 2:
            print(
                json.dumps({"ok": False, "error": "Usage: --call <tool_name> ['<json_args>']"}),
                file=sys.stderr,
            )
            return 1
        tool_name = args.call[0]
        raw_tool_args = args.call[1] if len(args.call) == 2 else "{}"
        try:
            parsed_args = json.loads(raw_tool_args)
            if not isinstance(parsed_args, dict):
                raise ValueError("Tool arguments must be a JSON object")
            payload, is_err = call_tool_one_shot(config, tool_name, parsed_args)
        except Exception as exc:  # noqa: BLE001
            print(json.dumps({"ok": False, "error": str(exc)}), file=sys.stderr)
            return 1
        out_stream = sys.stderr if is_err else sys.stdout
        print(json.dumps(payload, indent=2), file=out_stream)
        return 1 if is_err else 0

    if args.rest:
        if len(args.rest) < 2 or len(args.rest) > 3:
            print(
                json.dumps({"ok": False, "error": "Usage: --rest <METHOD> <PATH> ['<json_body>']"}),
                file=sys.stderr,
            )
            return 1
        method_arg, path_arg = args.rest[0], args.rest[1]
        body_obj: Any = None
        if len(args.rest) == 3:
            try:
                body_obj = json.loads(args.rest[2])
            except json.JSONDecodeError as exc:
                print(
                    json.dumps({"ok": False, "error": f"Invalid JSON body for --rest: {exc.msg}"}),
                    file=sys.stderr,
                )
                return 1
        try:
            status_code, resp_payload = call_rest_one_shot(
                config,
                method_arg,
                path_arg,
                body_obj,
                idempotency_key=args.idempotency_key,
            )
        except Exception as exc:  # noqa: BLE001
            print(json.dumps({"ok": False, "error": str(exc)}), file=sys.stderr)
            return 1
        is_http_ok = 200 <= status_code < 300
        out_stream = sys.stdout if is_http_ok else sys.stderr
        print(json.dumps(resp_payload, indent=2), file=out_stream)
        return 0 if is_http_ok else 1

    bridge = AimoMcpBridge(config)
    return bridge.run_stdio()


if __name__ == "__main__":
    raise SystemExit(main())
