"""Purchasing characteristics spam, injection, and policy-probing auto-quarantine guard."""

from __future__ import annotations

import secrets
import statistics
from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app import clock
from app.audit import record_audit
from app.auth import agent_to_out, compute_agent_spent_today, hash_secret
from app.errors import AppError, request_id_var
from app.models import Agent, AuditEventModel, Order
from app.schemas import (
    AgentAnomaliesResponse,
    AgentQuarantineReviewRequest,
    AgentQuarantineReviewResponse,
    Principal,
    QuarantinedAgentAlertOut,
    QuoteLine,
)
from app.services.inventory_holds import release_holds_for_agent

ROLLING_WINDOW_MINUTES = 10
DUPLICATE_HASH_SPAM_THRESHOLD = 3
REPEATED_INJECTION_STRIKE_THRESHOLD = 3
CONSECUTIVE_BLOCKS_THRESHOLD = 4
STANDING_DRIFT_MIN_PRIOR_ORDERS = 2
STANDING_DRIFT_RATIO_THRESHOLD = 1.25

_INJECTION_ACTIONS = (
    "assistant.injection_screen",
    "a2a.injection_screen",
    "order.injection_screen",
)
_ORDER_OUTCOME_ACTIONS = (
    "order.blocked",
    "order.created",
    "order.placed",
    "order.pending_approval",
    "order.submission_unknown",
)


def _agent_window_start(db: Session, company_id: str, agent_id: str, now: datetime) -> datetime:
    """Return max(now - 10m, last human restore/activation timestamp) in Helsinki tz."""
    base_start = now - timedelta(minutes=ROLLING_WINDOW_MINUTES)
    recent_reviews = db.scalars(
        select(AuditEventModel)
        .where(
            AuditEventModel.company_id == company_id,
            AuditEventModel.entity_type == "agent",
            AuditEventModel.entity_id == agent_id,
            AuditEventModel.action.in_(("agent.quarantine_reviewed", "agent.updated")),
        )
        .order_by(AuditEventModel.at.desc(), AuditEventModel.id.desc())
        .limit(10)
    ).all()

    for ev in recent_reviews:
        ev_at = clock.to_helsinki(ev.at)
        details = ev.details or {}
        if ev.action == "agent.quarantine_reviewed" and details.get("decision") == "restore":
            return max(base_start, ev_at)
        changes = details.get("changes")
        if (
            ev.action == "agent.updated"
            and isinstance(changes, dict)
            and isinstance(changes.get("status"), dict)
            and changes["status"].get("after") == "active"
        ):
            return max(base_start, ev_at)
    return base_start


def _quarantine_agent(
    db: Session,
    *,
    agent: Agent,
    principal: Principal,
    rule_code: str,
    trigger_reason: str,
    characteristics: dict[str, Any],
    recent_request_ids: list[str],
) -> None:
    """Set agent.status = 'quarantined' and append a tamper-evident audit event."""
    now = clock.now()
    agent.status = "quarantined"
    release_holds_for_agent(db, agent.id, now=now)
    req_id = request_id_var.get() or None
    req_ids = list(
        dict.fromkeys([*(r for r in recent_request_ids if r), *([req_id] if req_id else [])])
    )
    record_audit(
        db,
        company_id=agent.company_id,
        actor=principal,
        action="agent.quarantined",
        entity_type="agent",
        entity_id=agent.id,
        outcome="error",
        details={
            "request_id": req_id,
            "agent_id": agent.id,
            "agent_name": agent.name,
            "status": "quarantined",
            "rule_code": rule_code,
            "trigger_reason": trigger_reason,
            "quarantined_at": clock.format_helsinki_iso(now),
            "purchasing_characteristics": characteristics,
            "recent_request_ids": req_ids[:10],
        },
    )
    db.flush()


