"""Audit event recording with per-company tamper-evident hash chaining."""

import hashlib
import json
from typing import Any, Literal, cast

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import clock
from app.errors import request_id_var
from app.ids import new_id
from app.models import AuditEventModel
from app.schemas import AuditEventOut, AuditVerifyResponse, Principal


def _compute_audit_hash(prev_hash: str | None, payload: dict[str, Any]) -> str:
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )
    prefix = prev_hash or ""
    return hashlib.sha256(f"{prefix}{canonical}".encode()).hexdigest()


def record_audit(
    db: Session,
    *,
    company_id: str,
    actor: Principal | None,
    action: str,
    entity_type: str,
    entity_id: str | None,
    outcome: Literal["ok", "error"] = "ok",
    details: dict[str, Any] | None = None,
) -> AuditEventModel:
    """Append a tamper-evident audit event to the database session and flush it."""
    event_id = new_id("aud_")
    at = clock.now()
    req_id = request_id_var.get() or new_id("req_")
    actor_dict = actor.model_dump(mode="json") if actor is not None else None
    actor_id = actor.id if actor is not None else None
    details_dict = details if details is not None else {}

    prev_hash = db.scalar(
        select(AuditEventModel.hash)
        .where(AuditEventModel.company_id == company_id)
        .order_by(AuditEventModel.at.desc(), AuditEventModel.id.desc())
        .limit(1)
    )

    hash_payload: dict[str, Any] = {
        "id": event_id,
        "company_id": company_id,
        "at": clock.format_helsinki_iso(at),
        "request_id": req_id,
        "actor": actor_dict,
        "action": action,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "outcome": outcome,
        "details": details_dict,
    }
    event_hash = _compute_audit_hash(prev_hash, hash_payload)

    row = AuditEventModel(
        id=event_id,
        company_id=company_id,
        at=at,
        request_id=req_id,
        actor_id=actor_id,
        actor=actor_dict,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        outcome=outcome,
        details=details_dict,
        prev_hash=prev_hash,
        hash=event_hash,
    )
    db.add(row)
    db.flush()
    return row


def audit_event_to_out(row: AuditEventModel) -> AuditEventOut:
    """Convert an AuditEventModel ORM row to an AuditEventOut schema."""
    actor = Principal.model_validate(row.actor) if row.actor is not None else None
    outcome = cast(Literal["ok", "error"], row.outcome if row.outcome in ("ok", "error") else "ok")
    return AuditEventOut(
        id=row.id,
        at=row.at,
        request_id=row.request_id,
        actor=actor,
        action=row.action,
        entity_type=row.entity_type,
        entity_id=row.entity_id,
        outcome=outcome,
        details=row.details or {},
    )


def verify_audit_chain(db: Session, company_id: str) -> AuditVerifyResponse:
    """Verify prev_hash linkage and recomputed SHA-256 hash across a company's audit log."""
    rows = db.scalars(
        select(AuditEventModel)
        .where(AuditEventModel.company_id == company_id)
        .order_by(AuditEventModel.at.asc(), AuditEventModel.id.asc())
    ).all()

    expected_prev_hash: str | None = None
    checked_events = 0
    for row in rows:
        checked_events += 1
        if row.prev_hash != expected_prev_hash:
            return AuditVerifyResponse(
                valid=False,
                checked_events=checked_events,
                broken_event_id=row.id,
            )

        hash_payload: dict[str, Any] = {
            "id": row.id,
            "company_id": row.company_id,
            "at": clock.format_helsinki_iso(row.at),
            "request_id": row.request_id,
            "actor": row.actor,
            "action": row.action,
            "entity_type": row.entity_type,
            "entity_id": row.entity_id,
            "outcome": row.outcome,
            "details": row.details if row.details is not None else {},
        }
        recomputed = _compute_audit_hash(row.prev_hash, hash_payload)
        if not row.hash or row.hash != recomputed:
            return AuditVerifyResponse(
                valid=False,
                checked_events=checked_events,
                broken_event_id=row.id,
            )
        expected_prev_hash = row.hash

    return AuditVerifyResponse(
        valid=True,
        checked_events=checked_events,
        broken_event_id=None,
    )
