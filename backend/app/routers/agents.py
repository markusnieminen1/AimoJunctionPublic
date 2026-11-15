"""Agent management endpoints (user-only, strictly company-isolated)."""

import secrets
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Query, status
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app import clock
from app.audit import audit_event_to_out, record_audit
from app.auth import CurrentUser, DbSession, agent_to_out, hash_secret
from app.errors import AppError
from app.ids import new_id
from app.models import Agent, AuditEventModel, User
from app.schemas import (
    AgentAnomaliesResponse,
    AgentCreateRequest,
    AgentCreateResponse,
    AgentListResponse,
    AgentOut,
    AgentPatchRequest,
    AgentQuarantineReviewRequest,
    AgentQuarantineReviewResponse,
    AuditListResponse,
)
from app.services.inventory_holds import release_holds_for_agent
from app.services.purchasing_guard import list_agent_anomalies, review_quarantined_agent

router = APIRouter(prefix="/agents", tags=["agents"])


def _generate_agent_secret() -> str:
    return f"aks_{secrets.token_urlsafe(32)}"


def _get_company_agent(db: Session, agent_id: str, company_id: str) -> Agent:
    agent = db.scalar(select(Agent).where(Agent.id == agent_id, Agent.company_id == company_id))
    if agent is None:
        raise AppError("NOT_FOUND", 404, "Agent not found")
    return agent


