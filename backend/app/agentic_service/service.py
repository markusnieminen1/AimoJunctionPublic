"""Agentic SKU resolution and Core API HTTP/ASGI client (zero DB/ORM imports)."""

from __future__ import annotations

import hashlib
import json
import re
import secrets
from typing import Any

import httpx
from starlette.types import ASGIApp

from app.ids import new_id
from app.llm.prep_list import default_delivery_date, parse_prep_list
from app.request_router import AGENTIC_KEYS, LOOP_GUARD_HEADER, LOOP_GUARD_VALUE

INTERNAL_LOOP_HEADER = "X-Aimo-Internal-Loop-Token"
_INTERNAL_LOOP_SECRET = secrets.token_urlsafe(32)

_PRODUCT_ID_RE = re.compile(r"\b(\d{6})\b")
_PAX_RE = re.compile(r"\b(\d+)\s*pax\b", re.I)
_RECIPE_HINT_RE = re.compile(
    r"\b(rcp_[a-z0-9_]+|lohikeitto|salmon\s+soup|kermaperunat|smoothie|riisipuuro)\b",
    re.I,
)

# Lightweight in-memory idempotency cache for unstructured agentic POST /api/orders replays
_AGENTIC_IDEM_CACHE: dict[tuple[str, str, str], tuple[str, int, dict[str, Any]]] = {}


def get_internal_loop_secret() -> str:
    """Return the per-process secret proving a hop originated from agentic-service."""
    return _INTERNAL_LOOP_SECRET


