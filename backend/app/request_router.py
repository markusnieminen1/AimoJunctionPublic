"""Deterministic Request-Field Inspector separating Core API and Agentic Service (zero LLM)."""

from __future__ import annotations

import re
from typing import Any

from app.config import settings
from app.schemas import GovernanceRoutingResponse, RoutingInspectResponse

STRUCTURED_FIELDS: tuple[str, ...] = (
    "quote_id",
    "requested_delivery_date",
    "lines[].product_id",
    "diet",
    "note",
)
AGENTIC_KEYS: tuple[str, ...] = (
    "prompt",
    "message",
    "natural_language",
    "free_text",
    "prep_list",
    "instructions",
)
AGENTIC_FLAGS: tuple[str, ...] = (
    "auto_substitute",
    "auto_optimize",
    "agentic",
)
AGENTIC_PATH_PREFIXES: tuple[str, ...] = (
    "/api/assistant",
    "/api/a2a",
    "/api/menu/plan",
    "/api/agentic",
    "/.well-known/agent.json",
)
LOOP_GUARD_HEADER = "X-Aimo-Forwarded-By"
LOOP_GUARD_VALUE = "agentic-service"
RESPONSE_ROUTE_HEADER = "X-Aimo-Route"

_AGENTIC_PATH_RE = re.compile(
    r"^(/api/(assistant|a2a|menu/plan|agentic)|/\.well-known/agent\.json)(/|$)"
)


def inspect_request(
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    *,
    allow_loop_guard_header: bool = False,
) -> RoutingInspectResponse:
    """Classify an incoming HTTP request into `core-api` vs `agentic-service` with zero LLM calls.

    Security guardrail (`CRITIQUE.md` §10.8):
    - `allow_loop_guard_header` is ONLY True when the ASGI middleware has verified the per-process
      internal loop-guard token (`X-Aimo-Internal-Loop-Token`) or inside the read-only inspector
      endpoint (`POST /api/routing/inspect`) when NO unstructured agentic fields are present.
    - A client-spoofed `X-Aimo-Forwarded-By: agentic-service` header can never make unstructured
      `prompt`/`free_text` bypass validation on Core API.
    """
    norm_path = (path or "/").split("?", 1)[0].strip() or "/"
    body = payload if isinstance(payload, dict) else {}
    hdrs = {str(k).lower(): str(v) for k, v in (headers or {}).items()}

    matched: list[str] = []
    structured: list[str] = []

    for key in AGENTIC_KEYS:
        val = body.get(key)
        if isinstance(val, str):
            if val.strip():
                matched.append(key)
        elif val is not None:
            matched.append(key)

    for flag in AGENTIC_FLAGS:
        if body.get(flag) is True:
            matched.append(flag)

    lines_val = body.get("lines")
    if isinstance(lines_val, list):
        has_unresolved = any(
            isinstance(ln, dict)
            and not (isinstance(ln.get("product_id"), str) and str(ln.get("product_id")).strip())
            and (ln.get("query") is not None or ln.get("description") is not None)
            for ln in lines_val
        )
        if has_unresolved:
            matched.append("lines[].query")
        elif len(lines_val) > 0:
            structured.append("lines[].product_id")

    for s_key in ("quote_id", "requested_delivery_date", "diet", "note"):
        val = body.get(s_key)
        if val is not None and val != "":
            structured.append(s_key)

    is_agentic_endpoint = bool(_AGENTIC_PATH_RE.match(norm_path))
    if is_agentic_endpoint:
        matched.append(f"path:{norm_path}")

    forwarded_by = hdrs.get(LOOP_GUARD_HEADER.lower(), "").strip()
    trusted_hop = allow_loop_guard_header and forwarded_by == LOOP_GUARD_VALUE

    # Even on a trusted hop, if unstructured fields are somehow still present, Core API rejects
    # them via strict Pydantic schemas; structured forwarded requests go straight to Core API.
    is_agentic = is_agentic_endpoint or (len(matched) > 0 and not trusted_hop)

    if is_agentic:
        return RoutingInspectResponse(
            target_service="agentic-service",
            matched_fields=matched,
            structured_fields=structured,
            reason=(
                "Unstructured/agentic fields detected; routed to Agentic Service, which resolves "
                "SKUs and submits deterministically back to Core API."
            ),
            downstream_flow=[
                "request_router.py",
                "agentic-service",
                "core-api",
                "policy.py",
                "content_hash",
                "audit_events",
            ],
        )

    reason = (
        "Verified X-Aimo-Forwarded-By=agentic-service hop with structured fields; "
        "routed deterministically to Core API."
        if trusted_hop
        else (
            "Deterministic structured fields only; routed directly to Core API (zero LLM calls) "
            "through single policy.py & content_hash funnel."
        )
    )
    return RoutingInspectResponse(
        target_service="core-api",
        matched_fields=[],
        structured_fields=structured,
        reason=reason,
        downstream_flow=[
            "request_router.py",
            "core-api",
            "policy.py",
            "content_hash",
            "audit_events",
        ],
    )


def get_routing_descriptor() -> GovernanceRoutingResponse:
    """Build the boundary descriptor returned by GET /api/governance/routing."""
    ext_url = (settings.agentic_service_url or "").strip() or None
    return GovernanceRoutingResponse(
        architecture="deterministic-field-router -> (core-api | agentic-service -> core-api)",
        core_api_description=(
            "Deterministic transactional Core API owning catalog SQL, Synkka diet verification, "
            "live Valio pricing, quote locks, Layer 1 policy.py, SHA-256 content_hash approvals, "
            "and hash-chained audit_events (zero LLM calls)."
        ),
        agentic_service_description=(
            "Standalone Agentic Capabilities Service with zero direct DB/ORM access; translates "
            "natural-language prompts/prep lists into catalog SKUs and calls Core API over HTTP."
        ),
        agentic_service_mode="external-http" if ext_url else "in-process-asgi",
        agentic_service_url=ext_url,
        structured_fields=list(STRUCTURED_FIELDS),
        agentic_fields=[*AGENTIC_KEYS, *AGENTIC_FLAGS, "lines[].query"],
        agentic_path_prefixes=list(AGENTIC_PATH_PREFIXES),
        loop_guard_header=LOOP_GUARD_HEADER,
        response_route_header=RESPONSE_ROUTE_HEADER,
    )
