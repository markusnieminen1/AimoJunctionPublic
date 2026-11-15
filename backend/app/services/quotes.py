"""Quote creation, pricing/delivery aggregation, diet verification, and expiry."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Any, Literal, cast

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import clock
from app.aimo.client import AimoClient
from app.aimo.normalise import check_products_live
from app.audit import record_audit
from app.auth import compute_agent_spent_today
from app.errors import AppError, request_id_var
from app.ids import new_id
from app.models import Agent, OrderLineModel, Product, Quote, QuoteLineModel
from app.schemas import (
    DeliveryStatus,
    LineFlag,
    PolicyDecision,
    Principal,
    PrincipalRef,
    QuoteCreateRequest,
    QuoteLine,
    QuoteOut,
    RecipeSanityWarning,
)
from app.services import purchasing_guard
from app.services.catalog import product_passes_diets, resolve_diet_keys
from app.services.policy import evaluate_policy
from app.services.recipe_sanity import evaluate_recipe_sanity

DELIVERY_SEVERITY: dict[DeliveryStatus, int] = {
    "ON_TIME": 0,
    "UNKNOWN": 1,
    "AT_RISK": 2,
    "LATE": 3,
    "NOT_DELIVERABLE": 4,
}


def worst_delivery_status(statuses: Sequence[DeliveryStatus]) -> DeliveryStatus:
    """Return the worst DeliveryStatus (NOT_DELIVERABLE > LATE > AT_RISK > UNKNOWN > ON_TIME)."""
    if not statuses:
        return "UNKNOWN"
    return max(statuses, key=lambda s: DELIVERY_SEVERITY.get(s, 1))


def build_principal_ref(
    *,
    kind: str,
    principal_id: str,
    name: str,
    delegated_by_id: str | None = None,
    delegated_by_name: str | None = None,
) -> Principal:
    """Reconstruct a Principal schema from stored ORM actor columns."""
    normalized_kind = cast(Literal["user", "agent"], kind if kind in ("user", "agent") else "user")
    delegated_by = (
        PrincipalRef(id=delegated_by_id, name=delegated_by_name or "Unknown user")
        if normalized_kind == "agent" and delegated_by_id
        else None
    )
    return Principal(
        kind=normalized_kind,
        id=principal_id,
        name=name,
        company_id="",
        delegated_by=delegated_by,
    )


def principal_from_company_columns(
    *,
    company_id: str,
    kind: str,
    principal_id: str,
    name: str,
    delegated_by_id: str | None = None,
    delegated_by_name: str | None = None,
) -> Principal:
    """Reconstruct a Principal schema including company_id."""
    normalized_kind = cast(Literal["user", "agent"], kind if kind in ("user", "agent") else "user")
    delegated_by = (
        PrincipalRef(id=delegated_by_id, name=delegated_by_name or "Unknown user")
        if normalized_kind == "agent" and delegated_by_id
        else None
    )
    return Principal(
        kind=normalized_kind,
        id=principal_id,
        name=name,
        company_id=company_id,
        delegated_by=delegated_by,
    )


def line_model_to_schema(row: QuoteLineModel | OrderLineModel) -> QuoteLine:
    """Map a QuoteLineModel or OrderLineModel ORM row to a QuoteLine schema."""
    logistic = (
        cast(Literal["Stock", "Terminal"], row.logistic_model)
        if row.logistic_model in ("Stock", "Terminal")
        else None
    )
    qty_state = (
        cast(
            Literal["FINITE", "UNLIMITED", "TERMINAL_UNKNOWN", "ZERO"],
            row.available_qty_state,
        )
        if row.available_qty_state in ("FINITE", "UNLIMITED", "TERMINAL_UNKNOWN", "ZERO")
        else None
    )
    delivery = cast(
        DeliveryStatus,
        row.delivery_status if row.delivery_status in DELIVERY_SEVERITY else "UNKNOWN",
    )
    flags = [cast(LineFlag, f) for f in (row.flags or [])]
    return QuoteLine(
        product_id=row.product_id,
        product_name=row.product_name,
        quantity=row.quantity,
        sales_unit=row.sales_unit,
        unit_price_eur=row.unit_price_eur,
        line_total_eur=row.line_total_eur,
        tax_rate=row.tax_rate,
        available_date=row.available_date,
        closing_time=row.closing_time,
        logistic_model=logistic,
        available_qty_state=qty_state,
        available_amount=row.available_amount,
        delivery_status=delivery,
        on_time_probability=row.on_time_probability,
        co2_kg=row.co2_kg,
        co2_basis=row.co2_basis,
        flags=flags,
    )


def evaluate_diet_violations(
    product_map: dict[str, Product],
    lines: Sequence[QuoteLine],
    diet_param: str | None,
) -> list[str]:
    """Return human-readable violation strings for any line failing the requested diet."""
    if not diet_param or not diet_param.strip():
        return []
    diet_keys = resolve_diet_keys(diet_param, None)
    if not diet_keys:
        return []

    violations: list[str] = []
    for line in lines:
        product = product_map.get(line.product_id)
        if product is None:
            violations.append(f"{line.product_id}: unknown product")
            continue
        passed, reasons = product_passes_diets(product, diet_keys)
        if not passed:
            detail = "; ".join(reasons) if reasons else "does not meet diet requirements"
            violations.append(f"{line.product_id} ({line.product_name}): {detail}")
    return violations


def quote_to_out(quote: Quote) -> QuoteOut:
    """Serialize a Quote ORM row (with loaded lines) to QuoteOut."""
    raw_preview = dict(quote.policy_preview or {})
    raw_diet = raw_preview.pop("_diet", None)
    raw_preview.pop("_source_context", None)
    raw_preview.pop("_recipe_context", None)
    raw_recipe_warning = raw_preview.pop("_recipe_sanity_warning", None)
    raw_negotiation = raw_preview.pop("_negotiation", None)
    raw_preview.pop("_chat_trace", None)
    raw_preview.pop("_assurance", None)
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
    policy_preview = PolicyDecision.model_validate(raw_preview)

    status = cast(
        Literal["OPEN", "USED", "EXPIRED"],
        quote.status if quote.status in ("OPEN", "USED", "EXPIRED") else "EXPIRED",
    )
    overall = cast(
        DeliveryStatus,
        quote.overall_delivery_status
        if quote.overall_delivery_status in DELIVERY_SEVERITY
        else "UNKNOWN",
    )
    created_by = principal_from_company_columns(
        company_id=quote.company_id,
        kind=quote.created_by_kind,
        principal_id=quote.created_by_id,
        name=quote.created_by_name,
        delegated_by_id=quote.delegated_by_id,
        delegated_by_name=quote.delegated_by_name,
    )
    return QuoteOut(
        id=quote.id,
        status=status,
        created_at=quote.created_at,
        expires_at=quote.expires_at,
        requested_delivery_date=quote.requested_delivery_date,
        lines=[line_model_to_schema(row) for row in quote.lines],
        total_excl_vat_eur=quote.total_excl_vat_eur,
        total_incl_vat_eur=quote.total_incl_vat_eur,
        total_co2_kg=quote.total_co2_kg,
        overall_delivery_status=overall,
        policy_preview=policy_preview,
        created_by=created_by,
        diet=diet,
        recipe_sanity_warning=recipe_warning,
        negotiated_savings_eur=negotiated_savings_eur,
    )


def compute_quote_expires_at(lines: Sequence[QuoteLine], now: datetime) -> datetime:
    """Compute quote expiry: min(now + 30 min, earliest future closing_time)."""
    default_expiry = now + timedelta(minutes=30)
    candidates: list[datetime] = [default_expiry]
    for line in lines:
        if not line.closing_time:
            continue
        try:
            closing_dt = clock.to_helsinki(datetime.fromisoformat(line.closing_time))
        except ValueError:
            continue
        if closing_dt > now:
            candidates.append(closing_dt)
    return min(candidates)


def create_quote(
    db: Session,
    aimo_client: AimoClient,
    principal: Principal,
    req: QuoteCreateRequest,
    *,
    source_context: dict[str, Any] | None = None,
) -> QuoteOut:
    """Check live prices/availability, evaluate policy preview, and persist a locked Quote."""
    aggregated_qty: dict[str, float] = {}
    for item in req.lines:
        aggregated_qty[item.product_id] = aggregated_qty.get(item.product_id, 0.0) + float(
            item.quantity
        )
    product_ids = list(aggregated_qty.keys())

    # Validate diet early so invalid diet codes return 422 before upstream calls.
    if req.diet and req.diet.strip():
        resolve_diet_keys(req.diet, None)

    lines = check_products_live(
        db=db,
        aimo_client=aimo_client,
        product_ids=product_ids,
        quantities=aggregated_qty,
        requested_delivery_date=req.requested_delivery_date,
        customer_id=principal.company_id,
    )

    products = db.scalars(select(Product).where(Product.id.in_(product_ids))).all()
    product_map = {p.id: p for p in products}
    product_categories = {p.id: p.category for p in products}

    lot_size_warnings: list[dict[str, object]] = []
    for line in lines:
        prod = product_map.get(line.product_id)
        if prod and prod.allowed_lot_size and prod.allowed_lot_size > 1:
            rem = line.quantity % float(prod.allowed_lot_size)
            if rem > 1e-6 and abs(rem - float(prod.allowed_lot_size)) > 1e-6:
                lot_size_warnings.append(
                    {
                        "product_id": line.product_id,
                        "quantity": line.quantity,
                        "allowed_lot_size": float(prod.allowed_lot_size),
                    }
                )

    total_excl_vat_eur = round(
        sum(line.line_total_eur for line in lines if line.line_total_eur is not None),
        2,
    )
    total_incl_vat_eur = round(
        sum(
            round(line.line_total_eur * (1.0 + (line.tax_rate or 0.0) / 100.0), 2)
            for line in lines
            if line.line_total_eur is not None
        ),
        2,
    )
    co2_values = [line.co2_kg for line in lines if line.co2_kg is not None]
    total_co2_kg = round(sum(co2_values), 4) if co2_values else None
    overall_delivery_status = worst_delivery_status([line.delivery_status for line in lines])

    diet_violations = evaluate_diet_violations(product_map, lines, req.diet)
    recipe_warning = evaluate_recipe_sanity(
        product_map=product_map,
        lines=lines,
        recipe_id=req.recipe_id,
        target_pax=req.target_pax,
    )

    agent: Agent | None = None
    spent_today_eur = 0.0
    standing_drift_reason: str | None = None
    standing_drift_product_ids: list[str] = []
    if principal.kind == "agent":
        agent = db.get(Agent, principal.id)
        spent_today_eur = compute_agent_spent_today(db, principal.id, principal.company_id)
        standing_drift_reason, standing_drift_product_ids = (
            purchasing_guard.evaluate_standing_order_drift(
                db,
                company_id=principal.company_id,
                lines=lines,
            )
        )

    policy_preview = evaluate_policy(
        principal=principal,
        agent=agent,
        lines=lines,
        product_categories=product_categories,
        total_excl_vat_eur=total_excl_vat_eur,
        spent_today_eur=spent_today_eur,
        diet_violations=diet_violations or None,
        recipe_sanity_warning=recipe_warning,
        standing_drift_reason=standing_drift_reason,
        standing_drift_product_ids=standing_drift_product_ids,
    )

    now = clock.now()
    expires_at = compute_quote_expires_at(lines, now)
    policy_preview_json = policy_preview.model_dump(mode="json")
    if req.diet and req.diet.strip():
        policy_preview_json["_diet"] = req.diet.strip()
    if req.recipe_id or req.target_pax is not None:
        policy_preview_json["_recipe_context"] = {
            "recipe_id": req.recipe_id,
            "target_pax": req.target_pax,
        }
    if recipe_warning is not None and recipe_warning.triggered:
        policy_preview_json["_recipe_sanity_warning"] = recipe_warning.model_dump(mode="json")
    if source_context:
        clean_src = dict(source_context)
        clean_src.pop("upstream_injection_screened", None)
        raw_chat_trace = clean_src.pop("_chat_trace", None)
        raw_assurance = clean_src.pop("_assurance", None)
        if isinstance(raw_chat_trace, dict):
            policy_preview_json["_chat_trace"] = raw_chat_trace
        if isinstance(raw_assurance, dict):
            policy_preview_json["_assurance"] = raw_assurance
        if clean_src:
            policy_preview_json["_source_context"] = clean_src

    quote = Quote(
        id=new_id("qte_"),
        company_id=principal.company_id,
        status="OPEN",
        created_at=now,
        expires_at=expires_at,
        requested_delivery_date=req.requested_delivery_date,
        total_excl_vat_eur=total_excl_vat_eur,
        total_incl_vat_eur=total_incl_vat_eur,
        total_co2_kg=total_co2_kg,
        overall_delivery_status=overall_delivery_status,
        policy_preview=policy_preview_json,
        created_by_kind=principal.kind,
        created_by_id=principal.id,
        created_by_name=principal.name,
        delegated_by_id=principal.delegated_by.id if principal.delegated_by else None,
        delegated_by_name=principal.delegated_by.name if principal.delegated_by else None,
    )
    db.add(quote)
    db.flush()

    for idx, line in enumerate(lines):
        db.add(
            QuoteLineModel(
                quote_id=quote.id,
                line_index=idx,
                product_id=line.product_id,
                product_name=line.product_name,
                quantity=line.quantity,
                sales_unit=line.sales_unit,
                unit_price_eur=line.unit_price_eur,
                line_total_eur=line.line_total_eur,
                tax_rate=line.tax_rate,
                available_date=line.available_date,
                closing_time=line.closing_time,
                logistic_model=line.logistic_model,
                available_qty_state=line.available_qty_state,
                available_amount=line.available_amount,
                delivery_status=line.delivery_status,
                on_time_probability=line.on_time_probability,
                co2_kg=line.co2_kg,
                co2_basis=line.co2_basis,
                flags=list(line.flags),
            )
        )
    db.flush()
    db.refresh(quote)

    now_iso = clock.format_helsinki_iso(now)
    fired_rules = [r.rule_id for r in policy_preview.rules]
    reasoning_steps: list[dict[str, Any]] = [
        {
            "step": 1,
            "stage": "live_product_check",
            "summary": (
                f"Checked live price and availability for {len(lines)} SKU(s) "
                f"for {req.requested_delivery_date}; overall delivery: {overall_delivery_status}"
            ),
            "actor_kind": principal.kind,
            "at": now_iso,
            "details": {
                "product_ids": product_ids,
                "total_excl_vat_eur": total_excl_vat_eur,
                "overall_delivery_status": overall_delivery_status,
            },
        },
        {
            "step": 2,
            "stage": "diet_safety_screen",
            "summary": (
                f"Diet safety violation(s) found for [{req.diet}]: {'; '.join(diet_violations)}"
                if diet_violations
                else (
                    f"Verified [{req.diet}] dietary compliance across all {len(lines)} line(s)"
                    if req.diet and req.diet.strip()
                    else "No hard diet filter specified on quote"
                )
            ),
            "actor_kind": "system",
            "at": now_iso,
            "details": {
                "diet": req.diet.strip() if req.diet and req.diet.strip() else None,
                "violations": list(diet_violations),
            },
        },
        {
            "step": 3,
            "stage": "policy_preview",
            "summary": (
                f"Layer 1 policy preview: {policy_preview.outcome} "
                f"(rules: {', '.join(fired_rules) or 'none'})"
            ),
            "actor_kind": "system",
            "at": now_iso,
            "details": {
                "outcome": policy_preview.outcome,
                "fired_rules": fired_rules,
            },
        },
    ]

    audit_details: dict[str, object] = {
        "request_id": request_id_var.get() or None,
        "requested_delivery_date": req.requested_delivery_date,
        "line_count": len(lines),
        "total_excl_vat_eur": total_excl_vat_eur,
        "overall_delivery_status": overall_delivery_status,
        "policy_outcome": policy_preview.outcome,
        "fired_rules": fired_rules,
        "reasoning_steps": reasoning_steps,
    }
    if req.diet and req.diet.strip():
        audit_details["diet"] = req.diet.strip()
    if lot_size_warnings:
        audit_details["lot_size_warnings"] = lot_size_warnings
    if recipe_warning is not None and recipe_warning.triggered:
        audit_details["recipe_sanity_warning"] = recipe_warning.model_dump(mode="json")
    if source_context:
        audit_details["source_context"] = dict(source_context)
        if source_context.get("routing_path"):
            audit_details["routing_path"] = source_context["routing_path"]
        if source_context.get("conversation_id"):
            audit_details["conversation_id"] = source_context["conversation_id"]
        if source_context.get("a2a_task_id"):
            audit_details["a2a_task_id"] = source_context["a2a_task_id"]

    record_audit(
        db,
        company_id=principal.company_id,
        actor=principal,
        action="quote.created",
        entity_type="quote",
        entity_id=quote.id,
        outcome="ok",
        details=audit_details,
    )
    db.commit()
    db.refresh(quote)
    return quote_to_out(quote)


def get_quote(db: Session, principal: Principal, quote_id: str) -> QuoteOut:
    """Fetch a company-isolated Quote, lazily expiring it if past expires_at."""
    quote = db.scalar(
        select(Quote).where(Quote.id == quote_id, Quote.company_id == principal.company_id)
    )
    if quote is None:
        raise AppError("NOT_FOUND", 404, "Quote not found")

    if quote.status == "OPEN" and clock.now() >= clock.to_helsinki(quote.expires_at):
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
        db.refresh(quote)

    return quote_to_out(quote)