def check_duplicate_order_spam_and_quarantine(
    db: Session,
    *,
    principal: Principal,
    agent: Agent | None,
    candidate_content_hash: str,
    total_excl_vat_eur: float,
) -> None:
    """Quarantine agent if >=3 identical-fingerprint orders arrive with distinct keys in 10m."""
    if principal.kind != "agent" or agent is None:
        return

    now = clock.now()
    window_start = _agent_window_start(db, principal.company_id, agent.id, now)
    recent_orders = db.scalars(
        select(Order)
        .where(
            Order.company_id == principal.company_id,
            Order.created_by_kind == "agent",
            Order.created_by_id == agent.id,
        )
        .order_by(Order.created_at.desc(), Order.id.desc())
        .limit(40)
    ).all()

    in_window = [o for o in recent_orders if clock.to_helsinki(o.created_at) >= window_start]
    same_hash = [o for o in in_window if o.content_hash == candidate_content_hash]

    # Including the current distinct-Idempotency-Key attempt: len(same_hash) + 1 >= 3
    attempt_number = len(same_hash) + 1
    if attempt_number < DUPLICATE_HASH_SPAM_THRESHOLD:
        return

    characteristics = {
        "duplicate_fingerprint_attempts_10m": attempt_number,
        "orders_in_10m_window": len(in_window) + 1,
        "content_hash_prefix": candidate_content_hash[:16],
        "attempted_order_total_eur": round(float(total_excl_vat_eur), 2),
        "auto_approve_limit_eur": round(float(agent.auto_approve_limit_eur), 2),
        "spent_today_eur": compute_agent_spent_today(db, agent.id, agent.company_id),
    }
    trigger_reason = (
        f"Submitted {attempt_number} identical cart-fingerprint orders across distinct "
        f"Idempotency-Keys within {ROLLING_WINDOW_MINUTES} minutes "
        f"(hash {candidate_content_hash[:12]}...)."
    )
    _quarantine_agent(
        db,
        agent=agent,
        principal=principal,
        rule_code="R-SPAM-DUPLICATE-FINGERPRINT",
        trigger_reason=trigger_reason,
        characteristics=characteristics,
        recent_request_ids=[],
    )
    db.commit()
    raise AppError(
        "AGENT_DISABLED",
        403,
        f"Agent quarantined: {trigger_reason}",
        details={
            "agent_id": agent.id,
            "status": "quarantined",
            "rule_code": "R-SPAM-DUPLICATE-FINGERPRINT",
            "purchasing_characteristics": characteristics,
        },
    )


def check_repeated_injection_and_quarantine(
    db: Session,
    *,
    principal: Principal,
    reason: str,
) -> bool:
    """On >=3 prompt-injection strikes in 10m for an agent, quarantine and raise 403 AGENT_DISABLED.

    Strikes 1-2 return False without quarantining so single-turn resilience checks receive a clean
    governed refusal (200 OK, 0 auto-placed orders).
    """
    if principal.kind != "agent":
        return False

    agent = db.get(Agent, principal.id)
    if agent is None:
        return False

    now = clock.now()
    window_start = _agent_window_start(db, principal.company_id, agent.id, now)
    recent_events = db.scalars(
        select(AuditEventModel)
        .where(
            AuditEventModel.company_id == principal.company_id,
            AuditEventModel.actor_id == agent.id,
            AuditEventModel.action.in_(_INJECTION_ACTIONS),
        )
        .order_by(AuditEventModel.at.desc(), AuditEventModel.id.desc())
        .limit(20)
    ).all()

    strikes = [ev for ev in recent_events if clock.to_helsinki(ev.at) >= window_start]
    if len(strikes) < REPEATED_INJECTION_STRIKE_THRESHOLD:
        return False

    characteristics = {
        "injection_strikes_10m": len(strikes),
        "latest_injection_reason": reason,
        "spent_today_eur": compute_agent_spent_today(db, agent.id, agent.company_id),
        "auto_approve_limit_eur": round(float(agent.auto_approve_limit_eur), 2),
    }
    trigger_reason = (
        f"Triggered {len(strikes)} prompt-injection / policy-bypass strikes within "
        f"{ROLLING_WINDOW_MINUTES} minutes ({reason})."
    )
    _quarantine_agent(
        db,
        agent=agent,
        principal=principal,
        rule_code="R-SEMANTIC-INJECTION-QUARANTINE",
        trigger_reason=trigger_reason,
        characteristics=characteristics,
        recent_request_ids=[ev.request_id for ev in strikes if ev.request_id],
    )
    db.commit()
    raise AppError(
        "AGENT_DISABLED",
        403,
        f"Agent quarantined: {trigger_reason}",
        details={
            "agent_id": agent.id,
            "status": "quarantined",
            "rule_code": "R-SEMANTIC-INJECTION-QUARANTINE",
            "purchasing_characteristics": characteristics,
        },
    )


