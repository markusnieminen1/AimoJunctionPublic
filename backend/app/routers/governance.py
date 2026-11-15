"""Governance summary, routing boundary descriptor, and shadow policy simulator endpoints."""

from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.audit import verify_audit_chain
from app.auth import CurrentPrincipal, CurrentUser, DbSession, agent_to_out, is_agent_active
from app.errors import AppError
from app.governance_registry import build_layer1_invariants
from app.models import (
    Agent,
    Company,
    CompanyNegotiationSetting,
    Order,
    Product,
    PromotionRule,
)
from app.request_router import get_routing_descriptor
from app.routers.mcp import MCP_TOOLS
from app.schemas import (
    GovernanceRoutingResponse,
    GovernanceSummaryResponse,
    GovernanceToolInfoOut,
    OrderPolicyDiffOut,
    PolicyWhatIfRequest,
    PolicyWhatIfResponse,
    Principal,
    PrincipalRef,
    RecipeSanityWarning,
)
from app.services.assistant import TOOL_SPECS
from app.services.policy import evaluate_policy
from app.services.quotes import evaluate_diet_violations, line_model_to_schema

router = APIRouter(prefix="/governance", tags=["governance"])


@router.get("/summary", response_model=GovernanceSummaryResponse)
def get_governance_summary(
    principal: CurrentPrincipal,
    db: DbSession,
) -> GovernanceSummaryResponse:
    """Return active governance policies, agent roles, 3-gate invariants, tools & audit health."""
    company = db.get(Company, principal.company_id)
    company_name = company.name if company is not None else principal.company_id

    agents = list(
        db.scalars(
            select(Agent)
            .where(Agent.company_id == principal.company_id)
            .order_by(Agent.created_at.asc(), Agent.id.asc())
        ).all()
    )
    agent_roles = [agent_to_out(db, a) for a in agents]

    active_promotions_count = int(
        db.scalar(
            select(func.count())
            .select_from(PromotionRule)
            .where(
                PromotionRule.company_id == principal.company_id,
                PromotionRule.active.is_(True),
            )
        )
        or 0
    )

    audit_chain = verify_audit_chain(db, principal.company_id)
    neg_setting = db.get(CompanyNegotiationSetting, principal.company_id)

    policies = {
        "hard_diet_enforcement": True,
        "human_only_order_approval": True,
        "human_only_promotion_management": True,
        "human_only_admin_campaigns": True,
        "human_only_agent_management": True,
        "human_only_telegram_opt_in": True,
        "allow_price_negotiation": bool(neg_setting.allow_price_negotiation)
        if neg_setting is not None
        else False,
        "deterministic_request_routing": True,
        "quote_lock_minutes": 30,
        "active_agent_count": sum(1 for a in agents if is_agent_active(a)),
        "quarantined_agent_count": sum(1 for a in agents if a.status == "quarantined"),
        "total_agent_count": len(agent_roles),
    }

    assistant_tools = [
        GovernanceToolInfoOut(name=spec.name, description=spec.description) for spec in TOOL_SPECS
    ]
    mcp_tools = [
        GovernanceToolInfoOut(
            name=str(tool["name"]),
            description=str(tool.get("description") or ""),
        )
        for tool in MCP_TOOLS
    ]

    return GovernanceSummaryResponse(
        company_id=principal.company_id,
        company_name=company_name,
        policies=policies,
        agent_roles=agent_roles,
        layer1_invariants=build_layer1_invariants(),
        assistant_tools=assistant_tools,
        mcp_tools=mcp_tools,
        active_promotions_count=active_promotions_count,
        audit_chain=audit_chain,
    )


@router.get("/routing", response_model=GovernanceRoutingResponse)
def get_governance_routing(
    _principal: CurrentPrincipal,
) -> GovernanceRoutingResponse:
    """Return the deterministic Request-Field Router vs. Agentic Service boundary descriptor."""
    return get_routing_descriptor()


