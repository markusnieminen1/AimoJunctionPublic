"""Order lifecycle, state transitions, idempotency, approval, and expiry services."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from datetime import date, datetime, timedelta
from typing import Any, cast

from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app import clock
from app.aimo.client import AimoClient
from app.aimo.normalise import check_products_live
from app.audit import record_audit, verify_audit_chain
from app.auth import compute_agent_spent_today
from app.errors import AppError, request_id_var
from app.ids import new_id
from app.llm.decisions import is_prompt_injection
from app.models import (
    A2ATask,
    Agent,
    AssistantConversation,
    AuditEventModel,
    IdempotencyKey,
    Order,
    OrderEventModel,
    OrderLineModel,
    Product,
    Quote,
)
from app.schemas import (
    DeliveryStatus,
    LineFlag,
    OrderApproveRequest,
    OrderCreateRequest,
    OrderDecisionRequest,
    OrderEventOut,
    OrderListResponse,
    OrderOut,
    OrderStatus,
    OrderTraceResponse,
    OrderTraceStepOut,
    PolicyDecision,
    Principal,
    QuoteLine,
    RecipeSanityWarning,
)
from app.services import purchasing_guard
from app.services.catalog import resolve_diet_keys
from app.services.inventory_holds import (
    commit_or_release_holds_for_order,
    compute_order_reservation_fields,
    create_holds_for_pending_order,
    expire_holds_and_warn_for_order,
    sync_expired_holds,
)
from app.services.order_gateway import GatewayResult, OrderGateway, get_order_gateway
from app.services.policy import evaluate_policy
from app.services.quotes import (
    DELIVERY_SEVERITY,
    evaluate_diet_violations,
    line_model_to_schema,
    principal_from_company_columns,
)
from app.services.recipe_sanity import evaluate_recipe_sanity

MAX_RECONCILE_ATTEMPTS = 3

ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "PENDING_APPROVAL": {"SUBMITTING", "REJECTED", "EXPIRED", "CANCELLED"},
    "SUBMITTING": {"PLACED", "SUBMISSION_UNKNOWN", "FAILED"},
    "SUBMISSION_UNKNOWN": {"PLACED", "FAILED", "SUBMITTING"},
    "PLACED": {"CANCELLED"},
    "REJECTED": set(),
    "EXPIRED": set(),
    "CANCELLED": set(),
    "FAILED": set(),
}

_VALID_ORDER_STATUSES: set[str] = set(ALLOWED_TRANSITIONS.keys())


def _is_sqlite(db: Session) -> bool:
    bind = db.get_bind()
    return bind is not None and bind.dialect.name == "sqlite"


def validate_idempotency_key(raw_key: str | None) -> str:
    """Validate required Idempotency-Key header, raising 422 VALIDATION_ERROR if missing/blank."""
    if raw_key is None or not raw_key.strip():
        raise AppError("VALIDATION_ERROR", 422, "Missing Idempotency-Key header")
    cleaned = raw_key.strip()
    if len(cleaned) > 128:
        raise AppError(
            "VALIDATION_ERROR",
            422,
            "Idempotency-Key header exceeds maximum length of 128 characters",
        )
    return cleaned


def compute_request_hash(payload: BaseModel) -> str:
    """Compute a deterministic SHA-256 hash over a Pydantic request body."""
    canonical = json.dumps(
        payload.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _principal_key(principal: Principal) -> str:
    return f"{principal.kind}:{principal.id}"


def lookup_idempotency(
    db: Session,
    *,
    principal: Principal,
    endpoint: str,
    idem_key: str,
    request_hash: str,
) -> tuple[int, dict[str, Any]] | None:
    """Return stored (status_code, response_body) for an existing key, or raise 409 CONFLICT."""
    existing = db.scalar(
        select(IdempotencyKey).where(
            IdempotencyKey.principal_key == _principal_key(principal),
            IdempotencyKey.endpoint == endpoint,
            IdempotencyKey.idem_key == idem_key,
        )
    )
    if existing is None:
        return None
    if existing.request_hash != request_hash:
        raise AppError(
            "CONFLICT",
            409,
            "Idempotency-Key already used with a different request payload",
        )
    return existing.status_code, existing.response_body


def store_idempotency(
    db: Session,
    *,
    principal: Principal,
    endpoint: str,
    idem_key: str,
    request_hash: str,
    status_code: int,
    response_body: dict[str, Any],
) -> None:
    """Persist an idempotency record within the active transaction."""
    row = IdempotencyKey(
        principal_key=_principal_key(principal),
        endpoint=endpoint,
        idem_key=idem_key,
        request_hash=request_hash,
        status_code=status_code,
        response_body=response_body,
        created_at=clock.now(),
    )
    db.add(row)
    db.flush()


def compute_content_hash(
    lines: Sequence[QuoteLine],
    total_excl_vat_eur: float,
    requested_delivery_date: str,
) -> str:
    """SHA-256 over canonical JSON of lines, total_excl_vat_eur, and requested_delivery_date."""
    payload = {
        "lines": [
            {
                "product_id": str(line.product_id),
                "quantity": float(line.quantity),
                "sales_unit": str(line.sales_unit),
                "unit_price_eur": (
                    float(line.unit_price_eur) if line.unit_price_eur is not None else None
                ),
            }
            for line in lines
        ],
        "total_excl_vat_eur": round(float(total_excl_vat_eur), 2),
        "requested_delivery_date": str(requested_delivery_date),
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def order_to_out(order: Order) -> OrderOut:
    """Serialize an Order ORM row (with loaded lines and events) to OrderOut."""
    status = cast(
        OrderStatus,
        order.status if order.status in _VALID_ORDER_STATUSES else "FAILED",
    )
    created_by = principal_from_company_columns(
        company_id=order.company_id,
        kind=order.created_by_kind,
        principal_id=order.created_by_id,
        name=order.created_by_name,
        delegated_by_id=order.delegated_by_id,
        delegated_by_name=order.delegated_by_name,
    )
    decided_by = (
        principal_from_company_columns(
            company_id=order.company_id,
            kind=order.decided_by_kind or "user",
            principal_id=order.decided_by_id,
            name=order.decided_by_name or "Unknown user",
        )
        if order.decided_by_id is not None
        else None
    )
    events = [
        OrderEventOut(
            at=ev.at,
            type=ev.type,
            actor=Principal.model_validate(ev.actor) if ev.actor is not None else None,
            message=ev.message,
            request_id=ev.request_id,
        )
        for ev in order.events
    ]
    raw_policy = dict(order.policy or {})
    raw_diet = raw_policy.pop("_diet", None)
    raw_policy.pop("_source_context", None)
    raw_policy.pop("_recipe_context", None)
    raw_recipe_warning = raw_policy.pop("_recipe_sanity_warning", None)
    raw_reservation = raw_policy.pop("_reservation", None)
    raw_negotiation = raw_policy.pop("_negotiation", None)
    raw_policy.pop("_chat_trace", None)
    raw_assurance = raw_policy.pop("_assurance", None)
    raw_policy.pop("_delivery_schedule", None)
    diet = raw_diet.strip() if isinstance(raw_diet, str) and raw_diet.strip() else None
    recipe_warning = (
        RecipeSanityWarning.model_validate(raw_recipe_warning)
        if isinstance(raw_recipe_warning, dict)
        else None
    )
    negotiated_savings_eur = (
        round(float(raw_negotiation["negotiated_savings_eur"]), 2)
        if isinstance(raw_negotiation, dict)
        and raw_negotiation.get("negotiated_savings_eur") is not None
        else None
    )
    assurance_score = (
        int(raw_assurance["assurance_score"])
        if isinstance(raw_assurance, dict)
        and raw_assurance.get("assurance_score") is not None
        else None
    )
    assurance_threshold = (
        int(raw_assurance["assurance_threshold"])
        if isinstance(raw_assurance, dict)
        and raw_assurance.get("assurance_threshold") is not None
        else None
    )
    auto_executed = bool(
        isinstance(raw_assurance, dict) and raw_assurance.get("auto_executed")
    )
    res_status, res_exp, warn_count_7d, warn_msg = compute_order_reservation_fields(
        order,
        raw_reservation=raw_reservation,
        status=status,
    )

    return OrderOut(
        id=order.id,
        status=status,
        quote_id=order.quote_id,
        created_at=order.created_at,
        requested_delivery_date=order.requested_delivery_date,
        lines=[line_model_to_schema(ln) for ln in order.lines],
        total_excl_vat_eur=order.total_excl_vat_eur,
        total_incl_vat_eur=order.total_incl_vat_eur,
        total_co2_kg=order.total_co2_kg,
        policy=PolicyDecision.model_validate(raw_policy),
        created_by=created_by,
        decided_by=decided_by,
        decided_at=order.decided_at,
        decision_note=order.decision_note,
        approval_expires_at=order.approval_expires_at,
        content_hash=order.content_hash,
        gateway_reference=order.gateway_reference,
        events=events,
        diet=diet,
        recipe_sanity_warning=recipe_warning,
        reservation_status=res_status,
        reservation_expires_at=res_exp,
        company_warning_count_7d=warn_count_7d,
        reservation_warning_message=warn_msg,
        negotiated_savings_eur=negotiated_savings_eur,
        assurance_score=assurance_score,
        assurance_threshold=assurance_threshold,
        auto_executed=auto_executed,
    )


def transition_order(
    db: Session,
    order: Order,
    to_status: str,
    actor: Principal | None,
    message: str,
    event_type: str,
    *,
    audit_details: dict[str, Any] | None = None,
) -> OrderEventModel:
    """Enforce ALLOWED_TRANSITIONS, update order status, and emit OrderEvent + AuditEvent."""
    allowed = ALLOWED_TRANSITIONS.get(order.status, set())
    if to_status not in allowed:
        raise AppError(
            "INVALID_STATE",
            409,
            f"Cannot transition order from {order.status} to {to_status}",
            details={"current_status": order.status, "target_status": to_status},
        )

    now = clock.now()
    req_id = request_id_var.get() or new_id("req_")
    from_status = order.status
    order.status = to_status
    order.updated_at = now

    actor_dict = actor.model_dump(mode="json") if actor is not None else None
    event = OrderEventModel(
        id=new_id("evt_"),
        order_id=order.id,
        at=now,
        type=event_type,
        actor=actor_dict,
        message=message,
        request_id=req_id,
    )
    order.events.append(event)
    db.flush()

    details: dict[str, Any] = {
        "from_status": from_status,
        "to_status": to_status,
        "message": message,
    }
    if audit_details:
        details.update(audit_details)

    record_audit(
        db,
        company_id=order.company_id,
        actor=actor,
        action=event_type,
        entity_type="order",
        entity_id=order.id,
        outcome="ok",
        details=details,
    )
    return event


_CRITICAL_BLOCKER_FLAGS = {"CUTOFF_PASSED", "SALES_BLOCKED", "NOT_DELIVERABLE", "NO_PRICE"}


def _fillable_qty(ln: QuoteLine) -> float | None:
    if ln.available_qty_state == "ZERO":
        return 0.0
    if ln.available_qty_state == "FINITE" and ln.available_amount is not None:
        return min(float(ln.quantity), max(0.0, float(ln.available_amount)))
    return None


def _has_price_or_delivery_worsened(
    reference_lines: Sequence[QuoteLine],
    fresh_lines: Sequence[QuoteLine],
    *,
    base_unit_price_eur_by_sku: dict[str, Any] | None = None,
) -> bool:
    """Return True if price changed, delivery worsened, stock shrank, or blockers appeared."""
    for ref, fresh in zip(reference_lines, fresh_lines, strict=True):
        expected_price: float | None = ref.unit_price_eur
        if (
            isinstance(base_unit_price_eur_by_sku, dict)
            and ref.product_id in base_unit_price_eur_by_sku
            and base_unit_price_eur_by_sku[ref.product_id] is not None
        ):
            expected_price = float(base_unit_price_eur_by_sku[ref.product_id])
        if (expected_price is None) != (fresh.unit_price_eur is None):
            return True
        if (
            expected_price is not None
            and fresh.unit_price_eur is not None
            and abs(float(fresh.unit_price_eur) - float(expected_price)) > 1e-6
        ):
            return True
        if DELIVERY_SEVERITY[fresh.delivery_status] > DELIVERY_SEVERITY[ref.delivery_status]:
            return True
        if _CRITICAL_BLOCKER_FLAGS & (set(fresh.flags) - set(ref.flags)):
            return True
        ref_fill = _fillable_qty(ref)
        fresh_fill = _fillable_qty(fresh)
        if fresh_fill is not None:
            if ref_fill is not None and fresh_fill + 1e-6 < ref_fill:
                return True
            if ref_fill is None and fresh_fill + 1e-6 < float(ref.quantity):
                return True
    return False


def _compute_approval_expires_at(lines: Sequence[QuoteLine], now: datetime) -> datetime:
    """Earliest future closing_time among lines, or now + 24 hours if none."""
    future_closings: list[datetime] = []
    for line in lines:
        if not line.closing_time:
            continue
        try:
            dt = clock.to_helsinki(datetime.fromisoformat(line.closing_time))
        except ValueError:
            continue
        if dt > now:
            future_closings.append(dt)
    return min(future_closings) if future_closings else (now + timedelta(hours=24))


def next_weekday_after(date_iso: str) -> str:
    """Return the next Monday-Friday ISO date strictly after `date_iso`."""
    try:
        cur = date.fromisoformat(date_iso.strip()[:10])
    except ValueError:
        cur = clock.to_helsinki(clock.now()).date()
    cur = cur + timedelta(days=1)
    while cur.weekday() >= 5:
        cur = cur + timedelta(days=1)
    return cur.isoformat()


def compute_last_available_delivery_date(
    requested_delivery_date: str,
    lines: Sequence[QuoteLine],
    *,
    today_iso: str | None = None,
) -> str:
    """Compute effective order delivery date aligned to the last available deliverable item."""
    resolved_today = today_iso or clock.to_helsinki(clock.now()).date().isoformat()
    candidates = [requested_delivery_date] + [
        ln.available_date
        for ln in lines
        if ln.available_date and ln.delivery_status != "NOT_DELIVERABLE"
    ]
    last_available = max(candidates)
    needs_weekday_bump = any(
        ln.delivery_status != "NOT_DELIVERABLE"
        and ("LATE" in ln.flags or "CUTOFF_PASSED" in ln.flags)
        and (not ln.available_date or ln.available_date <= requested_delivery_date)
        for ln in lines
    )
    if needs_weekday_bump:
        bumped = next_weekday_after(max(requested_delivery_date, resolved_today))
        if bumped > last_available:
            last_available = bumped
    return last_available


def align_lines_for_order_placement(
    lines: Sequence[QuoteLine],
    *,
    last_available_date: str,
) -> list[QuoteLine]:
    """Align deliverable order lines to `last_available_date` so non-partial items place ON_TIME."""
    aligned: list[QuoteLine] = []
    for ln in lines:
        if (
            ln.delivery_status == "NOT_DELIVERABLE"
            or "NOT_DELIVERABLE" in ln.flags
            or "SALES_BLOCKED" in ln.flags
            or "NO_PRICE" in ln.flags
        ):
            aligned.append(ln)
            continue
        stripped_flags: list[LineFlag] = [
            f for f in ln.flags if f not in ("LATE", "CUTOFF_PASSED")
        ]
        eff_avail = ln.available_date or last_available_date
        if "PARTIAL" in stripped_flags or "UNIT_MISMATCH" in stripped_flags:
            eff_status: DeliveryStatus = (
                "AT_RISK" if ln.delivery_status == "LATE" else ln.delivery_status
            )
            eff_prob = ln.on_time_probability
        else:
            eff_status = "ON_TIME"
            eff_prob = round(max(float(ln.on_time_probability or 0.0), 0.95), 4)
        aligned.append(
            ln.model_copy(
                update={
                    "flags": stripped_flags,
                    "available_date": eff_avail,
                    "delivery_status": eff_status,
                    "on_time_probability": eff_prob,
                }
            )
        )
    return aligned


def _align_lines_to_last_available(
    lines: Sequence[QuoteLine],
    requested_delivery_date: str,
    today_iso: str | None = None,
) -> tuple[str, list[QuoteLine]]:
    """Compute last_available_date and align deliverable lines to ON_TIME for order placement."""
    last_available_date = compute_last_available_delivery_date(
        requested_delivery_date,
        lines,
        today_iso=today_iso,
    )
    aligned_lines = align_lines_for_order_placement(
        lines,
        last_available_date=last_available_date,
    )
    return last_available_date, aligned_lines


def _combined_diet_param(quote_diet: str | None, req_diet: str | None) -> str | None:
    seen: set[str] = set()
    ordered: list[str] = []
    for raw in (quote_diet, req_diet):
        if not raw or not raw.strip():
            continue
        for token in raw.split(","):
            cleaned = token.strip()
            if not cleaned:
                continue
            key = cleaned.upper()
            if key not in seen:
                seen.add(key)
                ordered.append(cleaned)
    return ",".join(ordered) if ordered else None


def expire_pending_orders(db: Session, *, company_id: str | None = None) -> int:
    """Mark any PENDING_APPROVAL orders past approval_expires_at as EXPIRED."""
    now = clock.now()
    holds_mutated = sync_expired_holds(db, now=now)
    stmt = (
        select(Order)
        .options(selectinload(Order.events))
        .where(
            Order.status == "PENDING_APPROVAL",
            Order.approval_expires_at.is_not(None),
        )
    )
    if company_id is not None:
        stmt = stmt.where(Order.company_id == company_id)

    candidates = db.scalars(stmt).all()
    expired_count = 0
    for order in candidates:
        if order.approval_expires_at is None:
            continue
        if now >= clock.to_helsinki(order.approval_expires_at):
            transition_order(
                db,
                order,
                "EXPIRED",
                None,
                "Pending approval expired before user decision",
                "order.expired",
            )
            expire_holds_and_warn_for_order(db, order, now=now)
            expired_count += 1

    if expired_count > 0 or holds_mutated > 0:
        db.commit()
    return expired_count


def _load_company_order(
    db: Session,
    order_id: str,
    company_id: str,
    *,
    for_update: bool = False,
) -> Order:
    stmt = (
        select(Order)
        .options(selectinload(Order.lines), selectinload(Order.events))
        .where(Order.id == order_id, Order.company_id == company_id)
    )
    if for_update and not _is_sqlite(db):
        stmt = stmt.with_for_update()
    order = db.scalar(stmt)
    if order is None:
        raise AppError("NOT_FOUND", 404, "Order not found")
    return order


def _expire_single_order_if_needed(db: Session, order: Order) -> bool:
    now = clock.now()
    if (
        order.status == "PENDING_APPROVAL"
        and order.approval_expires_at is not None
        and now >= clock.to_helsinki(order.approval_expires_at)
    ):
        transition_order(
            db,
            order,
            "EXPIRED",
            None,
            "Pending approval expired before user decision",
            "order.expired",
        )
        expire_holds_and_warn_for_order(db, order, now=now)
        db.commit()
        return True
    return False


def _build_gateway_payload(order: Order) -> dict[str, Any]:
    """Build canonical payload sent to the order gateway for an order."""
    return {
        "order_id": order.id,
        "company_id": order.company_id,
        "quote_id": order.quote_id,
        "requested_delivery_date": order.requested_delivery_date,
        "total_excl_vat_eur": order.total_excl_vat_eur,
        "total_incl_vat_eur": order.total_incl_vat_eur,
        "content_hash": order.content_hash,
        "lines": [
            {
                "product_id": ln.product_id,
                "quantity": ln.quantity,
                "sales_unit": ln.sales_unit,
                "unit_price_eur": ln.unit_price_eur,
            }
            for ln in order.lines
        ],
    }


def _order_source_context(order: Order) -> dict[str, Any]:
    raw = (order.policy or {}).get("_source_context")
    return dict(raw) if isinstance(raw, dict) else {}


def _build_gateway_step(
    *,
    stage: str,
    summary: str,
    actor: Principal | None,
    details: dict[str, Any],
) -> list[dict[str, Any]]:
    return [
        {
            "step": 1,
            "stage": stage,
            "summary": summary,
            "actor_kind": actor.kind if actor is not None else "system",
            "at": clock.format_helsinki_iso(clock.now()),
            "details": details,
        }
    ]


def _submit_via_gateway(
    db: Session,
    order: Order,
    actor: Principal | None,
    gateway: OrderGateway,
) -> GatewayResult:
    """Submit a SUBMITTING order to the gateway and transition based on outcome."""
    src_ctx = _order_source_context(order)
    result = gateway.submit(db, order.id, _build_gateway_payload(order))
    if result.status == "ok":
        order.gateway_reference = result.reference
        details: dict[str, Any] = {
            "gateway_reference": result.reference,
            "quote_id": order.quote_id,
            "reasoning_steps": _build_gateway_step(
                stage="gateway_submission",
                summary=f"Submitted order to Aimo gateway -> PLACED ({result.reference})",
                actor=actor,
                details={"gateway_reference": result.reference},
            ),
        }
        if src_ctx:
            details["source_context"] = src_ctx
        transition_order(
            db,
            order,
            "PLACED",
            actor,
            f"Order placed via simulated Valio Aimo order gateway ({result.reference})",
            "order.placed",
            audit_details=details,
        )
    elif result.status == "error":
        details = {
            "gateway_error": result.message,
            "quote_id": order.quote_id,
            "reasoning_steps": _build_gateway_step(
                stage="gateway_submission",
                summary=f"Aimo gateway rejected order: {result.message}",
                actor=actor,
                details={"gateway_error": result.message},
            ),
        }
        if src_ctx:
            details["source_context"] = src_ctx
        transition_order(
            db,
            order,
            "FAILED",
            actor,
            f"Simulated Valio Aimo order gateway rejected order: {result.message}",
            "order.failed",
            audit_details=details,
        )
    else:
        details = {
            "gateway_message": result.message,
            "quote_id": order.quote_id,
            "reasoning_steps": _build_gateway_step(
                stage="gateway_submission",
                summary=f"Aimo gateway outcome unknown ({result.message}); pending reconciliation",
                actor=actor,
                details={"gateway_message": result.message},
            ),
        }
        if src_ctx:
            details["source_context"] = src_ctx
        transition_order(
            db,
            order,
            "SUBMISSION_UNKNOWN",
            actor,
            (
                "Simulated Valio Aimo order gateway outcome unknown; "
                f"awaiting reconciliation ({result.message})"
            ),
            "order.submission_unknown",
            audit_details=details,
        )
    return result


def place_order(
    db: Session,
    aimo_client: AimoClient,
    principal: Principal,
    req: OrderCreateRequest,
    raw_idempotency_key: str | None,
    gateway: OrderGateway | None = None,
    *,
    source_context: dict[str, Any] | None = None,
) -> tuple[int, dict[str, Any]]:
    """Place an order from an OPEN quote with idempotency, locking, live check, and policy."""
    idem_key = validate_idempotency_key(raw_idempotency_key)
    endpoint = "POST:/api/orders"
    request_hash = compute_request_hash(req)

    cached = lookup_idempotency(
        db,
        principal=principal,
        endpoint=endpoint,
        idem_key=idem_key,
        request_hash=request_hash,
    )
    if cached is not None:
        return cached

    if req.diet and req.diet.strip():
        resolve_diet_keys(req.diet, None)

    quote_stmt = (
        select(Quote)
        .options(selectinload(Quote.lines))
        .where(Quote.id == req.quote_id, Quote.company_id == principal.company_id)
    )
    if not _is_sqlite(db):
        quote_stmt = quote_stmt.with_for_update()
    quote = db.scalar(quote_stmt)
    if quote is None:
        raise AppError("NOT_FOUND", 404, "Quote not found")

    if quote.status == "USED":
        raise AppError("QUOTE_ALREADY_USED", 409, "Quote has already been used")
    if quote.status == "EXPIRED":
        raise AppError("QUOTE_EXPIRED", 409, "Quote has expired")

    now = clock.now()
    if now >= clock.to_helsinki(quote.expires_at):
        quote.status = "EXPIRED"
        record_audit(
            db,
            company_id=quote.company_id,
            actor=principal,
            action="quote.expired",
            entity_type="quote",
            entity_id=quote.id,
            outcome="ok",
            details={"expires_at": clock.format_helsinki_iso(quote.expires_at)},
        )
        db.commit()
        raise AppError("QUOTE_EXPIRED", 409, "Quote has expired")

    quoted_lines = [line_model_to_schema(ln) for ln in quote.lines]
    product_ids = [ln.product_id for ln in quoted_lines]
    quantities = {ln.product_id: float(ln.quantity) for ln in quoted_lines}

    fresh_lines = check_products_live(
        db=db,
        aimo_client=aimo_client,
        product_ids=product_ids,
        quantities=quantities,
        requested_delivery_date=quote.requested_delivery_date,
        customer_id=principal.company_id,
    )
    raw_quote_neg = (quote.policy_preview or {}).get("_negotiation")
    quote_base_prices = (
        raw_quote_neg.get("base_unit_price_eur_by_sku")
        if isinstance(raw_quote_neg, dict)
        else None
    )
    if _has_price_or_delivery_worsened(
        quoted_lines,
        fresh_lines,
        base_unit_price_eur_by_sku=quote_base_prices,
    ):
        raise AppError(
            "QUOTE_STALE",
            409,
            "Prices or delivery availability changed since quote was created",
            details={"lines": [ln.model_dump(mode="json") for ln in fresh_lines]},
        )

    today_iso = clock.to_helsinki(now).date().isoformat()
    rescheduled_cutoff_pids = [
        ln.product_id
        for ln in quoted_lines
        if ln.delivery_status != "NOT_DELIVERABLE"
        and ("CUTOFF_PASSED" in ln.flags or "LATE" in ln.flags)
    ]
    last_available_date, quoted_lines = _align_lines_to_last_available(
        quoted_lines,
        quote.requested_delivery_date,
        today_iso,
    )
    aligned_flag = last_available_date != quote.requested_delivery_date
    delivery_schedule_meta: dict[str, Any] = {
        "quote_requested_delivery_date": quote.requested_delivery_date,
        "effective_delivery_date": last_available_date,
        "aligned_to_last_available": aligned_flag,
        "advanced": aligned_flag,
        "rescheduled_cutoff_product_ids": rescheduled_cutoff_pids,
    }

    products = db.scalars(select(Product).where(Product.id.in_(product_ids))).all()
    product_map = {p.id: p for p in products}
    product_categories = {p.id: p.category for p in products}

    quote_diet = (quote.policy_preview or {}).get("_diet")
    combined_diet = _combined_diet_param(
        quote_diet if isinstance(quote_diet, str) else None,
        req.diet,
    )
    diet_violations = evaluate_diet_violations(product_map, quoted_lines, combined_diet)

    quote_recipe_ctx = (quote.policy_preview or {}).get("_recipe_context")
    recipe_ctx_dict = dict(quote_recipe_ctx) if isinstance(quote_recipe_ctx, dict) else {}
    eff_recipe_id = req.recipe_id or (
        str(recipe_ctx_dict["recipe_id"]) if recipe_ctx_dict.get("recipe_id") is not None else None
    )
    eff_target_pax = req.target_pax or (
        int(recipe_ctx_dict["target_pax"])
        if recipe_ctx_dict.get("target_pax") is not None
        else None
    )
    recipe_warning = evaluate_recipe_sanity(
        product_map=product_map,
        lines=quoted_lines,
        recipe_id=eff_recipe_id,
        target_pax=eff_target_pax,
    )

    agent: Agent | None = None
    spent_today_eur = 0.0
    if principal.kind == "agent":
        agent = db.get(Agent, principal.id)
        spent_today_eur = compute_agent_spent_today(db, principal.id, principal.company_id)

    content_hash = compute_content_hash(
        quoted_lines,
        quote.total_excl_vat_eur,
        last_available_date,
    )
    quote_source_ctx = (quote.policy_preview or {}).get("_source_context")
    merged_source_ctx: dict[str, Any] = (
        dict(quote_source_ctx) if isinstance(quote_source_ctx, dict) else {}
    )
    if source_context:
        merged_source_ctx.update(source_context)
    merged_source_ctx.pop("upstream_injection_screened", None)
    incoming_chat_trace = merged_source_ctx.pop("_chat_trace", None)
    incoming_assurance = merged_source_ctx.pop("_assurance", None)
    quote_chat_trace = (quote.policy_preview or {}).get("_chat_trace")
    quote_assurance = (quote.policy_preview or {}).get("_assurance")
    resolved_chat_trace = (
        incoming_chat_trace
        if isinstance(incoming_chat_trace, dict)
        else (quote_chat_trace if isinstance(quote_chat_trace, dict) else None)
    )
    resolved_assurance = (
        incoming_assurance
        if isinstance(incoming_assurance, dict)
        else (quote_assurance if isinstance(quote_assurance, dict) else None)
    )

    if not merged_source_ctx.get("simulator_scenario"):
        purchasing_guard.check_duplicate_order_spam_and_quarantine(
            db,
            principal=principal,
            agent=agent,
            candidate_content_hash=content_hash,
            total_excl_vat_eur=quote.total_excl_vat_eur,
        )

    neg_meta = (quote.policy_preview or {}).get("_negotiation")
    semantic_guard_reason: str | None = (
        str(neg_meta["semantic_guard_reason"])
        if isinstance(neg_meta, dict) and neg_meta.get("semantic_guard_reason")
        else None
    )
    if req.note and req.note.strip():
        note_inj = is_prompt_injection(req.note)
        if note_inj.answer:
            semantic_guard_reason = note_inj.reason
            already_screened_upstream = bool(
                source_context and source_context.get("upstream_injection_screened")
            )
            if not already_screened_upstream:
                record_audit(
                    db,
                    company_id=principal.company_id,
                    actor=principal,
                    action="order.injection_screen",
                    entity_type="quote",
                    entity_id=quote.id,
                    outcome="ok",
                    details={
                        "request_id": request_id_var.get() or None,
                        "quote_id": quote.id,
                        "flagged": True,
                        "reason": note_inj.reason,
                        "verifier_model": note_inj.verifier_model,
                        "note_preview": req.note[:200],
                    },
                )
                db.flush()
                purchasing_guard.check_repeated_injection_and_quarantine(
                    db,
                    principal=principal,
                    reason=note_inj.reason,
                )

    standing_drift_reason: str | None = None
    standing_drift_product_ids: list[str] = []
    if principal.kind == "agent":
        standing_drift_reason, standing_drift_product_ids = (
            purchasing_guard.evaluate_standing_order_drift(
                db,
                company_id=principal.company_id,
                lines=quoted_lines,
            )
        )

    decision = evaluate_policy(
        principal=principal,
        agent=agent,
        lines=quoted_lines,
        product_categories=product_categories,
        total_excl_vat_eur=quote.total_excl_vat_eur,
        spent_today_eur=spent_today_eur,
        diet_violations=diet_violations or None,
        recipe_sanity_warning=recipe_warning,
        semantic_guard_reason=semantic_guard_reason,
        standing_drift_reason=standing_drift_reason,
        standing_drift_product_ids=standing_drift_product_ids,
    )

    fired_rule_ids = [r.rule_id for r in decision.rules]
    now_iso = clock.format_helsinki_iso(now)
    policy_reasoning_steps: list[dict[str, Any]] = [
        {
            "step": 1,
            "stage": "quote_revalidation",
            "summary": (
                f"Re-validated quote {quote.id} ({len(quoted_lines)} lines, "
                f"{quote.total_excl_vat_eur:.2f} EUR excl. VAT) against live prices & diet rules"
            ),
            "actor_kind": principal.kind,
            "at": now_iso,
            "details": {
                "quote_id": quote.id,
                "diet": combined_diet,
                "diet_violations": list(diet_violations),
                "total_excl_vat_eur": quote.total_excl_vat_eur,
            },
        },
        {
            "step": 2,
            "stage": "layer1_policy_evaluation",
            "summary": (
                f"Layer 1 policy decision: {decision.outcome} "
                f"(fired rules: {', '.join(fired_rule_ids) or 'none'})"
            ),
            "actor_kind": "system",
            "at": now_iso,
            "details": {
                "outcome": decision.outcome,
                "fired_rules": fired_rule_ids,
                "spent_today_eur": spent_today_eur,
            },
        },
    ]

    if decision.outcome == "BLOCKED":
        blocked_details = decision.model_dump(mode="json")
        blocked_details["request_id"] = request_id_var.get() or None
        blocked_details["fired_rules"] = fired_rule_ids
        blocked_details["reasoning_steps"] = policy_reasoning_steps
        if merged_source_ctx:
            blocked_details["source_context"] = merged_source_ctx
            if merged_source_ctx.get("routing_path"):
                blocked_details["routing_path"] = merged_source_ctx["routing_path"]
        record_audit(
            db,
            company_id=principal.company_id,
            actor=principal,
            action="order.blocked",
            entity_type="quote",
            entity_id=quote.id,
            outcome="error",
            details=blocked_details,
        )
        db.flush()
        purchasing_guard.check_consecutive_policy_blocks_and_quarantine(
            db,
            principal=principal,
            agent=agent,
            latest_fired_rules=fired_rule_ids,
        )
        db.commit()
        raise AppError(
            "POLICY_BLOCKED",
            403,
            "Order blocked by purchasing policy",
            details=decision.model_dump(mode="json"),
        )

    order_id = new_id("ord_")
    initial_status = "SUBMITTING" if decision.outcome == "AUTO" else "PENDING_APPROVAL"
    approval_expires_at = (
        _compute_approval_expires_at(quoted_lines, now)
        if initial_status == "PENDING_APPROVAL"
        else None
    )

    policy_json = decision.model_dump(mode="json")
    if combined_diet:
        policy_json["_diet"] = combined_diet
    if eff_recipe_id or eff_target_pax is not None:
        policy_json["_recipe_context"] = {
            "recipe_id": eff_recipe_id,
            "target_pax": eff_target_pax,
        }
    if recipe_warning is not None and recipe_warning.triggered:
        policy_json["_recipe_sanity_warning"] = recipe_warning.model_dump(mode="json")
    if isinstance(raw_quote_neg, dict):
        policy_json["_negotiation"] = raw_quote_neg
    if isinstance(resolved_chat_trace, dict):
        policy_json["_chat_trace"] = resolved_chat_trace
    if isinstance(resolved_assurance, dict):
        policy_json["_assurance"] = resolved_assurance
    policy_json["_delivery_schedule"] = delivery_schedule_meta
    if merged_source_ctx:
        policy_json["_source_context"] = merged_source_ctx

    order = Order(
        id=order_id,
        company_id=principal.company_id,
        quote_id=quote.id,
        status=initial_status,
        created_at=now,
        requested_delivery_date=last_available_date,
        total_excl_vat_eur=quote.total_excl_vat_eur,
        total_incl_vat_eur=quote.total_incl_vat_eur,
        total_co2_kg=quote.total_co2_kg,
        policy=policy_json,
        created_by_kind=principal.kind,
        created_by_id=principal.id,
        created_by_name=principal.name,
        delegated_by_id=principal.delegated_by.id if principal.delegated_by else None,
        delegated_by_name=principal.delegated_by.name if principal.delegated_by else None,
        decided_by_kind=None,
        decided_by_id=None,
        decided_by_name=None,
        decided_at=None,
        decision_note=None,
        approval_expires_at=approval_expires_at,
        content_hash=content_hash,
        gateway_reference=None,
        note=req.note,
        reconcile_attempts=0,
        updated_at=now,
    )
    db.add(order)
    db.flush()

    reservation_meta: dict[str, Any] | None = None
    if initial_status == "PENDING_APPROVAL" and approval_expires_at is not None:
        reservation_meta = create_holds_for_pending_order(
            db,
            order=order,
            quoted_lines=quoted_lines,
            approval_expires_at=approval_expires_at,
            now=now,
        )
        order.policy = {**policy_json, "_reservation": reservation_meta}
        db.flush()

    for idx, ln in enumerate(quoted_lines):
        order.lines.append(
            OrderLineModel(
                order_id=order.id,
                line_index=idx,
                product_id=ln.product_id,
                product_name=ln.product_name,
                quantity=ln.quantity,
                sales_unit=ln.sales_unit,
                unit_price_eur=ln.unit_price_eur,
                line_total_eur=ln.line_total_eur,
                tax_rate=ln.tax_rate,
                available_date=ln.available_date,
                closing_time=ln.closing_time,
                logistic_model=ln.logistic_model,
                available_qty_state=ln.available_qty_state,
                available_amount=ln.available_amount,
                delivery_status=ln.delivery_status,
                on_time_probability=ln.on_time_probability,
                co2_kg=ln.co2_kg,
                co2_basis=ln.co2_basis,
                flags=list(ln.flags),
            )
        )

    req_id = request_id_var.get() or new_id("req_")
    created_msg = (
        "Order created and queued for submission"
        if initial_status == "SUBMITTING"
        else "Order created and awaiting human approval"
    )
    order.events.append(
        OrderEventModel(
            id=new_id("evt_"),
            order_id=order.id,
            at=now,
            type="order.created",
            actor=principal.model_dump(mode="json"),
            message=created_msg,
            request_id=req_id,
        )
    )
    quote.status = "USED"
    db.flush()

    created_audit_details: dict[str, Any] = {
        "request_id": req_id,
        "quote_id": quote.id,
        "status": initial_status,
        "policy_outcome": decision.outcome,
        "fired_rules": fired_rule_ids,
        "total_excl_vat_eur": quote.total_excl_vat_eur,
        "reasoning_steps": policy_reasoning_steps,
    }
    if recipe_warning is not None and recipe_warning.triggered:
        created_audit_details["recipe_sanity_warning"] = recipe_warning.model_dump(mode="json")
    if reservation_meta is not None:
        created_audit_details["reservation"] = reservation_meta
    if merged_source_ctx:
        created_audit_details["source_context"] = merged_source_ctx
        if merged_source_ctx.get("conversation_id"):
            created_audit_details["conversation_id"] = merged_source_ctx["conversation_id"]
        if merged_source_ctx.get("a2a_task_id"):
            created_audit_details["a2a_task_id"] = merged_source_ctx["a2a_task_id"]
            src_task = db.get(A2ATask, str(merged_source_ctx["a2a_task_id"]))
            if (
                src_task is not None
                and src_task.company_id == principal.company_id
                and src_task.linked_order_id is None
            ):
                src_task.linked_order_id = order.id
                db.flush()
        if merged_source_ctx.get("routing_path"):
            created_audit_details["routing_path"] = merged_source_ctx["routing_path"]

    record_audit(
        db,
        company_id=principal.company_id,
        actor=principal,
        action="order.created",
        entity_type="order",
        entity_id=order.id,
        outcome="ok",
        details=created_audit_details,
    )

    if initial_status == "SUBMITTING":
        order.gateway_attempts += 1
        db.flush()
        _submit_via_gateway(db, order, principal, gateway or get_order_gateway())

    order_out = order_to_out(order)
    response_body = order_out.model_dump(mode="json")

    try:
        store_idempotency(
            db,
            principal=principal,
            endpoint=endpoint,
            idem_key=idem_key,
            request_hash=request_hash,
            status_code=201,
            response_body=response_body,
        )
        db.commit()
    except IntegrityError:
        db.rollback()
        replay = lookup_idempotency(
            db,
            principal=principal,
            endpoint=endpoint,
            idem_key=idem_key,
            request_hash=request_hash,
        )
        if replay is not None:
            return replay
        raise

    return 201, response_body


def approve_order(
    db: Session,
    aimo_client: AimoClient,
    principal: Principal,
    order_id: str,
    req: OrderApproveRequest,
    raw_idempotency_key: str | None,
    gateway: OrderGateway | None = None,
) -> tuple[int, dict[str, Any]]:
    """Human-approve a PENDING_APPROVAL order after content_hash and live re-validation."""
    idem_key = validate_idempotency_key(raw_idempotency_key)
    endpoint = f"POST:/api/orders/{order_id}/approve"
    request_hash = compute_request_hash(req)

    cached = lookup_idempotency(
        db,
        principal=principal,
        endpoint=endpoint,
        idem_key=idem_key,
        request_hash=request_hash,
    )
    if cached is not None:
        return cached

    if sync_expired_holds(db) > 0:
        db.commit()

    order = _load_company_order(db, order_id, principal.company_id, for_update=True)

    if _expire_single_order_if_needed(db, order):
        raise AppError(
            "INVALID_STATE",
            409,
            "Order approval has expired",
            details={"status": order.status},
        )

    if order.status != "PENDING_APPROVAL":
        raise AppError(
            "INVALID_STATE",
            409,
            f"Order cannot be approved in status {order.status}",
            details={"status": order.status},
        )

    if req.content_hash != order.content_hash:
        raise AppError(
            "APPROVAL_CONTENT_CHANGED",
            409,
            "Order content hash does not match current order state",
            details={"expected_content_hash": order.content_hash},
        )

    order_lines = [line_model_to_schema(ln) for ln in order.lines]
    product_ids = [ln.product_id for ln in order_lines]
    quantities = {ln.product_id: float(ln.quantity) for ln in order_lines}

    fresh_lines = check_products_live(
        db=db,
        aimo_client=aimo_client,
        product_ids=product_ids,
        quantities=quantities,
        requested_delivery_date=order.requested_delivery_date,
        customer_id=principal.company_id,
        exclude_order_id=order.id,
    )
    now_check = clock.now()
    if order.requested_delivery_date < now_check.date().isoformat():
        raise AppError(
            "QUOTE_STALE",
            409,
            "Requested delivery date is now in the past",
            details={
                "requested_delivery_date": order.requested_delivery_date,
                "lines": [ln.model_dump(mode="json") for ln in fresh_lines],
            },
        )
    raw_order_sched = (order.policy or {}).get("_delivery_schedule")
    rescheduled_cutoff_set = (
        set(raw_order_sched.get("rescheduled_cutoff_product_ids") or [])
        if isinstance(raw_order_sched, dict)
        else set()
    )
    for fresh_ln in fresh_lines:
        if fresh_ln.product_id in rescheduled_cutoff_set:
            continue
        if fresh_ln.closing_time:
            try:
                closing_dt = clock.to_helsinki(datetime.fromisoformat(fresh_ln.closing_time))
            except ValueError:
                closing_dt = None
            if closing_dt is not None and closing_dt <= now_check:
                raise AppError(
                    "QUOTE_STALE",
                    409,
                    "Order delivery cut-off has passed before approval",
                    details={"lines": [ln.model_dump(mode="json") for ln in fresh_lines]},
                )

    fresh_compare_lines = (
        align_lines_for_order_placement(
            fresh_lines,
            last_available_date=order.requested_delivery_date,
        )
        if isinstance(raw_order_sched, dict)
        else fresh_lines
    )

    raw_order_neg = (order.policy or {}).get("_negotiation")
    order_base_prices = (
        raw_order_neg.get("base_unit_price_eur_by_sku")
        if isinstance(raw_order_neg, dict)
        else None
    )
    if _has_price_or_delivery_worsened(
        order_lines,
        fresh_compare_lines,
        base_unit_price_eur_by_sku=order_base_prices,
    ):
        raise AppError(
            "QUOTE_STALE",
            409,
            "Prices or delivery availability changed since order was created",
            details={"lines": [ln.model_dump(mode="json") for ln in fresh_lines]},
        )

    products = db.scalars(select(Product).where(Product.id.in_(product_ids))).all()
    product_map = {p.id: p for p in products}
    product_categories = {p.id: p.category for p in products}

    order_diet = (order.policy or {}).get("_diet")
    quote = db.get(Quote, order.quote_id)
    quote_diet = (quote.policy_preview or {}).get("_diet") if quote is not None else None
    effective_diet = _combined_diet_param(
        order_diet if isinstance(order_diet, str) else None,
        quote_diet if isinstance(quote_diet, str) else None,
    )
    diet_violations = evaluate_diet_violations(
        product_map,
        fresh_compare_lines,
        effective_diet,
    )

    decision = evaluate_policy(
        principal=principal,
        agent=None,
        lines=list(fresh_compare_lines),
        product_categories=product_categories,
        total_excl_vat_eur=order.total_excl_vat_eur,
        spent_today_eur=0.0,
        diet_violations=diet_violations or None,
        approval_override=True,
    )
    if decision.outcome == "BLOCKED":
        record_audit(
            db,
            company_id=principal.company_id,
            actor=principal,
            action="order.approval_blocked",
            entity_type="order",
            entity_id=order.id,
            outcome="error",
            details=decision.model_dump(mode="json"),
        )
        db.commit()
        raise AppError(
            "POLICY_BLOCKED",
            403,
            "Order blocked by hard safety policy during approval re-validation",
            details=decision.model_dump(mode="json"),
        )

    now = clock.now()
    order.decided_by_kind = principal.kind
    order.decided_by_id = principal.id
    order.decided_by_name = principal.name
    order.decided_at = now
    order.decision_note = req.note

    approve_details: dict[str, Any] = {
        "content_hash": order.content_hash,
        "quote_id": order.quote_id,
        "reasoning_steps": _build_gateway_step(
            stage="human_approval",
            summary=f"Human user {principal.name} approved order ({req.note or 'no note'})",
            actor=principal,
            details={"content_hash": order.content_hash, "note": req.note},
        ),
    }
    src_ctx = _order_source_context(order)
    if src_ctx:
        approve_details["source_context"] = src_ctx
    transition_order(
        db,
        order,
        "SUBMITTING",
        principal,
        req.note or "Order approved by user",
        "order.approved",
        audit_details=approve_details,
    )
    order.gateway_attempts += 1
    db.flush()
    gw_res = _submit_via_gateway(db, order, principal, gateway or get_order_gateway())
    commit_or_release_holds_for_order(
        db,
        order.id,
        target_state="RELEASED" if gw_res.status == "error" else "COMMITTED",
        now=now,
    )

    order_out = order_to_out(order)
    response_body = order_out.model_dump(mode="json")

    try:
        store_idempotency(
            db,
            principal=principal,
            endpoint=endpoint,
            idem_key=idem_key,
            request_hash=request_hash,
            status_code=200,
            response_body=response_body,
        )
        db.commit()
    except IntegrityError:
        db.rollback()
        replay = lookup_idempotency(
            db,
            principal=principal,
            endpoint=endpoint,
            idem_key=idem_key,
            request_hash=request_hash,
        )
        if replay is not None:
            return replay
        raise

    return 200, response_body


def reject_order(
    db: Session,
    principal: Principal,
    order_id: str,
    req: OrderDecisionRequest,
) -> OrderOut:
    """Reject a PENDING_APPROVAL order (user-only)."""
    if sync_expired_holds(db) > 0:
        db.commit()
    order = _load_company_order(db, order_id, principal.company_id, for_update=True)

    if _expire_single_order_if_needed(db, order):
        raise AppError(
            "INVALID_STATE",
            409,
            "Order approval has expired",
            details={"status": order.status},
        )

    if order.status != "PENDING_APPROVAL":
        raise AppError(
            "INVALID_STATE",
            409,
            f"Order cannot be rejected in status {order.status}",
            details={"status": order.status},
        )

    now = clock.now()
    order.decided_by_kind = principal.kind
    order.decided_by_id = principal.id
    order.decided_by_name = principal.name
    order.decided_at = now
    order.decision_note = req.note
    commit_or_release_holds_for_order(db, order.id, target_state="RELEASED", now=now)

    transition_order(
        db,
        order,
        "REJECTED",
        principal,
        req.note or "Order rejected by user",
        "order.rejected",
        audit_details={
            "quote_id": order.quote_id,
            "reasoning_steps": _build_gateway_step(
                stage="human_rejection",
                summary=f"Human user {principal.name} rejected order ({req.note or 'no note'})",
                actor=principal,
                details={"note": req.note},
            ),
        },
    )
    db.commit()
    return order_to_out(order)


def cancel_order(
    db: Session,
    principal: Principal,
    order_id: str,
    req: OrderDecisionRequest,
) -> OrderOut:
    """Cancel PENDING_APPROVAL (user or owning agent) or PLACED before cutoff (human user only)."""
    if sync_expired_holds(db) > 0:
        db.commit()
    order = _load_company_order(db, order_id, principal.company_id, for_update=True)

    if principal.kind == "agent" and (
        order.created_by_kind != "agent" or order.created_by_id != principal.id
    ):
        raise AppError("NOT_FOUND", 404, "Order not found")

    if _expire_single_order_if_needed(db, order):
        raise AppError(
            "INVALID_STATE",
            409,
            "Order approval has expired",
            details={"status": order.status},
        )

    now = clock.now()
    now_hel = clock.to_helsinki(now)

    if order.status == "PLACED":
        if principal.kind != "user":
            raise AppError(
                "INVALID_STATE",
                409,
                f"Order cannot be cancelled in status {order.status}",
                details={"status": order.status},
            )
        raw_cancel_sched = (order.policy or {}).get("_delivery_schedule")
        cancel_rescheduled_set = (
            set(raw_cancel_sched.get("rescheduled_cutoff_product_ids") or [])
            if isinstance(raw_cancel_sched, dict)
            else set()
        )
        has_closing_time = False
        for ln in order.lines:
            if ln.product_id in cancel_rescheduled_set or not ln.closing_time:
                continue
            try:
                closing_dt = clock.to_helsinki(datetime.fromisoformat(ln.closing_time))
                has_closing_time = True
            except ValueError:
                continue
            if now_hel >= closing_dt:
                raise AppError(
                    "INVALID_STATE",
                    409,
                    "Order cannot be cancelled after delivery cut-off time",
                    details={"status": order.status, "closing_time": ln.closing_time},
                )
        if not has_closing_time and order.requested_delivery_date < clock.format_helsinki_iso(now)[
            :10
        ]:
            raise AppError(
                "INVALID_STATE",
                409,
                "Order cannot be cancelled after delivery date has passed",
                details={"status": order.status},
            )
    elif order.status != "PENDING_APPROVAL":
        raise AppError(
            "INVALID_STATE",
            409,
            f"Order cannot be cancelled in status {order.status}",
            details={"status": order.status},
        )

    prior_status = order.status
    order.decided_by_kind = principal.kind
    order.decided_by_id = principal.id
    order.decided_by_name = principal.name
    order.decided_at = now
    order.decision_note = req.note
    commit_or_release_holds_for_order(db, order.id, target_state="RELEASED", now=now)

    transition_order(
        db,
        order,
        "CANCELLED",
        principal,
        req.note or "Order cancelled",
        "order.cancelled",
        audit_details={
            "quote_id": order.quote_id,
            "prior_status": prior_status,
            "reasoning_steps": _build_gateway_step(
                stage="order_cancellation",
                summary=(
                    f"{principal.name} cancelled {prior_status.lower()} order "
                    f"({req.note or 'no note'})"
                ),
                actor=principal,
                details={"note": req.note, "prior_status": prior_status},
            ),
        },
    )
    db.commit()
    return order_to_out(order)


def list_orders(
    db: Session,
    principal: Principal,
    *,
    status_filter: OrderStatus | None = None,
    created_by_agent: bool | None = None,
    agent_id: str | None = None,
    limit: int = 20,
    offset: int = 0,
) -> OrderListResponse:
    """List company-isolated orders (agents see only their own)."""
    expire_pending_orders(db, company_id=principal.company_id)

    filters = [Order.company_id == principal.company_id]
    if principal.kind == "agent":
        filters.append(Order.created_by_kind == "agent")
        filters.append(Order.created_by_id == principal.id)
    if status_filter is not None:
        filters.append(Order.status == status_filter)
    if created_by_agent is True:
        filters.append(Order.created_by_kind == "agent")
    elif created_by_agent is False:
        filters.append(Order.created_by_kind == "user")
    if principal.kind == "user" and agent_id is not None and agent_id.strip():
        filters.append(Order.created_by_kind == "agent")
        filters.append(Order.created_by_id == agent_id.strip())

    total = db.scalar(select(func.count()).select_from(Order).where(*filters)) or 0
    rows = db.scalars(
        select(Order)
        .options(selectinload(Order.lines), selectinload(Order.events))
        .where(*filters)
        .order_by(Order.created_at.desc(), Order.id.desc())
        .offset(offset)
        .limit(limit)
    ).all()

    return OrderListResponse(
        items=[order_to_out(row) for row in rows],
        total=total,
    )


def get_order(db: Session, principal: Principal, order_id: str) -> OrderOut:
    """Fetch a single company-isolated order (agents see only their own)."""
    expire_pending_orders(db, company_id=principal.company_id)
    order = _load_company_order(db, order_id, principal.company_id)
    if principal.kind == "agent" and (
        order.created_by_kind != "agent" or order.created_by_id != principal.id
    ):
        raise AppError("NOT_FOUND", 404, "Order not found")
    return order_to_out(order)


def reconcile_orders(
    db: Session,
    gateway: OrderGateway | None = None,
    *,
    recover_all_submitting: bool = False,
) -> dict[str, int]:
    """Reconcile SUBMISSION_UNKNOWN and stale/crashed SUBMITTING orders safely (lookup first)."""
    gw = gateway or get_order_gateway()
    now = clock.now()
    submitting_cutoff = now - timedelta(seconds=60)

    candidates = db.scalars(
        select(Order)
        .options(selectinload(Order.lines), selectinload(Order.events))
        .where(Order.status.in_(("SUBMISSION_UNKNOWN", "SUBMITTING")))
        .order_by(Order.updated_at.asc(), Order.id.asc())
    ).all()

    counts: dict[str, int] = {
        "checked": 0,
        "placed": 0,
        "failed": 0,
        "unknown": 0,
    }

    for order in candidates:
        if order.status == "SUBMITTING" and not recover_all_submitting:
            if clock.to_helsinki(order.updated_at) > submitting_cutoff:
                continue

        counts["checked"] += 1
        existing_ref = gw.lookup(db, order.id)
        if existing_ref is not None:
            order.gateway_reference = existing_ref
            transition_order(
                db,
                order,
                "PLACED",
                None,
                "Reconciler confirmed existing gateway submission",
                "order.placed",
                audit_details={"gateway_reference": existing_ref, "reconciled": True},
            )
            counts["placed"] += 1
            continue

        if order.gateway_attempts >= MAX_RECONCILE_ATTEMPTS:
            transition_order(
                db,
                order,
                "FAILED",
                None,
                f"Reconciliation exhausted ({MAX_RECONCILE_ATTEMPTS} attempts): "
                "needs human attention",
                "order.failed",
                audit_details={
                    "gateway_attempts": order.gateway_attempts,
                    "needs_human_attention": True,
                },
            )
            counts["failed"] += 1
            continue

        if order.status == "SUBMISSION_UNKNOWN":
            transition_order(
                db,
                order,
                "SUBMITTING",
                None,
                f"Reconciler retrying gateway submission (attempt {order.gateway_attempts + 1})",
                "order.reconciling",
                audit_details={"attempt": order.gateway_attempts + 1},
            )

        order.gateway_attempts += 1
        db.flush()
        result = _submit_via_gateway(db, order, None, gw)
        if result.status == "ok":
            counts["placed"] += 1
        elif result.status == "error":
            counts["failed"] += 1
        else:
            counts["unknown"] += 1

    if counts["checked"] > 0:
        db.commit()

    return counts


def _parse_trace_dt(raw: Any, fallback: datetime) -> datetime:
    if isinstance(raw, datetime):
        return clock.to_helsinki(raw)
    if isinstance(raw, str) and raw.strip():
        try:
            return clock.to_helsinki(datetime.fromisoformat(raw))
        except ValueError:
            return clock.to_helsinki(fallback)
    return clock.to_helsinki(fallback)


def get_order_trace(
    db: Session,
    principal: Principal,
    order_id: str,
) -> OrderTraceResponse:
    """Assemble the unified chronological 'How we got here' trace for an order."""
    expire_pending_orders(db, company_id=principal.company_id)
    order = _load_company_order(db, order_id, principal.company_id)
    if principal.kind == "agent" and (
        order.created_by_kind != "agent" or order.created_by_id != principal.id
    ):
        raise AppError("NOT_FOUND", 404, "Order not found")

    raw_policy = dict(order.policy or {})
    raw_policy.pop("_diet", None)
    raw_src = raw_policy.pop("_source_context", None)
    raw_policy.pop("_recipe_context", None)
    raw_policy.pop("_recipe_sanity_warning", None)
    raw_policy.pop("_reservation", None)
    raw_policy.pop("_negotiation", None)
    raw_chat_trace = raw_policy.pop("_chat_trace", None)
    raw_policy.pop("_assurance", None)
    raw_policy.pop("_delivery_schedule", None)
    policy_decision = PolicyDecision.model_validate(raw_policy)
    chat_trace: dict[str, Any] | None = (
        dict(raw_chat_trace) if isinstance(raw_chat_trace, dict) else None
    )
    src_ctx = dict(raw_src) if isinstance(raw_src, dict) else {}

    conv_id: str | None = (
        str(src_ctx["conversation_id"]) if src_ctx.get("conversation_id") else None
    )
    a2a_task_id: str | None = str(src_ctx["a2a_task_id"]) if src_ctx.get("a2a_task_id") else None

    # Also discover linked A2A task by linked_order_id if not in source_context
    linked_task: A2ATask | None = db.scalar(
        select(A2ATask).where(
            A2ATask.company_id == principal.company_id,
            A2ATask.linked_order_id == order.id,
        )
    )
    if linked_task is not None and a2a_task_id is None:
        a2a_task_id = linked_task.id
    elif a2a_task_id is not None and linked_task is None:
        linked_task = db.scalar(
            select(A2ATask).where(
                A2ATask.company_id == principal.company_id,
                A2ATask.id == a2a_task_id,
            )
        )

    # Collect company audit events referencing this order, its quote, or discovered source context
    company_audits = db.scalars(
        select(AuditEventModel)
        .where(AuditEventModel.company_id == principal.company_id)
        .order_by(AuditEventModel.at.asc(), AuditEventModel.id.asc())
    ).all()

    relevant_audits: list[AuditEventModel] = []
    for ev in company_audits:
        det = ev.details if isinstance(ev.details, dict) else {}
        det_quote = det.get("quote_id")
        det_order = det.get("order_id")
        det_src = det.get("source_context") if isinstance(det.get("source_context"), dict) else {}
        det_conv = det.get("conversation_id") or det_src.get("conversation_id")
        det_a2a = det.get("a2a_task_id") or det_src.get("a2a_task_id")

        matches_order_or_quote = (
            ev.entity_id in {order.id, order.quote_id}
            or det_quote == order.quote_id
            or det_order == order.id
        )
        if matches_order_or_quote:
            if conv_id is None and isinstance(det_conv, str) and det_conv:
                conv_id = det_conv
            if a2a_task_id is None and isinstance(det_a2a, str) and det_a2a:
                a2a_task_id = det_a2a
            relevant_audits.append(ev)

    # Include linked assistant / a2a audit events too
    if conv_id or a2a_task_id:
        seen_audit_ids = {ev.id for ev in relevant_audits}
        for ev in company_audits:
            if ev.id in seen_audit_ids:
                continue
            if conv_id and ev.entity_id == conv_id:
                relevant_audits.append(ev)
                seen_audit_ids.add(ev.id)
            elif a2a_task_id and ev.entity_id == a2a_task_id:
                relevant_audits.append(ev)
                seen_audit_ids.add(ev.id)
        relevant_audits.sort(key=lambda e: (clock.to_helsinki(e.at), e.id))

    # Raw sortable items: (at_dt, source_rank, seq, OrderTraceStepOut)
    sortable: list[tuple[datetime, int, int, OrderTraceStepOut]] = []
    seq = 0

    def push_step(
        at_dt: datetime,
        rank: int,
        *,
        source: str,
        stage: str,
        summary: str,
        actor_kind: str | None = None,
        actor_id: str | None = None,
        actor_name: str | None = None,
        request_id: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        nonlocal seq
        seq += 1
        sortable.append(
            (
                clock.to_helsinki(at_dt),
                rank,
                seq,
                OrderTraceStepOut(
                    step=0,
                    source=cast(Any, source),
                    stage=stage,
                    summary=summary,
                    actor_kind=actor_kind,
                    actor_id=actor_id,
                    actor_name=actor_name,
                    at=clock.to_helsinki(at_dt),
                    request_id=request_id,
                    details=details or {},
                ),
            )
        )

    # 1. Linked AssistantConversation turns
    if conv_id:
        conv = db.scalar(
            select(AssistantConversation).where(
                AssistantConversation.id == conv_id,
                AssistantConversation.company_id == principal.company_id,
            )
        )
        if conv is not None:
            for idx, msg in enumerate(conv.messages or []):
                if not isinstance(msg, dict):
                    continue
                role = str(msg.get("role") or "user")
                content = str(msg.get("content") or "")
                at_dt = _parse_trace_dt(msg.get("at"), conv.created_at)
                tools_called = msg.get("tools_called")
                msg_details: dict[str, Any] = {
                    "conversation_id": conv.id,
                    "turn_index": idx,
                    "role": role,
                }
                if msg.get("context_note"):
                    msg_details["context_note"] = msg["context_note"]
                if tools_called:
                    msg_details["tools_called"] = tools_called
                push_step(
                    at_dt,
                    0,
                    source="assistant",
                    stage=f"assistant_{role}_turn",
                    summary=f"[{role}] {content[:240]}",
                    actor_kind=conv.principal_kind if role == "user" else "assistant",
                    actor_id=conv.principal_id if role == "user" else None,
                    details=msg_details,
                )

    # 2. Linked A2ATask transitions & artifacts
    if linked_task is not None:
        history = (linked_task.context or {}).get("history")
        if isinstance(history, list):
            for idx, entry in enumerate(history):
                if not isinstance(entry, dict):
                    continue
                at_dt = _parse_trace_dt(entry.get("at"), linked_task.created_at)
                role = str(entry.get("role") or "agent")
                state = str(entry.get("state") or linked_task.state)
                msg_text = str(entry.get("message") or "")
                push_step(
                    at_dt,
                    0,
                    source="a2a",
                    stage=f"a2a_{state}",
                    summary=f"[A2A:{role}/{state}] {msg_text[:240]}",
                    actor_kind="agent" if role == "agent" else "system",
                    actor_id=linked_task.principal_id if role == "agent" else None,
                    details={
                        "a2a_task_id": linked_task.id,
                        "history_index": idx,
                        "state": state,
                        "data": entry.get("data"),
                    },
                )
        if linked_task.artifacts:
            push_step(
                linked_task.updated_at,
                1,
                source="a2a",
                stage="a2a_artifacts",
                summary=(
                    f"A2A task {linked_task.id} produced {len(linked_task.artifacts)} artifact(s)"
                ),
                actor_kind="system",
                details={"a2a_task_id": linked_task.id, "artifacts": linked_task.artifacts},
            )

    # 3. AuditEventModel rows + nested reasoning_steps
    has_legacy_assistant_chat = any(ev.action == "assistant.chat" for ev in relevant_audits)
    for ev in relevant_audits:
        actor = ev.actor if isinstance(ev.actor, dict) else {}
        det = dict(ev.details) if isinstance(ev.details, dict) else {}
        if chat_trace is None and ev.entity_type == "assistant_chat" and det:
            chat_trace = {k: v for k, v in det.items() if k != "reasoning_steps"}
        raw_reasoning = det.get("reasoning_steps")
        push_step(
            ev.at,
            2,
            source="audit",
            stage=ev.action,
            summary=(
                f"Audit {ev.action} ({ev.outcome}) on {ev.entity_type} {ev.entity_id or ''}"
            ).strip(),
            actor_kind=actor.get("kind"),
            actor_id=actor.get("id") or ev.actor_id,
            actor_name=actor.get("name"),
            request_id=ev.request_id,
            details=det,
        )
        if isinstance(raw_reasoning, list) and not (
            ev.entity_type == "assistant_chat" and has_legacy_assistant_chat
        ):
            for rs in raw_reasoning:
                if not isinstance(rs, dict):
                    continue
                rs_dt = _parse_trace_dt(rs.get("at"), ev.at)
                push_step(
                    rs_dt,
                    3,
                    source="reasoning",
                    stage=str(rs.get("stage") or ev.action),
                    summary=str(rs.get("summary") or ""),
                    actor_kind=str(rs.get("actor_kind") or actor.get("kind") or "system"),
                    actor_id=actor.get("id") or ev.actor_id,
                    actor_name=actor.get("name"),
                    request_id=ev.request_id,
                    details=dict(rs.get("details") or {}),
                )

    # 4. OrderEventModel rows
    for evt in order.events:
        actor = evt.actor if isinstance(evt.actor, dict) else {}
        push_step(
            evt.at,
            4,
            source="order_event",
            stage=evt.type,
            summary=evt.message,
            actor_kind=actor.get("kind"),
            actor_id=actor.get("id"),
            actor_name=actor.get("name"),
            request_id=evt.request_id,
            details={"order_event_id": evt.id, "order_id": order.id},
        )

    sortable.sort(key=lambda item: (item[0], item[1], item[2]))
    ordered_steps = [
        item[3].model_copy(update={"step": idx + 1}) for idx, item in enumerate(sortable)
    ]

    verify_res = verify_audit_chain(db, principal.company_id)
    order_status = cast(
        OrderStatus,
        order.status
        if order.status
        in (
            "PENDING_APPROVAL",
            "SUBMITTING",
            "PLACED",
            "SUBMISSION_UNKNOWN",
            "FAILED",
            "REJECTED",
            "EXPIRED",
            "CANCELLED",
        )
        else "FAILED",
    )

    return OrderTraceResponse(
        order_id=order.id,
        quote_id=order.quote_id,
        status=order_status,
        policy_decision=policy_decision,
        fired_rules=list(policy_decision.rules),
        steps=ordered_steps,
        chain_verified=verify_res.valid,
        source_conversation_id=conv_id,
        source_a2a_task_id=a2a_task_id,
        chat_trace=chat_trace,
    )
