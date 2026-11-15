"""Request-Field Inspector endpoint (POST /api/routing/inspect)."""

from __future__ import annotations

from fastapi import APIRouter, Response

from app.auth import CurrentPrincipal
from app.request_router import RESPONSE_ROUTE_HEADER, inspect_request
from app.schemas import RoutingInspectRequest, RoutingInspectResponse

router = APIRouter(prefix="/routing", tags=["governance"])


@router.post("/inspect", response_model=RoutingInspectResponse)
def post_inspect_routing(
    payload: RoutingInspectRequest,
    response: Response,
    _principal: CurrentPrincipal,
) -> RoutingInspectResponse:
    """Deterministically inspect how a request routes between Core API and Agentic Service."""
    # Preview loop-guard header effect ONLY when payload itself has no unstructured text keys
    has_unstructured_text = any(
        isinstance(payload.payload.get(k), str) and str(payload.payload.get(k)).strip()
        for k in ("prompt", "message", "natural_language", "free_text", "prep_list", "instructions")
    )
    res = inspect_request(
        method=payload.method,
        path=payload.path,
        payload=payload.payload,
        headers=payload.headers,
        allow_loop_guard_header=not has_unstructured_text,
    )
    response.headers[RESPONSE_ROUTE_HEADER] = res.target_service
    return res
