"""FastAPI routers for A2A Agent Card, JSON-RPC 2.0 endpoint, and UI trace list."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request

from app.a2a.service import build_agent_card, handle_rpc, list_a2a_tasks
from app.aimo.client import AimoClient, get_aimo_client
from app.auth import CurrentPrincipal, CurrentUser, DbSession
from app.llm.decisions import DecisionProvider, get_decision_provider
from app.schemas import A2AAgentCard, A2ARpcRequest, A2ATaskListResponse
from app.services.order_gateway import OrderGateway, get_order_gateway

well_known_router = APIRouter(tags=["a2a"])
router = APIRouter(prefix="/a2a", tags=["a2a"])


@well_known_router.get("/.well-known/agent-card.json", response_model=A2AAgentCard)
def get_agent_card(request: Request) -> A2AAgentCard:
    """Serve the public A2A Agent Card for external agent discovery."""
    return build_agent_card(str(request.base_url))


@router.post("")
def post_a2a_rpc(
    payload: A2ARpcRequest,
    principal: CurrentPrincipal,
    db: DbSession,
    aimo_client: Annotated[AimoClient, Depends(get_aimo_client)],
    gateway: Annotated[OrderGateway, Depends(get_order_gateway)],
    decision_provider: Annotated[DecisionProvider, Depends(get_decision_provider)],
) -> dict[str, Any]:
    """Handle A2A JSON-RPC 2.0 calls (message/send, tasks/get, tasks/cancel)."""
    return handle_rpc(
        db,
        aimo_client=aimo_client,
        gateway=gateway,
        decision_provider=decision_provider,
        principal=principal,
        rpc=payload,
    )


@router.get("/tasks", response_model=A2ATaskListResponse)
def get_a2a_tasks(
    principal: CurrentUser,
    db: DbSession,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> A2ATaskListResponse:
    """List company-scoped A2A task traces for the web UI (user-only)."""
    return list_a2a_tasks(db, principal=principal, limit=limit, offset=offset)