@router.get("", response_model=AgentListResponse)
def list_agents(
    principal: CurrentUser,
    db: DbSession,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> AgentListResponse:
    """List agents belonging to the caller's company."""
    base_filter = Agent.company_id == principal.company_id
    total = db.scalar(select(func.count()).select_from(Agent).where(base_filter)) or 0
    rows = db.scalars(
        select(Agent)
        .where(base_filter)
        .order_by(Agent.created_at.desc(), Agent.id.desc())
        .offset(offset)
        .limit(limit)
    ).all()

    delegator_ids = {row.delegated_by_user_id for row in rows}
    delegators: dict[str, User] = {}
    if delegator_ids:
        for user in db.scalars(select(User).where(User.id.in_(delegator_ids))).all():
            delegators[user.id] = user

    items = [
        agent_to_out(db, row, delegator=delegators.get(row.delegated_by_user_id)) for row in rows
    ]
    return AgentListResponse(items=items, total=total)


@router.post("", response_model=AgentCreateResponse, status_code=status.HTTP_201_CREATED)
def create_agent(
    payload: AgentCreateRequest,
    principal: CurrentUser,
    db: DbSession,
) -> AgentCreateResponse:
    """Create a delegated purchasing agent and return its one-time secret."""
    now = clock.now()
    plain_secret = _generate_agent_secret()
    agent = Agent(
        id=new_id("agt_"),
        company_id=principal.company_id,
        delegated_by_user_id=principal.id,
        name=payload.name,
        description=payload.description,
        status="active",
        secret_hash=hash_secret(plain_secret),
        auto_approve_limit_eur=payload.auto_approve_limit_eur,
        max_order_value_eur=payload.max_order_value_eur,
        daily_spend_limit_eur=payload.daily_spend_limit_eur,
        allowed_categories=list(payload.allowed_categories),
        max_quantity_per_line=payload.max_quantity_per_line,
        can_place_orders=payload.can_place_orders,
        expires_at=payload.expires_at,
        created_at=now,
        last_used_at=None,
    )
    db.add(agent)
    db.flush()

    record_audit(
        db,
        company_id=principal.company_id,
        actor=principal,
        action="agent.created",
        entity_type="agent",
        entity_id=agent.id,
        outcome="ok",
        details={
            "name": agent.name,
            "delegated_by_user_id": principal.id,
            "auto_approve_limit_eur": agent.auto_approve_limit_eur,
            "max_order_value_eur": agent.max_order_value_eur,
            "daily_spend_limit_eur": agent.daily_spend_limit_eur,
            "can_place_orders": agent.can_place_orders,
        },
    )
    db.commit()

    return AgentCreateResponse(
        agent=agent_to_out(db, agent, spent_today_eur=0.0),
        agent_secret=plain_secret,
    )


@router.get("/anomalies", response_model=AgentAnomaliesResponse)
def get_agent_anomalies(
    principal: CurrentUser,
    db: DbSession,
) -> AgentAnomaliesResponse:
    """List quarantined/anomalous agents in the caller's company awaiting human review."""
    return list_agent_anomalies(db, principal.company_id)


@router.get("/{agent_id}", response_model=AgentOut)
def get_agent(
    agent_id: str,
    principal: CurrentUser,
    db: DbSession,
) -> AgentOut:
    """Fetch a single agent within the caller's company."""
    agent = _get_company_agent(db, agent_id, principal.company_id)
    return agent_to_out(db, agent)


@router.patch("/{agent_id}", response_model=AgentOut)
def patch_agent(
    agent_id: str,
    payload: AgentPatchRequest,
    principal: CurrentUser,
    db: DbSession,
) -> AgentOut:
    """Update limits, status, or metadata on an agent in the caller's company."""
    agent = _get_company_agent(db, agent_id, principal.company_id)
    changes: dict[str, Any] = {}

    for field_name in sorted(payload.model_fields_set):
        new_value = getattr(payload, field_name)
        old_value = getattr(agent, field_name)
        if field_name == "allowed_categories" and new_value is not None:
            new_value = list(new_value)
        setattr(agent, field_name, new_value)
        changes[field_name] = {
            "before": clock.format_helsinki_iso(old_value)
            if isinstance(old_value, datetime)
            else old_value,
            "after": clock.format_helsinki_iso(new_value)
            if isinstance(new_value, datetime)
            else new_value,
        }

    if agent.status in ("disabled", "quarantined"):
        release_holds_for_agent(db, agent.id)

    record_audit(
        db,
        company_id=principal.company_id,
        actor=principal,
        action="agent.updated",
        entity_type="agent",
        entity_id=agent.id,
        outcome="ok",
        details={"updated_fields": sorted(payload.model_fields_set), "changes": changes},
    )
    db.commit()

    return agent_to_out(db, agent)


@router.post("/{agent_id}/rotate-secret", response_model=AgentCreateResponse)
def rotate_agent_secret(
    agent_id: str,
    principal: CurrentUser,
    db: DbSession,
) -> AgentCreateResponse:
    """Rotate an agent's secret, immediately invalidating the previous secret."""
    agent = _get_company_agent(db, agent_id, principal.company_id)
    plain_secret = _generate_agent_secret()
    agent.secret_hash = hash_secret(plain_secret)

    record_audit(
        db,
        company_id=principal.company_id,
        actor=principal,
        action="agent.secret_rotated",
        entity_type="agent",
        entity_id=agent.id,
        outcome="ok",
        details={},
    )
    db.commit()

    return AgentCreateResponse(
        agent=agent_to_out(db, agent),
        agent_secret=plain_secret,
    )


@router.post("/{agent_id}/review", response_model=AgentQuarantineReviewResponse)
def post_review_quarantined_agent(
    agent_id: str,
    payload: AgentQuarantineReviewRequest,
    principal: CurrentUser,
    db: DbSession,
) -> AgentQuarantineReviewResponse:
    """Human-review a quarantined agent (`restore` to active or `revoke` permanently)."""
    return review_quarantined_agent(db, principal, agent_id, payload)


@router.get("/{agent_id}/activity", response_model=AuditListResponse)
def get_agent_activity(
    agent_id: str,
    principal: CurrentUser,
    db: DbSession,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> AuditListResponse:
    """List audit events acted by or targeting a specific agent."""
    agent = _get_company_agent(db, agent_id, principal.company_id)
    activity_filter = and_(
        AuditEventModel.company_id == principal.company_id,
        or_(
            AuditEventModel.actor_id == agent.id,
            and_(
                AuditEventModel.entity_type == "agent",
                AuditEventModel.entity_id == agent.id,
            ),
        ),
    )
    total = db.scalar(select(func.count()).select_from(AuditEventModel).where(activity_filter)) or 0
    rows = db.scalars(
        select(AuditEventModel)
        .where(activity_filter)
        .order_by(AuditEventModel.at.desc(), AuditEventModel.id.desc())
        .offset(offset)
        .limit(limit)
    ).all()

    return AuditListResponse(
        items=[audit_event_to_out(row) for row in rows],
        total=total,
    )
