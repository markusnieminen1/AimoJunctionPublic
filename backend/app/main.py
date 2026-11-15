import asyncio
import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from typing import Any

import httpx
from fastapi import FastAPI
from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send
from ulid import ULID

from app import clock
from app import db as app_db
from app.a2a.router import router as a2a_router
from app.a2a.router import well_known_router as a2a_well_known_router
from app.agentic_service.app import INTERNAL_LOOP_HEADER, agentic_app, set_core_asgi_app
from app.agentic_service.service import get_internal_loop_secret
from app.config import settings
from app.errors import register_exception_handlers, request_id_var
from app.logging_config import configure_logging
from app.request_router import LOOP_GUARD_HEADER, RESPONSE_ROUTE_HEADER, inspect_request
from app.routers.admin_campaigns import (
    account_settings_router,
    admin_campaigns_router,
    admin_company_negotiations_router,
    brand_preferences_router,
)
from app.routers.agents import router as agents_router
from app.routers.assistant import router as assistant_router
from app.routers.audit import router as audit_router
from app.routers.auth import router as auth_router
from app.routers.dashboard import router as dashboard_router
from app.routers.dev import router as dev_router
from app.routers.governance import router as governance_router
from app.routers.health import router as health_router
from app.routers.mcp import router as mcp_router
from app.routers.menu import router as menu_router
from app.routers.orders import router as orders_router
from app.routers.products import categories_router
from app.routers.products import router as products_router
from app.routers.promotions import router as promotions_router
from app.routers.quotes import router as quotes_router
from app.routers.request_router import router as request_router
from app.routers.telegram_bridge import router as telegram_bridge_router
from app.routers.telegram_prefs import router as telegram_prefs_router
from app.services.orders import expire_pending_orders, reconcile_orders

logger = logging.getLogger(__name__)

RECONCILER_INTERVAL_SECONDS = 30.0


def _strip_untrusted_loop_headers(scope: Scope) -> tuple[bool, dict[str, str]]:
    """Verify internal loop token; strip any client-spoofed X-Aimo-Forwarded-By header."""
    raw_headers: list[tuple[bytes, bytes]] = list(scope.get("headers") or [])
    incoming_map = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in raw_headers}
    loop_secret = get_internal_loop_secret()
    trusted_internal = (
        incoming_map.get(INTERNAL_LOOP_HEADER.lower(), "") == loop_secret
        and incoming_map.get(LOOP_GUARD_HEADER.lower(), "") == "agentic-service"
    )
    cleaned_headers: list[tuple[bytes, bytes]] = []
    for k_bytes, v_bytes in raw_headers:
        k_lower = k_bytes.decode("latin-1").lower()
        if k_lower == INTERNAL_LOOP_HEADER.lower():
            continue
        if k_lower == LOOP_GUARD_HEADER.lower() and not trusted_internal:
            continue
        cleaned_headers.append((k_bytes, v_bytes))
    scope["headers"] = cleaned_headers
    sanitized_map = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in cleaned_headers}
    return trusted_internal, sanitized_map


async def _read_request_body(receive: Receive) -> bytes:
    chunks: list[bytes] = []
    while True:
        message = await receive()
        if message["type"] == "http.request":
            body = message.get("body", b"")
            if body:
                chunks.append(body)
            if not message.get("more_body", False):
                break
        elif message["type"] == "http.disconnect":
            break
    return b"".join(chunks)


def _make_replay_receive(body_bytes: bytes) -> Receive:
    sent = False

    async def replay_receive() -> Message:
        nonlocal sent
        if not sent:
            sent = True
            return {"type": "http.request", "body": body_bytes, "more_body": False}
        return {"type": "http.disconnect"}

    return replay_receive


