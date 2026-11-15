"""Audit log query endpoint (user-only, company-isolated)."""

from fastapi import APIRouter, Query
from sqlalchemy import func, select

from app.audit import audit_event_to_out, verify_audit_chain
from app.auth import CurrentUser, DbSession
from app.models import AuditEventModel
from app.schemas import AuditListResponse, AuditVerifyResponse

router = APIRouter(prefix="/audit", tags=["audit"])


@router.get("", response_model=AuditListResponse)
def list_audit_events(
    principal: CurrentUser,
    db: DbSession,
    entity_type: str | None = None,
    entity_id: str | None = None,
    actor_id: str | None = None,
    request_id: str | None = None,
    action: str | None = None,
    outcome: str | None = None,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> AuditListResponse:
    """Query company-isolated audit events with optional entity, actor, action & outcome filters."""
    filters = [AuditEventModel.company_id == principal.company_id]
    if entity_type is not None:
        filters.append(AuditEventModel.entity_type == entity_type)
    if entity_id is not None:
        filters.append(AuditEventModel.entity_id == entity_id)
    if actor_id is not None:
        filters.append(AuditEventModel.actor_id == actor_id)
    if request_id is not None:
        filters.append(AuditEventModel.request_id == request_id)
    if action is not None:
        filters.append(AuditEventModel.action == action)
    if outcome is not None:
        filters.append(AuditEventModel.outcome == outcome)

    total = db.scalar(select(func.count()).select_from(AuditEventModel).where(*filters)) or 0
    rows = db.scalars(
        select(AuditEventModel)
        .where(*filters)
        .order_by(AuditEventModel.at.desc(), AuditEventModel.id.desc())
        .offset(offset)
        .limit(limit)
    ).all()

    return AuditListResponse(
        items=[audit_event_to_out(row) for row in rows],
        total=total,
    )


@router.get("/verify", response_model=AuditVerifyResponse)
def verify_company_audit_chain(
    principal: CurrentUser,
    db: DbSession,
) -> AuditVerifyResponse:
    """Verify tamper-evident SHA-256 hash linkage across the company's audit events."""
    return verify_audit_chain(db, principal.company_id)
