"""Aimo Platform 5-hour soft availability reservation & hanging-order abuse guard.

Layered deterministically over Valio Aimo live `/api/availability` (no physical upstream
ERP warehouse lock exists on the hackathon API; production hook integrates with OrderGateway).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.orm import Session, object_session

from app import clock
from app.audit import record_audit
from app.config import settings
from app.ids import new_id
from app.models import CompanyHoldWarning, InventoryHold, Order
from app.schemas import OrderStatus, QuoteLine, ReservationStatus

HoldTerminalState = Literal["COMMITTED", "RELEASED", "EXPIRED"]


def _parse_iso_helsinki(raw: Any) -> datetime | None:
    if isinstance(raw, datetime):
        return clock.to_helsinki(raw)
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        return clock.to_helsinki(datetime.fromisoformat(raw.strip()))
    except ValueError:
        return None


def count_company_warnings_7d(
    db: Session,
    company_id: str,
    *,
    now: datetime | None = None,
) -> int:
    """Count hanging-order warnings for a company within the rolling window."""
    resolved_now = now or clock.now()
    cutoff = resolved_now - timedelta(days=int(settings.hold_warning_window_days))
    rows = db.scalars(
        select(CompanyHoldWarning).where(CompanyHoldWarning.company_id == company_id)
    ).all()
    return sum(1 for row in rows if clock.to_helsinki(row.created_at) >= cutoff)


def record_hanging_order_warning(
    db: Session,
    *,
    company_id: str,
    order_id: str,
    reason: str,
    now: datetime | None = None,
) -> CompanyHoldWarning | None:
    """Idempotently record at most 1 hanging-order warning per abandoned order_id."""
    existing = db.scalar(select(CompanyHoldWarning).where(CompanyHoldWarning.order_id == order_id))
    if existing is not None:
        return None

    resolved_now = now or clock.now()
    warning = CompanyHoldWarning(
        id=new_id("hwn_"),
        company_id=company_id,
        order_id=order_id,
        reason=reason,
        created_at=resolved_now,
    )
    db.add(warning)
    db.flush()

    record_audit(
        db,
        company_id=company_id,
        actor=None,
        action="company.hold_warning",
        entity_type="company_hold_warning",
        entity_id=warning.id,
        outcome="ok",
        details={
            "order_id": order_id,
            "reason": reason,
        },
    )
    return warning


def sync_expired_holds(db: Session, *, now: datetime | None = None) -> int:
    """Lazily expire elapsed 5h holds and issue hanging-order warnings (deterministic via clock)."""
    resolved_now = now or clock.now()
    active_holds = db.scalars(select(InventoryHold).where(InventoryHold.state == "ACTIVE")).all()

    expired_order_ids: set[str] = set()
    mutated = 0

    for hold in active_holds:
        if clock.to_helsinki(hold.expires_at) <= resolved_now:
            hold.state = "EXPIRED"
            hold.released_at = resolved_now
            expired_order_ids.add(hold.order_id)
            mutated += 1

    for oid in sorted(expired_order_ids):
        order = db.get(Order, oid)
        if (
            order is not None
            and order.decided_at is None
            and order.status in ("PENDING_APPROVAL", "EXPIRED")
        ):
            src_ctx = (order.policy or {}).get("_source_context")
            if isinstance(src_ctx, dict) and src_ctx.get("simulator_scenario"):
                continue
            created_w = record_hanging_order_warning(
                db,
                company_id=order.company_id,
                order_id=order.id,
                reason="Aimo Platform 5h approval soft reservation expired without decision",
                now=resolved_now,
            )
            if created_w is not None:
                mutated += 1

    # Also check PENDING_APPROVAL orders whose _reservation window elapsed even if lines had
    # UNLIMITED stock (no FINITE InventoryHold rows), keeping strike counting consistent.
    pending_orders = db.scalars(
        select(Order).where(
            Order.status == "PENDING_APPROVAL",
            Order.decided_at.is_(None),
        )
    ).all()
    for order in pending_orders:
        raw_res = (order.policy or {}).get("_reservation")
        if not isinstance(raw_res, dict) or raw_res.get("status") != "HELD_5H":
            continue
        exp_dt = _parse_iso_helsinki(raw_res.get("expires_at"))
        if exp_dt is not None and exp_dt <= resolved_now:
            src_ctx = (order.policy or {}).get("_source_context")
            if isinstance(src_ctx, dict) and src_ctx.get("simulator_scenario"):
                continue
            created_w = record_hanging_order_warning(
                db,
                company_id=order.company_id,
                order_id=order.id,
                reason="Aimo Platform 5h approval soft reservation expired without decision",
                now=resolved_now,
            )
            if created_w is not None:
                mutated += 1

    if mutated > 0:
        db.flush()
    return mutated


def get_competing_held_quantities(
    db: Session,
    *,
    product_ids: Sequence[str],
    requested_delivery_date: str,
    now: datetime | None = None,
    exclude_order_id: str | None = None,
) -> dict[str, float]:
    """Sum active unexpired holds per product_id on requested_delivery_date."""
    if not product_ids:
        return {}
    resolved_now = now or clock.now()
    stmt = select(InventoryHold).where(
        InventoryHold.state == "ACTIVE",
        InventoryHold.requested_delivery_date == requested_delivery_date,
        InventoryHold.product_id.in_(list(dict.fromkeys(product_ids))),
    )
    if exclude_order_id is not None:
        stmt = stmt.where(InventoryHold.order_id != exclude_order_id)

    rows = db.scalars(stmt).all()
    totals: dict[str, float] = {}
    for row in rows:
        if clock.to_helsinki(row.expires_at) > resolved_now and float(row.held_quantity) > 0:
            totals[row.product_id] = round(
                totals.get(row.product_id, 0.0) + float(row.held_quantity), 4
            )
    return totals


def create_holds_for_pending_order(
    db: Session,
    *,
    order: Order,
    quoted_lines: Sequence[QuoteLine],
    approval_expires_at: datetime,
    now: datetime,
) -> dict[str, Any]:
    """Create 5h soft reservations for FINITE stock lines, or suspend if >= threshold warnings."""
    sync_expired_holds(db, now=now)
    warnings_7d = count_company_warnings_7d(db, order.company_id, now=now)
    threshold = int(settings.hold_warning_suspension_threshold)

    if warnings_7d >= threshold:
        return {
            "status": "UNRESERVED_DUE_TO_WARNINGS",
            "expires_at": None,
        }

    hold_delta = timedelta(hours=float(settings.approval_hold_hours))
    res_expires_at = min(now + hold_delta, clock.to_helsinki(approval_expires_at))
    agent_id = order.created_by_id if order.created_by_kind == "agent" else None

    for ln in quoted_lines:
        if (
            ln.available_qty_state == "FINITE"
            and ln.available_amount is not None
            and float(ln.available_amount) > 0
            and float(ln.quantity) > 0
        ):
            held_qty = round(min(float(ln.quantity), float(ln.available_amount)), 4)
            if held_qty > 0:
                db.add(
                    InventoryHold(
                        id=new_id("hld_"),
                        order_id=order.id,
                        company_id=order.company_id,
                        created_by_agent_id=agent_id,
                        product_id=ln.product_id,
                        requested_delivery_date=order.requested_delivery_date,
                        held_quantity=held_qty,
                        state="ACTIVE",
                        created_at=now,
                        expires_at=res_expires_at,
                        released_at=None,
                    )
                )

    db.flush()
    return {
        "status": "HELD_5H",
        "expires_at": clock.format_helsinki_iso(res_expires_at),
    }


def commit_or_release_holds_for_order(
    db: Session,
    order_id: str,
    *,
    target_state: HoldTerminalState,
    now: datetime | None = None,
) -> int:
    """Transition ACTIVE holds for order_id to COMMITTED or RELEASED with 0 warnings."""
    resolved_now = now or clock.now()
    rows = db.scalars(
        select(InventoryHold).where(
            InventoryHold.order_id == order_id,
            InventoryHold.state == "ACTIVE",
        )
    ).all()
    for row in rows:
        row.state = target_state
        row.released_at = resolved_now
    if rows:
        db.flush()
    return len(rows)


def expire_holds_and_warn_for_order(
    db: Session,
    order: Order,
    *,
    now: datetime | None = None,
) -> None:
    """Expire any remaining ACTIVE holds and record a hanging-order warning when order expires."""
    resolved_now = now or clock.now()
    commit_or_release_holds_for_order(
        db,
        order.id,
        target_state="EXPIRED",
        now=resolved_now,
    )
    if order.decided_at is not None:
        return
    src_ctx = (order.policy or {}).get("_source_context")
    if isinstance(src_ctx, dict) and src_ctx.get("simulator_scenario"):
        return
    raw_res = (order.policy or {}).get("_reservation")
    if isinstance(raw_res, dict) and raw_res.get("status") in (
        "HELD_5H",
        "UNRESERVED_DUE_TO_WARNINGS",
    ):
        record_hanging_order_warning(
            db,
            company_id=order.company_id,
            order_id=order.id,
            reason="Pending approval order expired without human decision",
            now=resolved_now,
        )


def release_holds_for_agent(
    db: Session,
    agent_id: str,
    *,
    now: datetime | None = None,
) -> int:
    """Immediately release all ACTIVE holds owned by a quarantined or disabled agent."""
    resolved_now = now or clock.now()
    rows = db.scalars(
        select(InventoryHold).where(
            InventoryHold.created_by_agent_id == agent_id,
            InventoryHold.state == "ACTIVE",
        )
    ).all()
    for row in rows:
        row.state = "RELEASED"
        row.released_at = resolved_now
    if rows:
        db.flush()
    return len(rows)


def compute_order_reservation_fields(
    order: Order,
    *,
    raw_reservation: Any,
    status: OrderStatus,
) -> tuple[ReservationStatus, datetime | None, int, str | None]:
    """Compute OrderOut reservation_status, expires_at, warning_count_7d, and warning_message."""
    now = clock.now()
    sess = object_session(order)
    warnings_7d = (
        count_company_warnings_7d(sess, order.company_id, now=now) if sess is not None else 0
    )
    threshold = int(settings.hold_warning_suspension_threshold)
    window_days = int(settings.hold_warning_window_days)
    hold_hours = float(settings.approval_hold_hours)

    meta = raw_reservation if isinstance(raw_reservation, dict) else {}
    meta_status = meta.get("status")
    exp_dt = _parse_iso_helsinki(meta.get("expires_at"))

    if meta_status == "UNRESERVED_DUE_TO_WARNINGS":
        res_status: ReservationStatus = "UNRESERVED_DUE_TO_WARNINGS"
        res_exp: datetime | None = None
    elif meta_status == "HELD_5H" and exp_dt is not None:
        res_exp = exp_dt
        if status == "PENDING_APPROVAL":
            if now < exp_dt:
                # Also verify holds weren't released early (e.g., agent quarantine)
                if sess is not None:
                    released_early = sess.scalar(
                        select(InventoryHold).where(
                            InventoryHold.order_id == order.id,
                            InventoryHold.state == "RELEASED",
                        )
                    )
                    res_status = "NONE" if released_early is not None else "HELD_5H"
                else:
                    res_status = "HELD_5H"
            else:
                res_status = "HOLD_EXPIRED"
        elif status == "EXPIRED" and order.decided_at is None:
            res_status = "HOLD_EXPIRED"
        else:
            res_status = "NONE"
    else:
        res_status = "NONE"
        res_exp = None

    msg: str | None = None
    if res_status == "UNRESERVED_DUE_TO_WARNINGS":
        msg = (
            f"Aimo Platform {hold_hours:g}h soft stock hold disabled "
            f"({warnings_7d}/{threshold} expired approvals in {window_days}d) — "
            "places on approve only if live stock remains available."
        )
    elif res_status == "HOLD_EXPIRED":
        msg = (
            f"Aimo Platform {hold_hours:g}h soft stock hold expired "
            f"(warnings: {warnings_7d}/{threshold}) — "
            "places on approve if live stock remains available."
        )
    elif warnings_7d >= 1:
        msg = (
            f"Hanging-order warnings ({window_days}d): {warnings_7d}/{threshold} — "
            f"approve or reject within {hold_hours:g}h to retain soft stock holds."
        )

    return res_status, res_exp, warnings_7d, msg
