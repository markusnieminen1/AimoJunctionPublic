"""Dev-only Agent Chaos & Policy Flight Simulator service (real service-layer execution)."""

from __future__ import annotations

import secrets
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import clock
from app.aimo.client import AimoClient
from app.audit import record_audit, verify_audit_chain
from app.auth import hash_secret
from app.errors import AppError, get_request_id
from app.ids import new_id
from app.models import Agent, Order
from app.schemas import (
    OrderApproveRequest,
    OrderCreateRequest,
    OrderDecisionRequest,
    PolicyDecision,
    Principal,
    PrincipalRef,
    QuoteCreateRequest,
    QuoteLineInput,
    SimulatorRunRequest,
    SimulatorRunResponse,
    SimulatorScenarioId,
    SimulatorScenarioOut,
    SimulatorScenariosResponse,
)
from app.services.order_gateway import MockAimoOrderGateway
from app.services.orders import approve_order, cancel_order, place_order, reconcile_orders
from app.services.quotes import create_quote
from app.services.substitutes import find_substitutes

SIMULATOR_AGENT_NAME = "Flight Simulator Agent"

SIMULATOR_SCENARIOS: tuple[SimulatorScenarioOut, ...] = (
    SimulatorScenarioOut(
        id="unit_trap",
        title="Bulk Unit Error Trap (500 ST > max 250)",
        summary=(
            "Agent quotes & submits 500 ST of 464306 against max_quantity_per_line=250. "
            "Caught in PENDING_APPROVAL instead of auto-buying."
        ),
        expected_rules=["R-LINE-QTY", "R-AUTO-LIMIT"],
        target_product_ids=["464306"],
        expected_outcome="PENDING_APPROVAL",
    ),
    SimulatorScenarioOut(
        id="diet_breach",
        title="Allergen & Diet Safety Block (M vs Dairy)",
        summary=(
            "Agent requests milk-free (M) quote containing dairy SKU 464306. "
            "Hard-blocked (403 POLICY_BLOCKED) before any order row is created."
        ),
        expected_rules=["R-HARD-DIET"],
        target_product_ids=["464306"],
        expected_outcome="BLOCKED (403)",
    ),
    SimulatorScenarioOut(
        id="gateway_timeout_recovery",
        title="Gateway Timeout & Lookup Reconciliation",
        summary=(
            "In-limit order hits scoped timeout_after_accept (SUBMISSION_UNKNOWN), "
            "reconciles via idempotent lookup, and transitions to PLACED without duplicating."
        ),
        expected_rules=["R-AGENT-WITHIN-LIMITS"],
        target_product_ids=["464306"],
        expected_outcome="SUBMISSION_UNKNOWN → PLACED",
    ),
    SimulatorScenarioOut(
        id="partial_and_cutoff",
        title="Finite Stock Shortage (469363) & Substitutes",
        summary=(
            "500 ST of 469363 exceeds 384 finite stock (PARTIAL), fires R-DELIVERY-RISK, "
            "and proposes ON_TIME diet-safe alternatives."
        ),
        expected_rules=["R-DELIVERY-RISK"],
        target_product_ids=["469363", "464306", "464398"],
        expected_outcome="APPROVAL_REQUIRED + ON_TIME swaps",
    ),
    SimulatorScenarioOut(
        id="tampered_approval_hash",
        title="Cryptographic Approval Content-Hash Guard (409)",
        summary=(
            "Attempts approve_order with a mutated content_hash. Rejected with 409 "
            "APPROVAL_CONTENT_CHANGED and cleans up probe order."
        ),
        expected_rules=["R-LINE-QTY", "R-AUTO-LIMIT"],
        target_product_ids=["464306"],
        expected_outcome="409 APPROVAL_CONTENT_CHANGED",
    ),
)

_SCENARIO_BY_ID: dict[SimulatorScenarioId, SimulatorScenarioOut] = {
    sc.id: sc for sc in SIMULATOR_SCENARIOS
}


def list_simulator_scenarios() -> SimulatorScenariosResponse:
    """Return all 5 canonical Flight Simulator scenarios."""
    return SimulatorScenariosResponse(scenarios=list(SIMULATOR_SCENARIOS))


def _upcoming_monday_iso() -> str:
    today = clock.now().date()
    days_ahead = (7 - today.weekday()) % 7 or 7
    return (today + timedelta(days=days_ahead)).isoformat()