class AgenticRoutingMiddleware:
    """Deterministic Request-Field Router separating Core API from Agentic Capabilities Service."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path") or "/"
        method = (scope.get("method") or "GET").upper()
        trusted_internal, hdr_map = _strip_untrusted_loop_headers(scope)
        state = scope.setdefault("state", {})
        state["routing_path"] = "agentic-service->core-api" if trusted_internal else "core-api"

        # Only buffer JSON body on endpoints that may carry unstructured/agentic fields
        needs_body_inspect = (
            not trusted_internal and method == "POST" and path in ("/api/orders", "/api/quotes")
        ) or path.startswith("/api/agentic")

        body_bytes: bytes | None = None
        payload: dict[str, Any] = {}
        if needs_body_inspect:
            body_bytes = await _read_request_body(receive)
            if body_bytes:
                try:
                    parsed = json.loads(body_bytes.decode("utf-8"))
                    if isinstance(parsed, dict):
                        payload = parsed
                except Exception:
                    payload = {}

        routing = inspect_request(
            method=method,
            path=path,
            payload=payload,
            headers=hdr_map,
            allow_loop_guard_header=trusted_internal,
        )
        route_label = routing.target_service

        should_dispatch_to_agentic_app = path.startswith("/api/agentic") or (
            not trusted_internal
            and method == "POST"
            and path in ("/api/orders", "/api/quotes")
            and route_label == "agentic-service"
        )

        async def send_with_route_headers(message: Message) -> None:
            if message["type"] == "http.response.start" and (
                path.startswith("/api") or path == "/.well-known/agent.json"
            ):
                mutable = MutableHeaders(scope=message)
                if RESPONSE_ROUTE_HEADER not in mutable:
                    mutable[RESPONSE_ROUTE_HEADER] = route_label
                if trusted_internal and "X-Aimo-Hop" not in mutable:
                    mutable["X-Aimo-Hop"] = "agentic-service->core-api"
            await send(message)

        effective_receive = _make_replay_receive(body_bytes) if body_bytes is not None else receive

        if should_dispatch_to_agentic_app:
            ext_url = (settings.agentic_service_url or "").strip()
            if ext_url:
                async with httpx.AsyncClient(base_url=ext_url, timeout=20.0) as client:
                    fwd_hdrs = {
                        k: v for k, v in hdr_map.items() if k not in ("host", "content-length")
                    }
                    resp = await client.request(
                        method,
                        path,
                        content=body_bytes or b"",
                        headers=fwd_hdrs,
                    )
                    resp_headers = [
                        (RESPONSE_ROUTE_HEADER.encode("latin-1"), b"agentic-service"),
                        (b"content-type", b"application/json"),
                    ]
                    hop = resp.headers.get("X-Aimo-Hop")
                    if hop:
                        resp_headers.append((b"x-aimo-hop", hop.encode("latin-1")))
                    await send_with_route_headers(
                        {
                            "type": "http.response.start",
                            "status": resp.status_code,
                            "headers": resp_headers,
                        }
                    )
                    await send_with_route_headers(
                        {"type": "http.response.body", "body": resp.content}
                    )
                return

            await agentic_app(scope, effective_receive, send_with_route_headers)
            return

        await self.app(scope, effective_receive, send_with_route_headers)


class RequestContextMiddleware:
    """Attach X-Request-ID and X-Server-Time headers and bind request_id_var."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        incoming_id = headers.get("x-request-id", "").strip()
        req_id = incoming_id if incoming_id else f"req_{ULID()}"

        scope.setdefault("state", {})["request_id"] = req_id
        token = request_id_var.set(req_id)

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                mutable_headers = MutableHeaders(scope=message)
                mutable_headers["X-Request-ID"] = req_id
                mutable_headers["X-Server-Time"] = clock.now_iso()
            await send(message)

        try:
            await self.app(scope, receive, send_with_headers)
        finally:
            request_id_var.reset(token)


def _run_reconciler_tick(*, recover_all_submitting: bool = False) -> None:
    """Run one reconciliation and pending-expiry pass using a fresh DB session."""
    with app_db.SessionLocal() as db:
        if recover_all_submitting:
            from app.services.telegram_customer import ensure_startup_demo_migrations

            ensure_startup_demo_migrations(db)
        reconcile_orders(db, recover_all_submitting=recover_all_submitting)
        expire_pending_orders(db)


async def _reconciler_loop(stop_event: asyncio.Event, interval_seconds: float) -> None:
    """Periodically reconcile unknown/stale orders and expire pending approvals."""
    while not stop_event.is_set():
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
            break
        except TimeoutError:
            pass
        try:
            _run_reconciler_tick()
        except Exception:
            logger.exception("Background reconciler/expiry tick failed")


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Initialize DB, run startup recovery, manage background reconciler, and dispose pool."""
    app_db.init_db()
    try:
        _run_reconciler_tick(recover_all_submitting=True)
    except Exception:
        logger.exception("Startup reconciliation/expiry pass failed")

    stop_event = asyncio.Event()
    reconciler_task = asyncio.create_task(_reconciler_loop(stop_event, RECONCILER_INTERVAL_SECONDS))
    try:
        yield
    finally:
        stop_event.set()
        reconciler_task.cancel()
        with suppress(asyncio.CancelledError):
            await reconciler_task
        app_db.engine.dispose()


def create_app() -> FastAPI:
    """Construct and configure the FastAPI application."""
    configure_logging()

    app = FastAPI(
        title="AimoAnnos API",
        version=settings.version,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        redoc_url=None,
        lifespan=lifespan,
    )
    app.add_middleware(AgenticRoutingMiddleware)
    app.add_middleware(RequestContextMiddleware)
    register_exception_handlers(app)
    app.include_router(health_router, prefix="/api")
    app.include_router(auth_router, prefix="/api")
    app.include_router(telegram_prefs_router, prefix="/api")
    app.include_router(telegram_bridge_router, prefix="/api")
    app.include_router(agents_router, prefix="/api")
    app.include_router(products_router, prefix="/api")
    app.include_router(categories_router, prefix="/api")
    app.include_router(promotions_router, prefix="/api")
    app.include_router(admin_campaigns_router, prefix="/api")
    app.include_router(admin_company_negotiations_router, prefix="/api")
    app.include_router(brand_preferences_router, prefix="/api")
    app.include_router(account_settings_router, prefix="/api")
    app.include_router(menu_router, prefix="/api")
    app.include_router(quotes_router, prefix="/api")
    app.include_router(orders_router, prefix="/api")
    app.include_router(dashboard_router, prefix="/api")
    app.include_router(audit_router, prefix="/api")
    app.include_router(governance_router, prefix="/api")
    app.include_router(request_router, prefix="/api")
    app.include_router(assistant_router, prefix="/api")
    app.include_router(a2a_well_known_router, prefix="/api")
    app.include_router(a2a_well_known_router, include_in_schema=False)
    app.include_router(a2a_router, prefix="/api")
    app.include_router(mcp_router, prefix="/api")
    if settings.app_env == "dev":
        app.include_router(dev_router, prefix="/api")

    set_core_asgi_app(app)
    return app


app = create_app()