def check_consecutive_policy_blocks_and_quarantine(
    db: Session,
    *,
    principal: Principal,
    agent: Agent | None,
    latest_fired_rules: list[str],
) -> bool:
    """Quarantine agent in DB after >=4 consecutive POLICY_BLOCKED attempts in 10m."""
    if principal.kind != "agent" or agent is None:
        return False

    now = clock.now()
    window_start = _agent_window_start(db, principal.company_id, agent.id, now)
    recent_events = db.scalars(
        select(AuditEventModel)
        .where(
            AuditEventModel.company_id == principal.company_id,
            AuditEventModel.actor_id == agent.id,
            AuditEventModel.action.in_(_ORDER_OUTCOME_ACTIONS),
        )
        .order_by(AuditEventModel.at.desc(), AuditEventModel.id.desc())
        .limit(20)
    ).all()

    consecutive_blocks: list[AuditEventModel] = []
    for ev in recent_events:
        if clock.to_helsinki(ev.at) < window_start:
            break
        if ev.action == "order.blocked":
            consecutive_blocks.append(ev)
        else:
            break

    if len(consecutive_blocks) < CONSECUTIVE_BLOCKS_THRESHOLD:
        return False

    characteristics = {
        "consecutive_policy_blocks_10m": len(consecutive_blocks),
        "latest_fired_rules": latest_fired_rules,
        "spent_today_eur": compute_agent_spent_today(db, agent.id, agent.company_id),
        "max_order_value_eur": round(float(agent.max_order_value_eur), 2),
    }
    trigger_reason = (
        f"Hit {len(consecutive_blocks)} consecutive POLICY_BLOCKED violations within "
        f"{ROLLING_WINDOW_MINUTES} minutes (latest rules: {', '.join(latest_fired_rules)})."
    )
    _quarantine_agent(
        db,
        agent=agent,
        principal=principal,
        rule_code="R-POLICY-PROBE-QUARANTINE",
        trigger_reason=trigger_reason,
        characteristics=characteristics,
        recent_request_ids=[ev.request_id for ev in consecutive_blocks if ev.request_id],
    )
    return True


def evaluate_standing_order_drift(
    db: Session,
    *,
    company_id: str,
    lines: Sequence[QuoteLine],
) -> tuple[str | None, list[str]]:
    """Detect >25% portion or line-spend creep vs >=2 prior-day PLACED orders for the same SKU."""
    if not lines:
        return None, []

    start_of_today = clock.now().replace(hour=0, minute=0, second=0, microsecond=0)
    placed_orders = db.scalars(
        select(Order)
        .options(selectinload(Order.lines))
        .where(
            Order.company_id == company_id,
            Order.status == "PLACED",
        )
        .order_by(Order.created_at.desc(), Order.id.desc())
        .limit(100)
    ).all()

    prior_day_orders = [
        o for o in placed_orders if clock.to_helsinki(o.created_at) < start_of_today
    ]
    if len(prior_day_orders) < STANDING_DRIFT_MIN_PRIOR_ORDERS:
        return None, []

    sku_history: dict[str, list[tuple[float, float | None]]] = {}
    for hist_order in prior_day_orders:
        for hist_ln in hist_order.lines:
            sku_history.setdefault(hist_ln.product_id, []).append(
                (float(hist_ln.quantity), hist_ln.line_total_eur)
            )

    drift_notes: list[str] = []
    drifted_ids: list[str] = []
    for line in lines:
        samples = sku_history.get(line.product_id, [])
        if len(samples) < STANDING_DRIFT_MIN_PRIOR_ORDERS:
            continue

        median_qty = float(statistics.median(q for q, _ in samples))
        eur_samples = [float(e) for _, e in samples if e is not None and float(e) > 0]
        median_eur = float(statistics.median(eur_samples)) if eur_samples else 0.0

        cur_qty = float(line.quantity)
        cur_eur = float(line.line_total_eur) if line.line_total_eur is not None else 0.0
        qty_ratio = (cur_qty / median_qty) if median_qty > 0 else 0.0
        eur_ratio = (cur_eur / median_eur) if median_eur > 0 and cur_eur > 0 else 0.0
        max_ratio = max(qty_ratio, eur_ratio)

        if max_ratio > STANDING_DRIFT_RATIO_THRESHOLD:
            pct = round((max_ratio - 1.0) * 100)
            drift_notes.append(
                f"{line.product_id} ({line.product_name}): +{pct}% portion/cost drift vs "
                f"recurring baseline ({cur_qty:g} vs {median_qty:g} {line.sales_unit} median "
                f"across {len(samples)} prior orders)"
            )
            drifted_ids.append(line.product_id)

    if not drift_notes:
        return None, []
    return f"Standing-order drift detected: {'; '.join(drift_notes)}", drifted_ids