def _ensure_simulator_agent(db: Session, user_principal: Principal) -> Principal:
    """Create or normalize a dedicated synthetic company agent without touching real budgets."""
    agent = db.scalar(
        select(Agent).where(
            Agent.company_id == user_principal.company_id,
            Agent.name == SIMULATOR_AGENT_NAME,
        )
    )
    now = clock.now()
    if agent is None:
        agent = Agent(
            id=new_id("agt_"),
            company_id=user_principal.company_id,
            name=SIMULATOR_AGENT_NAME,
            description="Scoped chaos probe agent created by the Audit Flight Simulator",
            secret_hash=hash_secret(f"aks_sim_{secrets.token_urlsafe(24)}"),
            status="active",
            auto_approve_limit_eur=500.0,
            max_order_value_eur=5000.0,
            daily_spend_limit_eur=50000.0,
            allowed_categories=[],
            max_quantity_per_line=250.0,
            can_place_orders=True,
            delegated_by_user_id=user_principal.id,
            expires_at=None,
            created_at=now,
            last_used_at=now,
        )
        db.add(agent)
        db.flush()
    else:
        agent.status = "active"
        agent.auto_approve_limit_eur = 500.0
        agent.max_order_value_eur = 5000.0
        agent.daily_spend_limit_eur = 50000.0
        agent.allowed_categories = []
        agent.max_quantity_per_line = 250.0
        agent.can_place_orders = True
        agent.expires_at = None
        agent.last_used_at = now
        db.flush()

    db.commit()
    return Principal(
        kind="agent",
        id=agent.id,
        name=agent.name,
        company_id=user_principal.company_id,
        delegated_by=PrincipalRef(id=user_principal.id, name=user_principal.name),
    )