@router.post("/simulate-policy", response_model=PolicyWhatIfResponse)
def post_simulate_policy(
    payload: PolicyWhatIfRequest,
    principal: CurrentUser,
    db: DbSession,
) -> PolicyWhatIfResponse:
    """Replay up to 20 recent company orders read-only against proposed agent spend/scope limits."""
    agent = db.scalar(
        select(Agent).where(
            Agent.id == payload.agent_id,
            Agent.company_id == principal.company_id,
        )
    )
    if agent is None:
        raise AppError("NOT_FOUND", 404, "Agent not found")

    sample_orders = list(
        db.scalars(
            select(Order)
            .options(selectinload(Order.lines))
            .where(Order.company_id == principal.company_id)
            .order_by(Order.created_at.desc(), Order.id.desc())
            .limit(payload.sample_size)
        ).all()
    )

    all_pids = {ln.product_id for ord_row in sample_orders for ln in ord_row.lines}
    products = db.scalars(select(Product).where(Product.id.in_(all_pids))).all() if all_pids else []
    product_map = {p.id: p for p in products}
    product_categories = {p.id: p.category for p in products}

    # Ephemeral detached shadow Agent — NEVER added to db session!
    shadow_agent = Agent(
        id=agent.id,
        company_id=agent.company_id,
        delegated_by_user_id=agent.delegated_by_user_id,
        name=agent.name,
        description=agent.description,
        status="active",
        secret_hash=agent.secret_hash,
        auto_approve_limit_eur=payload.auto_approve_limit_eur,
        max_order_value_eur=payload.max_order_value_eur,
        daily_spend_limit_eur=payload.daily_spend_limit_eur,
        allowed_categories=(
            list(payload.allowed_categories)
            if payload.allowed_categories is not None
            else list(agent.allowed_categories or [])
        ),
        max_quantity_per_line=payload.max_quantity_per_line,
        can_place_orders=agent.can_place_orders,
        expires_at=agent.expires_at,
        created_at=agent.created_at,
        last_used_at=agent.last_used_at,
    )

    sim_principal = Principal(
        kind="agent",
        id=agent.id,
        name=agent.name,
        company_id=principal.company_id,
        delegated_by=PrincipalRef(id=principal.id, name=principal.name),
    )

    unchanged_count = 0
    newly_auto_approved = 0
    newly_requiring_approval = 0
    newly_blocked = 0
    safety_blocks_preserved = 0
    diffs: list[OrderPolicyDiffOut] = []

    for ord_row in sample_orders:
        lines = [line_model_to_schema(ln) for ln in ord_row.lines]
        raw_policy = ord_row.policy or {}
        stored_diet = raw_policy.get("_diet")
        diet_str = stored_diet if isinstance(stored_diet, str) else None
        diet_violations = evaluate_diet_violations(product_map, lines, diet_str)

        raw_rw = raw_policy.get("_recipe_sanity_warning")
        recipe_warning = (
            RecipeSanityWarning.model_validate(raw_rw) if isinstance(raw_rw, dict) else None
        )

        before_dec = evaluate_policy(
            principal=sim_principal,
            agent=agent,
            lines=lines,
            product_categories=product_categories,
            total_excl_vat_eur=ord_row.total_excl_vat_eur,
            spent_today_eur=0.0,
            diet_violations=diet_violations or None,
            recipe_sanity_warning=recipe_warning,
        )
        after_dec = evaluate_policy(
            principal=sim_principal,
            agent=shadow_agent,
            lines=lines,
            product_categories=product_categories,
            total_excl_vat_eur=ord_row.total_excl_vat_eur,
            spent_today_eur=0.0,
            diet_violations=diet_violations or None,
            recipe_sanity_warning=recipe_warning,
        )

        before_rules = [r.rule_id for r in before_dec.rules]
        after_rules = [r.rule_id for r in after_dec.rules]
        had_safety_lock = any(rid in ("R-HARD-DIET", "R-NOT-DELIVERABLE") for rid in before_rules)
        if had_safety_lock and after_dec.outcome == "BLOCKED":
            safety_blocks_preserved += 1

        changed = before_dec.outcome != after_dec.outcome
        if not changed:
            unchanged_count += 1
        elif after_dec.outcome == "AUTO":
            newly_auto_approved += 1
        elif after_dec.outcome == "APPROVAL_REQUIRED":
            newly_requiring_approval += 1
        elif after_dec.outcome == "BLOCKED":
            newly_blocked += 1

        diffs.append(
            OrderPolicyDiffOut(
                order_id=ord_row.id,
                total_excl_vat_eur=ord_row.total_excl_vat_eur,
                before_outcome=before_dec.outcome,
                after_outcome=after_dec.outcome,
                before_rules=before_rules,
                after_rules=after_rules,
                changed=changed,
            )
        )

    return PolicyWhatIfResponse(
        evaluated_orders=len(sample_orders),
        unchanged_count=unchanged_count,
        newly_auto_approved=newly_auto_approved,
        newly_requiring_approval=newly_requiring_approval,
        newly_blocked=newly_blocked,
        safety_blocks_preserved=safety_blocks_preserved,
        diffs=diffs,
    )