def _extract_unstructured_text(payload: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in AGENTIC_KEYS:
        val = payload.get(key)
        if isinstance(val, str) and val.strip():
            parts.append(val.strip())
    return "\n".join(parts).strip()


def _canonical_hash(payload: dict[str, Any]) -> str:
    raw = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _build_forward_headers(
    incoming_headers: dict[str, str],
    *,
    idempotency_key: str | None = None,
) -> dict[str, str]:
    out: dict[str, str] = {
        LOOP_GUARD_HEADER: LOOP_GUARD_VALUE,
        INTERNAL_LOOP_HEADER: _INTERNAL_LOOP_SECRET,
    }
    for h_name in ("authorization", "x-request-id"):
        val = incoming_headers.get(h_name)
        if val:
            out[h_name] = val
    if idempotency_key:
        out["Idempotency-Key"] = idempotency_key
    return out


async def _resolve_lines_via_core_api(
    client: httpx.AsyncClient,
    *,
    payload: dict[str, Any],
    forward_headers: dict[str, str],
) -> tuple[int | None, dict[str, Any] | None, list[dict[str, Any]], str | None]:
    """Resolve unstructured text or lines[].query into structured lines via GET /api/products."""
    resolved_lines: list[dict[str, Any]] = []
    text = _extract_unstructured_text(payload)
    parsed = parse_prep_list(text) if text else None

    explicit_diet: str | None = None
    if isinstance(payload.get("diet"), str) and str(payload["diet"]).strip():
        explicit_diet = str(payload["diet"]).strip()
    elif parsed and parsed.global_diets:
        explicit_diet = ",".join(parsed.global_diets)

    raw_lines = payload.get("lines")
    if isinstance(raw_lines, list) and raw_lines:
        for item in raw_lines:
            if not isinstance(item, dict):
                continue
            qty = float(item.get("quantity") or 1.0)
            pid = str(item.get("product_id") or "").strip()
            if pid:
                resolved_lines.append({"product_id": pid, "quantity": qty})
                continue
            q = str(item.get("query") or item.get("description") or "").strip()
            if not q:
                continue
            params: dict[str, Any] = {"q": q, "limit": 5}
            if explicit_diet:
                params["diet"] = explicit_diet
            resp = await client.get("/api/products", params=params, headers=forward_headers)
            if resp.status_code != 200:
                return resp.status_code, resp.json(), [], explicit_diet
            items = resp.json().get("items") or []
            if not items:
                return (
                    404,
                    {
                        "error": {
                            "code": "NOT_FOUND",
                            "message": f"No catalog product matched query '{q}'",
                            "request_id": forward_headers.get("x-request-id") or new_id("req_"),
                            "details": {"query": q},
                        }
                    },
                    [],
                    explicit_diet,
                )
            resolved_lines.append({"product_id": str(items[0]["id"]), "quantity": qty})

    if not resolved_lines and text:
        explicit_pids = _PRODUCT_ID_RE.findall(text)
        if explicit_pids:
            qty = (
                float(parsed.items[0].quantity)
                if (parsed and parsed.items and parsed.items[0].quantity)
                else 1.0
            )
            for pid in dict.fromkeys(explicit_pids):
                resolved_lines.append({"product_id": pid, "quantity": qty})
        elif parsed and parsed.items:
            for p_item in parsed.items[:8]:
                q = (p_item.query or "").strip() or text[:60]
                item_diet = ",".join(p_item.diets) if p_item.diets else explicit_diet
                params = {"q": q, "limit": 5}
                if item_diet:
                    params["diet"] = item_diet
                resp = await client.get("/api/products", params=params, headers=forward_headers)
                if resp.status_code != 200:
                    return resp.status_code, resp.json(), [], explicit_diet
                items = resp.json().get("items") or []
                if not items:
                    return (
                        404,
                        {
                            "error": {
                                "code": "NOT_FOUND",
                                "message": f"No catalog product matched '{q}'",
                                "request_id": forward_headers.get("x-request-id") or new_id("req_"),
                                "details": {"query": q},
                            }
                        },
                        [],
                        explicit_diet,
                    )
                qty = float(p_item.quantity) if p_item.quantity else 1.0
                resolved_lines.append({"product_id": str(items[0]["id"]), "quantity": qty})

    if not resolved_lines:
        return (
            422,
            {
                "error": {
                    "code": "VALIDATION_ERROR",
                    "message": (
                        "Could not extract any purchasable product lines from agentic request"
                    ),
                    "request_id": forward_headers.get("x-request-id") or new_id("req_"),
                    "details": {},
                }
            },
            [],
            explicit_diet,
        )

    return None, None, resolved_lines, explicit_diet


async def execute_agentic_quote_or_order(
    *,
    core_asgi_app: ASGIApp | None,
    core_base_url: str,
    target_kind: str,
    payload: dict[str, Any],
    incoming_headers: dict[str, str],
) -> tuple[int, dict[str, Any], dict[str, str]]:
    """Execute an unstructured quote or order by calling Core API strictly over HTTP/ASGI."""
    norm_headers = {k.lower(): v for k, v in incoming_headers.items()}
    raw_idem = norm_headers.get("idempotency-key", "").strip()

    if target_kind == "order":
        if not raw_idem:
            return (
                422,
                {
                    "error": {
                        "code": "VALIDATION_ERROR",
                        "message": "Missing Idempotency-Key header",
                        "request_id": norm_headers.get("x-request-id") or new_id("req_"),
                        "details": {},
                    }
                },
                {"X-Aimo-Hop": "agentic-service->core-api"},
            )
        auth_fp = hashlib.sha256(norm_headers.get("authorization", "").encode("utf-8")).hexdigest()
        cache_key = (auth_fp, "POST:/api/orders:agentic", raw_idem)
        req_hash = _canonical_hash(payload)
        cached = _AGENTIC_IDEM_CACHE.get(cache_key)
        if cached is not None:
            cached_hash, cached_status, cached_body = cached
            if cached_hash != req_hash:
                return (
                    409,
                    {
                        "error": {
                            "code": "CONFLICT",
                            "message": (
                                "Idempotency-Key already used with a different request payload"
                            ),
                            "request_id": norm_headers.get("x-request-id") or new_id("req_"),
                            "details": {},
                        }
                    },
                    {"X-Aimo-Hop": "agentic-service->core-api"},
                )
            return cached_status, cached_body, {"X-Aimo-Hop": "agentic-service->core-api"}
    elif target_kind == "agentic_order" and not raw_idem:
        raw_idem = f"ag_{new_id('idm_')}"

    transport = httpx.ASGITransport(app=core_asgi_app) if core_asgi_app is not None else None
    async with httpx.AsyncClient(
        transport=transport, base_url=core_base_url, timeout=20.0
    ) as client:
        fwd_headers = _build_forward_headers(norm_headers)
        text = _extract_unstructured_text(payload)
        parsed = parse_prep_list(text) if text else None

        # If caller supplied an explicit quote_id alongside agentic flags on POST /api/orders
        explicit_quote_id = str(payload.get("quote_id") or "").strip()
        recipe_id = str(payload["recipe_id"]).strip() if payload.get("recipe_id") else None
        target_pax = int(payload["target_pax"]) if payload.get("target_pax") is not None else None
        if text and not recipe_id:
            r_match = _RECIPE_HINT_RE.search(text)
            if r_match:
                recipe_id = r_match.group(1).lower()
        if text and target_pax is None:
            p_match = _PAX_RE.search(text)
            if p_match:
                target_pax = int(p_match.group(1))

        if not explicit_quote_id:
            err_status, err_body, resolved_lines, eff_diet = await _resolve_lines_via_core_api(
                client,
                payload=payload,
                forward_headers=fwd_headers,
            )
            if err_status is not None and err_body is not None:
                return err_status, err_body, {"X-Aimo-Hop": "agentic-service->core-api"}

            delivery_date = str(
                payload.get("requested_delivery_date")
                or (parsed.requested_delivery_date if parsed else None)
                or default_delivery_date()
            ).strip()

            quote_body: dict[str, Any] = {
                "requested_delivery_date": delivery_date,
                "lines": resolved_lines,
            }
            if eff_diet:
                quote_body["diet"] = eff_diet
            if recipe_id:
                quote_body["recipe_id"] = recipe_id
            if target_pax is not None:
                quote_body["target_pax"] = target_pax

            q_resp = await client.post("/api/quotes", json=quote_body, headers=fwd_headers)
            if q_resp.status_code not in (200, 201) or target_kind == "quote":
                return (
                    q_resp.status_code,
                    q_resp.json(),
                    {"X-Aimo-Hop": "agentic-service->core-api"},
                )
            quote_json = q_resp.json()
            explicit_quote_id = str(quote_json["id"])
        else:
            eff_diet = str(payload["diet"]).strip() if payload.get("diet") else None

        note_val = str(payload.get("note") or "").strip()
        if not note_val and text:
            src_chan = str(payload.get("source_channel") or "agentic-service").strip()
            note_val = f"[{src_chan}] {text}"[:1000]

        order_body: dict[str, Any] = {"quote_id": explicit_quote_id}
        if note_val:
            order_body["note"] = note_val
        if eff_diet:
            order_body["diet"] = eff_diet
        if recipe_id:
            order_body["recipe_id"] = recipe_id
        if target_pax is not None:
            order_body["target_pax"] = target_pax

        ord_headers = _build_forward_headers(norm_headers, idempotency_key=raw_idem)
        o_resp = await client.post("/api/orders", json=order_body, headers=ord_headers)
        o_json = o_resp.json()

        if target_kind == "order" and o_resp.status_code in (200, 201):
            auth_fp = hashlib.sha256(
                norm_headers.get("authorization", "").encode("utf-8")
            ).hexdigest()
            _AGENTIC_IDEM_CACHE[(auth_fp, "POST:/api/orders:agentic", raw_idem)] = (
                _canonical_hash(payload),
                o_resp.status_code,
                o_json,
            )

        return o_resp.status_code, o_json, {"X-Aimo-Hop": "agentic-service->core-api"}