def list_agent_anomalies(db: Session, company_id: str) -> AgentAnomaliesResponse:
    """Return all quarantined agents in company_id with trigger telemetry for human review."""
    quarantined_agents = list(
        db.scalars(
            select(Agent)
            .where(Agent.company_id == company_id, Agent.status == "quarantined")
            .order_by(Agent.created_at.desc(), Agent.id.desc())
        ).all()
    )
    if not quarantined_agents:
        return AgentAnomaliesResponse(items=[])

    items: list[QuarantinedAgentAlertOut] = []
    for agent in quarantined_agents:
        q_event = db.scalars(
            select(AuditEventModel)
            .where(
                AuditEventModel.company_id == company_id,
                AuditEventModel.entity_type == "agent",
                AuditEventModel.entity_id == agent.id,
                AuditEventModel.action == "agent.quarantined",
            )
            .order_by(AuditEventModel.at.desc(), AuditEventModel.id.desc())
            .limit(1)
        ).first()

        details = dict(q_event.details or {}) if q_event is not None else {}
        quarantined_at = (
            q_event.at if q_event is not None else (agent.last_used_at or agent.created_at)
        )
        trigger_reason = str(
            details.get("trigger_reason")
            or "Agent automatically quarantined by purchasing anomaly guard."
        )
        rule_code = str(details.get("rule_code")) if details.get("rule_code") else None
        raw_chars = details.get("purchasing_characteristics")
        chars = dict(raw_chars) if isinstance(raw_chars, dict) else {}
        chars.setdefault("spent_today_eur", compute_agent_spent_today(db, agent.id, company_id))
        chars.setdefault("auto_approve_limit_eur", round(float(agent.auto_approve_limit_eur), 2))
        chars.setdefault("max_order_value_eur", round(float(agent.max_order_value_eur), 2))
        raw_reqs = details.get("recent_request_ids")
        req_ids = [str(x) for x in raw_reqs] if isinstance(raw_reqs, list) else []
        if q_event is not None and q_event.request_id and q_event.request_id not in req_ids:
            req_ids.insert(0, q_event.request_id)

        items.append(
            QuarantinedAgentAlertOut(
                agent_id=agent.id,
                agent_name=agent.name,
                status=agent.status,
                trigger_reason=trigger_reason,
                rule_code=rule_code,
                quarantined_at=quarantined_at,
                purchasing_characteristics=chars,
                recent_request_ids=req_ids,
            )
        )

    return AgentAnomaliesResponse(items=items)


def review_quarantined_agent(
    db: Session,
    principal: Principal,
    agent_id: str,
    req: AgentQuarantineReviewRequest,
) -> AgentQuarantineReviewResponse:
    """Human-only review of a quarantined agent: restore ('active') or revoke ('disabled')."""
    agent = db.scalar(
        select(Agent).where(Agent.id == agent_id, Agent.company_id == principal.company_id)
    )
    if agent is None:
        raise AppError("NOT_FOUND", 404, "Agent not found")

    now = clock.now()
    prev_status = agent.status
    if req.action == "restore":
        agent.status = "active"
    else:
        agent.status = "disabled"
        agent.can_place_orders = False
        agent.secret_hash = hash_secret(f"revoked_{secrets.token_urlsafe(32)}")
        release_holds_for_agent(db, agent.id, now=now)

    record_audit(
        db,
        company_id=principal.company_id,
        actor=principal,
        action="agent.quarantine_reviewed",
        entity_type="agent",
        entity_id=agent.id,
        outcome="ok",
        details={
            "request_id": request_id_var.get() or None,
            "decision": req.action,
            "previous_status": prev_status,
            "new_status": agent.status,
            "note": req.note,
        },
    )
    db.commit()
    db.refresh(agent)
    return AgentQuarantineReviewResponse(
        agent=agent_to_out(db, agent),
        action=req.action,
        reviewed_at=now,
    )
