"""Order endpoints (create, list, detail, approve, reject, cancel)."""

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import JSONResponse

from app.aimo.client import AimoClient, get_aimo_client
from app.auth import CurrentPrincipal, CurrentUser, DbSession
from app.schemas import (
    OrderApproveRequest,
    OrderCreateRequest,
    OrderDecisionRequest,
    OrderListResponse,
    OrderOut,
    OrderStatus,
    OrderTraceResponse,
)
from app.services.order_gateway import OrderGateway, get_order_gateway
from app.services.orders import (
    approve_order,
    cancel_order,
    get_order,
    get_order_trace,
    list_orders,
    place_order,
    reject_order,
)

router = APIRouter(prefix="/orders", tags=["orders"])


@router.post("", response_model=OrderOut, status_code=201)
def post_order(
    request: Request,
    payload: OrderCreateRequest,
    principal: CurrentPrincipal,
    db: DbSession,
    client: Annotated[AimoClient, Depends(get_aimo_client)],
    gateway: Annotated[OrderGateway, Depends(get_order_gateway)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> JSONResponse:
    """Place an order from an OPEN quote with Idempotency-Key enforcement."""
    routing_path = getattr(request.state, "routing_path", "core-api")
    status_code, body = place_order(
        db=db,
        aimo_client=client,
        principal=principal,
        req=payload,
        raw_idempotency_key=idempotency_key,
        gateway=gateway,
        source_context={"routing_path": routing_path},
    )
    return JSONResponse(status_code=status_code, content=body)


@router.get("", response_model=OrderListResponse)
def get_orders(
    principal: CurrentPrincipal,
    db: DbSession,
    status: OrderStatus | None = None,
    created_by_agent: bool | None = None,
    agent_id: str | None = None,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> OrderListResponse:
    """List orders in the caller's company (agents only see their own orders)."""
    return list_orders(
        db=db,
        principal=principal,
        status_filter=status,
        created_by_agent=created_by_agent,
        agent_id=agent_id,
        limit=limit,
        offset=offset,
    )


@router.get("/{order_id}/trace", response_model=OrderTraceResponse)
def get_order_trace_endpoint(
    order_id: str,
    principal: CurrentPrincipal,
    db: DbSession,
) -> OrderTraceResponse:
    """Fetch unified 'How we got here' trace combining chat/A2A, reasoning, audit & order events."""
    return get_order_trace(db=db, principal=principal, order_id=order_id)


@router.get("/{order_id}", response_model=OrderOut)
def get_order_detail(
    order_id: str,
    principal: CurrentPrincipal,
    db: DbSession,
) -> OrderOut:
    """Fetch a single order by ID (company- and agent-isolated)."""
    return get_order(db=db, principal=principal, order_id=order_id)


@router.post("/{order_id}/approve", response_model=OrderOut)
def post_approve_order(
    order_id: str,
    payload: OrderApproveRequest,
    principal: CurrentUser,
    db: DbSession,
    client: Annotated[AimoClient, Depends(get_aimo_client)],
    gateway: Annotated[OrderGateway, Depends(get_order_gateway)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> JSONResponse:
    """Approve a PENDING_APPROVAL order (user-only) with content_hash and Idempotency-Key."""
    status_code, body = approve_order(
        db=db,
        aimo_client=client,
        principal=principal,
        order_id=order_id,
        req=payload,
        raw_idempotency_key=idempotency_key,
        gateway=gateway,
    )
    return JSONResponse(status_code=status_code, content=body)


@router.post("/{order_id}/reject", response_model=OrderOut)
def post_reject_order(
    order_id: str,
    principal: CurrentUser,
    db: DbSession,
    payload: OrderDecisionRequest | None = None,
) -> OrderOut:
    """Reject a PENDING_APPROVAL order (user-only)."""
    return reject_order(
        db=db,
        principal=principal,
        order_id=order_id,
        req=payload or OrderDecisionRequest(),
    )


@router.post("/{order_id}/cancel", response_model=OrderOut)
def post_cancel_order(
    order_id: str,
    principal: CurrentPrincipal,
    db: DbSession,
    payload: OrderDecisionRequest | None = None,
) -> OrderOut:
    """Cancel a PENDING_APPROVAL order (user or owning agent)."""
    return cancel_order(
        db=db,
        principal=principal,
        order_id=order_id,
        req=payload or OrderDecisionRequest(),
    )
