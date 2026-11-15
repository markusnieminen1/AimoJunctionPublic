"""User dashboard KPI summary endpoint."""

from datetime import datetime, time, timedelta

from fastapi import APIRouter
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app import clock
from app.auth import CurrentUser, DbSession, is_agent_active
from app.models import Agent, Order
from app.schemas import DashboardResponse
from app.services.orders import expire_pending_orders, order_to_out

router = APIRouter(prefix="/dashboard", tags=["dashboard"])

_COMMITTED_ORDER_STATUSES = ("PLACED", "SUBMITTING", "SUBMISSION_UNKNOWN")


@router.get("", response_model=DashboardResponse)
def get_dashboard(
    principal: CurrentUser,
    db: DbSession,
) -> DashboardResponse:
    """Return company-wide KPI metrics and recent orders for human operators."""
    expire_pending_orders(db, company_id=principal.company_id)

    now = clock.now()
    today_start = datetime.combine(now.date(), time.min, tzinfo=clock.HELSINKI_TZ)
    today_end = today_start + timedelta(days=1)
    seven_days_ago = now - timedelta(days=7)

    pending_approvals = (
        db.scalar(
            select(func.count())
            .select_from(Order)
            .where(
                Order.company_id == principal.company_id,
                Order.status == "PENDING_APPROVAL",
            )
        )
        or 0
    )

    orders_today = (
        db.scalar(
            select(func.count())
            .select_from(Order)
            .where(
                Order.company_id == principal.company_id,
                Order.status.in_(_COMMITTED_ORDER_STATUSES),
                Order.created_at >= today_start,
                Order.created_at < today_end,
            )
        )
        or 0
    )

    spend_today_raw = db.scalar(
        select(func.coalesce(func.sum(Order.total_excl_vat_eur), 0.0)).where(
            Order.company_id == principal.company_id,
            Order.status.in_(_COMMITTED_ORDER_STATUSES),
            Order.created_at >= today_start,
            Order.created_at < today_end,
        )
    )

    spend_7d_raw = db.scalar(
        select(func.coalesce(func.sum(Order.total_excl_vat_eur), 0.0)).where(
            Order.company_id == principal.company_id,
            Order.status.in_(_COMMITTED_ORDER_STATUSES),
            Order.created_at >= seven_days_ago,
        )
    )

    co2_7d_raw = db.scalar(
        select(func.coalesce(func.sum(func.coalesce(Order.total_co2_kg, 0.0)), 0.0)).where(
            Order.company_id == principal.company_id,
            Order.status.in_(_COMMITTED_ORDER_STATUSES),
            Order.created_at >= seven_days_ago,
        )
    )

    company_agents = db.scalars(
        select(Agent).where(
            Agent.company_id == principal.company_id,
            Agent.status == "active",
        )
    ).all()
    active_agents = sum(1 for agent in company_agents if is_agent_active(agent, at=now))

    recent_rows = db.scalars(
        select(Order)
        .options(selectinload(Order.lines), selectinload(Order.events))
        .where(Order.company_id == principal.company_id)
        .order_by(Order.created_at.desc(), Order.id.desc())
        .limit(5)
    ).all()

    return DashboardResponse(
        pending_approvals=int(pending_approvals),
        orders_today=int(orders_today),
        spend_today_eur=round(float(spend_today_raw or 0.0), 2),
        spend_7d_eur=round(float(spend_7d_raw or 0.0), 2),
        co2_7d_kg=round(float(co2_7d_raw or 0.0), 4),
        active_agents=active_agents,
        recent_orders=[order_to_out(row) for row in recent_rows],
    )
