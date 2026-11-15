"""Standalone FastAPI application for the Agentic Capabilities Service (`agentic_app`)."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.types import ASGIApp

from app.agentic_service.service import INTERNAL_LOOP_HEADER, execute_agentic_quote_or_order
from app.config import settings
from app.errors import register_exception_handlers
from app.request_router import RESPONSE_ROUTE_HEADER

_CORE_ASGI_APP: ASGIApp | None = None


def set_core_asgi_app(core_app: ASGIApp) -> None:
    """Bind the Core API ASGI app for zero-config in-process HTTP forwarding."""
    global _CORE_ASGI_APP
    _CORE_ASGI_APP = core_app


def _core_base_url() -> str:
    return f"http://127.0.0.1:{settings.port}"


async def _parse_json_body(request: Request) -> dict[str, Any]:
    try:
        data = await request.json()
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def create_agentic_app() -> FastAPI:
    """Construct the standalone Agentic Capabilities FastAPI app (zero DB/ORM access)."""
    svc = FastAPI(
        title="AimoAnnos Agentic Capabilities Service",
        version=settings.version,
        docs_url="/api/agentic/docs",
        openapi_url="/api/agentic/openapi.json",
        redoc_url=None,
    )
    register_exception_handlers(svc)

    @svc.get("/api/agentic/health")
    async def agentic_health() -> JSONResponse:
        return JSONResponse(
            status_code=200,
            content={
                "status": "ok",
                "service": "agentic-service",
                "db_access": "none",
                "core_api_mode": "in-process-asgi" if _CORE_ASGI_APP is not None else "http",
            },
            headers={RESPONSE_ROUTE_HEADER: "agentic-service"},
        )

    @svc.post("/api/quotes")
    @svc.post("/api/agentic/quotes")
    async def agentic_quotes(request: Request) -> JSONResponse:
        payload = await _parse_json_body(request)
        status_code, body, extra_headers = await execute_agentic_quote_or_order(
            core_asgi_app=_CORE_ASGI_APP,
            core_base_url=_core_base_url(),
            target_kind="quote",
            payload=payload,
            incoming_headers=dict(request.headers),
        )
        return JSONResponse(
            status_code=status_code,
            content=body,
            headers={RESPONSE_ROUTE_HEADER: "agentic-service", **extra_headers},
        )

    @svc.post("/api/orders")
    async def agentic_orders(request: Request) -> JSONResponse:
        payload = await _parse_json_body(request)
        status_code, body, extra_headers = await execute_agentic_quote_or_order(
            core_asgi_app=_CORE_ASGI_APP,
            core_base_url=_core_base_url(),
            target_kind="order",
            payload=payload,
            incoming_headers=dict(request.headers),
        )
        return JSONResponse(
            status_code=status_code,
            content=body,
            headers={RESPONSE_ROUTE_HEADER: "agentic-service", **extra_headers},
        )

    @svc.post("/api/agentic/orders")
    async def agentic_explicit_orders(request: Request) -> JSONResponse:
        payload = await _parse_json_body(request)
        status_code, body, extra_headers = await execute_agentic_quote_or_order(
            core_asgi_app=_CORE_ASGI_APP,
            core_base_url=_core_base_url(),
            target_kind="agentic_order",
            payload=payload,
            incoming_headers=dict(request.headers),
        )
        return JSONResponse(
            status_code=status_code,
            content=body,
            headers={RESPONSE_ROUTE_HEADER: "agentic-service", **extra_headers},
        )

    return svc


agentic_app = create_agentic_app()

__all__ = [
    "INTERNAL_LOOP_HEADER",
    "agentic_app",
    "create_agentic_app",
    "set_core_asgi_app",
]
