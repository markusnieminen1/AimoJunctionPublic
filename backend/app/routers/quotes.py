"""Quote endpoints (POST /api/quotes, POST /api/quotes/optimize, GET /api/quotes/{id})."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request, status

from app.aimo.client import AimoClient, get_aimo_client
from app.auth import CurrentPrincipal, DbSession
from app.schemas import (
    QuoteCreateRequest,
    QuoteNegotiateRequest,
    QuoteNegotiateResponse,
    QuoteOptimizeRequest,
    QuoteOptimizeResponse,
    QuoteOut,
)
from app.services.negotiation import negotiate_quote
from app.services.optimizer import optimize_quote
from app.services.quotes import create_quote, get_quote

router = APIRouter(prefix="/quotes", tags=["quotes"])


@router.post("", response_model=QuoteOut, status_code=status.HTTP_201_CREATED)
def post_quote(
    request: Request,
    payload: QuoteCreateRequest,
    principal: CurrentPrincipal,
    db: DbSession,
    client: Annotated[AimoClient, Depends(get_aimo_client)],
) -> QuoteOut:
    """Create a 30-minute locked quote with live pricing, availability, and policy preview."""
    routing_path = getattr(request.state, "routing_path", "core-api")
    return create_quote(
        db=db,
        aimo_client=client,
        principal=principal,
        req=payload,
        source_context={"routing_path": routing_path},
    )


@router.post("/optimize", response_model=QuoteOptimizeResponse)
def post_quote_optimize(
    payload: QuoteOptimizeRequest,
    principal: CurrentPrincipal,
    db: DbSession,
    client: Annotated[AimoClient, Depends(get_aimo_client)],
) -> QuoteOptimizeResponse:
    """Preview multi-objective, diet-safe cart swaps before locking a quote."""
    return optimize_quote(
        db=db,
        aimo_client=client,
        principal=principal,
        req=payload,
    )


@router.post("/negotiate", response_model=QuoteNegotiateResponse)
def post_quote_negotiate(
    request: Request,
    payload: QuoteNegotiateRequest,
    principal: CurrentPrincipal,
    db: DbSession,
    client: Annotated[AimoClient, Depends(get_aimo_client)],
) -> QuoteNegotiateResponse:
    """Run account-gated preliminary A2A volume/bundle price negotiation and lock a Quote."""
    routing_path = getattr(request.state, "routing_path", "core-api")
    return negotiate_quote(
        db=db,
        aimo_client=client,
        principal=principal,
        req=payload,
        source_context={"routing_path": routing_path},
    )


@router.get("/{quote_id}", response_model=QuoteOut)
def get_quote_detail(
    quote_id: str,
    principal: CurrentPrincipal,
    db: DbSession,
) -> QuoteOut:
    """Fetch a company-isolated quote by ID."""
    return get_quote(db=db, principal=principal, quote_id=quote_id)