def run_simulator_scenario(
    db: Session,
    aimo_client: AimoClient,
    user_principal: Principal,
    req: SimulatorRunRequest,
) -> SimulatorRunResponse:
    """Execute a live service-layer fault/policy scenario under a scoped simulator agent."""
    scenario = _SCENARIO_BY_ID.get(req.scenario_id)
    if scenario is None:
        raise AppError(
            "VALIDATION_ERROR",
            422,
            f"Unknown simulator scenario: {req.scenario_id}",
            details={"field": "scenario_id"},
        )

    req_id = get_request_id()
    delivery_date = _upcoming_monday_iso()
    sim_agent = _ensure_simulator_agent(db, user_principal)
    src_ctx = {"simulator_scenario": req.scenario_id, "routing_path": "core-api"}

    if req.scenario_id == "unit_trap":
        quote = create_quote(
            db=db,
            aimo_client=aimo_client,
            principal=sim_agent,
            req=QuoteCreateRequest(
                requested_delivery_date=delivery_date,
                lines=[QuoteLineInput(product_id="464306", quantity=500)],
            ),
            source_context=src_ctx,
        )
        status_code, order_body = place_order(
            db=db,
            aimo_client=aimo_client,
            principal=sim_agent,
            req=OrderCreateRequest(quote_id=quote.id, note="[simulator:unit_trap]"),
            raw_idempotency_key=f"sim-unit-trap-{new_id('')}",
            source_context=src_ctx,
        )
        policy = PolicyDecision.model_validate(order_body["policy"])
        fired_ids = {r.rule_id for r in policy.rules}
        passed = (
            status_code == 201
            and order_body["status"] == "PENDING_APPROVAL"
            and {"R-LINE-QTY", "R-AUTO-LIMIT"}.issubset(fired_ids)
        )
        record_audit(
            db,
            company_id=user_principal.company_id,
            actor=user_principal,
            action="simulator.run",
            entity_type="simulator",
            entity_id=req.scenario_id,
            outcome="ok",
            details={
                "simulator_scenario": req.scenario_id,
                "quote_id": quote.id,
                "order_id": order_body["id"],
                "passed": passed,
            },
        )
        db.commit()
        return SimulatorRunResponse(
            scenario_id=req.scenario_id,
            title=scenario.title,
            passed=passed,
            quote_id=quote.id,
            order_id=str(order_body["id"]),
            http_status=status_code,
            error_code=None,
            policy=policy,
            order_status_before_reconcile=None,
            order_status_after=str(order_body["status"]),
            gateway_reference=order_body.get("gateway_reference"),
            substitutes_preview=[],
            audit_chain=verify_audit_chain(db, user_principal.company_id),
            request_id=req_id,
        )

    if req.scenario_id == "diet_breach":
        quote = create_quote(
            db=db,
            aimo_client=aimo_client,
            principal=sim_agent,
            req=QuoteCreateRequest(
                requested_delivery_date=delivery_date,
                lines=[QuoteLineInput(product_id="464306", quantity=10)],
                diet="M",
            ),
            source_context=src_ctx,
        )
        http_status = 200
        error_code: str | None = None
        policy_out: PolicyDecision | None = quote.policy_preview
        try:
            place_order(
                db=db,
                aimo_client=aimo_client,
                principal=sim_agent,
                req=OrderCreateRequest(
                    quote_id=quote.id,
                    note="[simulator:diet_breach]",
                    diet="M",
                ),
                raw_idempotency_key=f"sim-diet-breach-{new_id('')}",
                source_context=src_ctx,
            )
        except AppError as err:
            http_status = err.status_code
            error_code = err.code
            if isinstance(err.details, dict) and "outcome" in err.details:
                policy_out = PolicyDecision.model_validate(err.details)

        fired_ids = {r.rule_id for r in (policy_out.rules if policy_out else [])}
        passed = (
            http_status == 403 and error_code == "POLICY_BLOCKED" and "R-HARD-DIET" in fired_ids
        )
        record_audit(
            db,
            company_id=user_principal.company_id,
            actor=user_principal,
            action="simulator.run",
            entity_type="simulator",
            entity_id=req.scenario_id,
            outcome="ok",
            details={
                "simulator_scenario": req.scenario_id,
                "quote_id": quote.id,
                "blocked": True,
                "passed": passed,
            },
        )
        db.commit()
        return SimulatorRunResponse(
            scenario_id=req.scenario_id,
            title=scenario.title,
            passed=passed,
            quote_id=quote.id,
            order_id=None,
            http_status=http_status,
            error_code=error_code,
            policy=policy_out,
            order_status_before_reconcile=None,
            order_status_after=None,
            gateway_reference=None,
            substitutes_preview=[],
            audit_chain=verify_audit_chain(db, user_principal.company_id),
            request_id=req_id,
        )

    if req.scenario_id == "gateway_timeout_recovery":
        quote = create_quote(
            db=db,
            aimo_client=aimo_client,
            principal=sim_agent,
            req=QuoteCreateRequest(
                requested_delivery_date=delivery_date,
                lines=[QuoteLineInput(product_id="464306", quantity=10)],
            ),
            source_context=src_ctx,
        )
        timeout_gw = MockAimoOrderGateway(mode_override="timeout_after_accept")
        status_code, order_body = place_order(
            db=db,
            aimo_client=aimo_client,
            principal=sim_agent,
            req=OrderCreateRequest(
                quote_id=quote.id,
                note="[simulator:gateway_timeout_recovery]",
            ),
            raw_idempotency_key=f"sim-gw-timeout-{new_id('')}",
            gateway=timeout_gw,
            source_context=src_ctx,
        )
        order_id = str(order_body["id"])
        status_before = str(order_body["status"])
        policy = PolicyDecision.model_validate(order_body["policy"])

        normal_gw = MockAimoOrderGateway(mode_override="none")
        reconcile_orders(db=db, gateway=normal_gw)
        refreshed = db.get(Order, order_id)
        status_after = refreshed.status if refreshed is not None else status_before
        gw_ref = refreshed.gateway_reference if refreshed is not None else None
        passed = (
            status_code == 201
            and status_before == "SUBMISSION_UNKNOWN"
            and status_after == "PLACED"
            and bool(gw_ref)
        )
        record_audit(
            db,
            company_id=user_principal.company_id,
            actor=user_principal,
            action="simulator.run",
            entity_type="simulator",
            entity_id=req.scenario_id,
            outcome="ok",
            details={
                "simulator_scenario": req.scenario_id,
                "order_id": order_id,
                "status_before": status_before,
                "status_after": status_after,
                "gateway_reference": gw_ref,
                "passed": passed,
            },
        )
        db.commit()
        return SimulatorRunResponse(
            scenario_id=req.scenario_id,
            title=scenario.title,
            passed=passed,
            quote_id=quote.id,
            order_id=order_id,
            http_status=200,
            error_code=None,
            policy=policy,
            order_status_before_reconcile=status_before,
            order_status_after=status_after,
            gateway_reference=gw_ref,
            substitutes_preview=[],
            audit_chain=verify_audit_chain(db, user_principal.company_id),
            request_id=req_id,
        )

    if req.scenario_id == "partial_and_cutoff":
        quote = create_quote(
            db=db,
            aimo_client=aimo_client,
            principal=sim_agent,
            req=QuoteCreateRequest(
                requested_delivery_date=delivery_date,
                lines=[QuoteLineInput(product_id="469363", quantity=500)],
            ),
            source_context=src_ctx,
        )
        subs = find_substitutes(
            db=db,
            aimo_client=aimo_client,
            product_id="469363",
            quantity=500.0,
            requested_delivery_date=delivery_date,
            customer_id=user_principal.company_id,
        )
        on_time_subs = [
            item for item in subs.items if item.quote_line.delivery_status == "ON_TIME"
        ][:4]
        fired_ids = {r.rule_id for r in quote.policy_preview.rules}
        passed = "R-DELIVERY-RISK" in fired_ids and len(on_time_subs) > 0
        record_audit(
            db,
            company_id=user_principal.company_id,
            actor=user_principal,
            action="simulator.run",
            entity_type="simulator",
            entity_id=req.scenario_id,
            outcome="ok",
            details={
                "simulator_scenario": req.scenario_id,
                "quote_id": quote.id,
                "substitutes_count": len(on_time_subs),
                "passed": passed,
            },
        )
        db.commit()
        return SimulatorRunResponse(
            scenario_id=req.scenario_id,
            title=scenario.title,
            passed=passed,
            quote_id=quote.id,
            order_id=None,
            http_status=200,
            error_code=None,
            policy=quote.policy_preview,
            order_status_before_reconcile=None,
            order_status_after=None,
            gateway_reference=None,
            substitutes_preview=on_time_subs,
            audit_chain=verify_audit_chain(db, user_principal.company_id),
            request_id=req_id,
        )

    # tampered_approval_hash
    quote = create_quote(
        db=db,
        aimo_client=aimo_client,
        principal=sim_agent,
        req=QuoteCreateRequest(
            requested_delivery_date=delivery_date,
            lines=[QuoteLineInput(product_id="464306", quantity=500)],
        ),
        source_context=src_ctx,
    )
    _, order_body = place_order(
        db=db,
        aimo_client=aimo_client,
        principal=sim_agent,
        req=OrderCreateRequest(
            quote_id=quote.id,
            note="[simulator:tampered_approval_hash]",
        ),
        raw_idempotency_key=f"sim-tamper-create-{new_id('')}",
        source_context=src_ctx,
    )
    order_id = str(order_body["id"])
    policy = PolicyDecision.model_validate(order_body["policy"])

    http_status = 200
    error_code = None
    try:
        approve_order(
            db=db,
            aimo_client=aimo_client,
            principal=user_principal,
            order_id=order_id,
            req=OrderApproveRequest(
                content_hash="sha256:tampered-mismatch-0000000000000000",
                note="Flight simulator tampered hash probe",
            ),
            raw_idempotency_key=f"sim-tamper-approve-{new_id('')}",
        )
    except AppError as err:
        http_status = err.status_code
        error_code = err.code

    cancelled = cancel_order(
        db=db,
        principal=user_principal,
        order_id=order_id,
        req=OrderDecisionRequest(note="Simulator cleanup after content-hash tamper test"),
    )
    passed = (
        http_status == 409
        and error_code == "APPROVAL_CONTENT_CHANGED"
        and cancelled.status == "CANCELLED"
    )
    record_audit(
        db,
        company_id=user_principal.company_id,
        actor=user_principal,
        action="simulator.run",
        entity_type="simulator",
        entity_id=req.scenario_id,
        outcome="ok",
        details={
            "simulator_scenario": req.scenario_id,
            "order_id": order_id,
            "tampered_rejected": http_status == 409,
            "cleaned_up_status": cancelled.status,
            "passed": passed,
        },
    )
    db.commit()
    return SimulatorRunResponse(
        scenario_id=req.scenario_id,
        title=scenario.title,
        passed=passed,
        quote_id=quote.id,
        order_id=order_id,
        http_status=http_status,
        error_code=error_code,
        policy=policy,
        order_status_before_reconcile=None,
        order_status_after=cancelled.status,
        gateway_reference=None,
        substitutes_preview=[],
        audit_chain=verify_audit_chain(db, user_principal.company_id),
        request_id=req_id,
    )
