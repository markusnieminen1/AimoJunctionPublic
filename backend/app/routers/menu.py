"""Restaurant Menu Bot router (/api/menu/plan)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from app.aimo.client import AimoClient, get_aimo_client
from app.auth import CurrentPrincipal, DbSession
from app.schemas import MenuPlanRequest, MenuPlanResponse
from app.services.menu_planner import plan_menu

router = APIRouter(prefix="/menu", tags=["menu"])


@router.post("/plan", response_model=MenuPlanResponse)
def plan_menu_endpoint(
    req: MenuPlanRequest,
    principal: CurrentPrincipal,
    db: DbSession,
    aimo_client: Annotated[AimoClient, Depends(get_aimo_client)],
) -> MenuPlanResponse:
    """Plan a diet-safe, deal- and promotion-aware menu with quote draft lines."""
    return plan_menu(db=db, aimo_client=aimo_client, principal=principal, req=req)
