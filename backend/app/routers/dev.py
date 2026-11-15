"""Dev-only endpoints for live demonstration (mounted only when APP_ENV=dev)."""

from typing import Annotated

from fastapi import APIRouter, Depends

from app.aimo.client import AimoClient, get_aimo_client
from app.audit import record_audit
from app.auth import CurrentUser, DbSession
from app.schemas import (
    GatewayModeRequest,
    GatewayModeResponse,
    SimulatorRunRequest,
    SimulatorRunResponse,
    SimulatorScenariosResponse,
)
from app.services.order_gateway import get_gateway_simulate_mode, set_gateway_simulate_mode
from app.services.simulator import list_simulator_scenarios, run_simulator_scenario

router = APIRouter(prefix="/dev", tags=["dev"])


@router.post("/gateway-mode", response_model=GatewayModeResponse)
def post_gateway_mode(
    payload: GatewayModeRequest,
    principal: CurrentUser,
    db: DbSession,
) -> GatewayModeResponse:
    """Set the simulated order gateway failure mode at runtime (dev-only, user-only)."""
    active = set_gateway_simulate_mode(payload.mode)
    record_audit(
        db,
        company_id=principal.company_id,
        actor=principal,
        action="dev.gateway_mode",
        entity_type="gateway",
        entity_id=None,
        outcome="ok",
        details={"mode": active},
    )
    db.commit()
    return GatewayModeResponse(mode=get_gateway_simulate_mode())


@router.get("/simulator/scenarios", response_model=SimulatorScenariosResponse)
def get_simulator_scenarios(
    _principal: CurrentUser,
) -> SimulatorScenariosResponse:
    """List canonical Flight Simulator fault/policy scenarios (dev-only, user-only)."""
    return list_simulator_scenarios()


@router.post("/simulator/run", response_model=SimulatorRunResponse)
def post_simulator_run(
    payload: SimulatorRunRequest,
    principal: CurrentUser,
    db: DbSession,
    client: Annotated[AimoClient, Depends(get_aimo_client)],
) -> SimulatorRunResponse:
    """Run a single Flight Simulator scenario end-to-end (dev-only, user-only)."""
    return run_simulator_scenario(
        db=db,
        aimo_client=client,
        user_principal=principal,
        req=payload,
    )
