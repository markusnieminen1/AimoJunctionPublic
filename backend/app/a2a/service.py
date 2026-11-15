"""Layer 3 A2A protocol implementation: Agent Card, JSON-RPC 2.0 tasks, and order sync."""

from __future__ import annotations

import re
from typing import Any, Literal, cast

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app import clock
from app.aimo.client import AimoClient
from app.aimo.normalise import check_products_live
from app.audit import record_audit
from app.config import settings
from app.errors import AppError, get_request_id
from app.ids import new_id
from app.llm.decisions import (
    DecisionProvider,
    is_prompt_injection,
    needs_clarification,
)
from app.llm.prep_list import default_delivery_date, parse_prep_list
from app.models import A2ATask, Company, Order, Product
from app.schemas import (
    A2AAgentCapabilities,
    A2AAgentCard,
    A2AAgentSkill,
    A2ARpcRequest,
    A2ASecurityScheme,
    A2ASupportedMatter,
    A2ATaskListResponse,
    A2ATaskState,
    A2ATaskTraceOut,
    A2ATraceEntry,
    OrderCreateRequest,
    OrderDecisionRequest,
    OrderOut,
    Principal,
    ProductOut,
    QuoteCreateRequest,
    QuoteLine,
    QuoteLineInput,
    QuoteOut,
)
from app.services import catalog, negotiation, orders, purchasing_guard, quotes, substitutes
from app.services.order_gateway import OrderGateway

_SUPPORTED_METHODS = frozenset(
    {
        "message/send",
        "tasks/get",
        "tasks/cancel",
        "session/connect",
        "session/capabilities",
    }
)
_TERMINAL_STATES = frozenset({"completed", "failed", "canceled"})
_PRODUCT_ID_RE = re.compile(r"\b(\d{6})\b")
_CAPABILITY_GREETING_RE = re.compile(
    r"\b(preflight|pre-flight|what\s+are\s+your\s+capabilities|"
    r"what\s+(?:matters\s+|things\s+)?can\s+you\s+(?:handle|do|support)|"
    r"what\s+do\s+you\s+(?:handle|support)|supported\s+matters|session\s+capabilities|"
    r"capabilities\b|hello\b|hi\b)\b",
    re.I,
)

_ORDER_TO_TASK_STATE: dict[str, tuple[A2ATaskState, str]] = {
    "PLACED": ("completed", "ORDER_PLACED"),
    "PENDING_APPROVAL": ("input-required", "PENDING_APPROVAL"),
    "SUBMITTING": ("working", "SUBMITTING"),
    "SUBMISSION_UNKNOWN": ("working", "SUBMISSION_UNKNOWN"),
    "REJECTED": ("failed", "REJECTED"),
    "EXPIRED": ("failed", "EXPIRED"),
    "FAILED": ("failed", "FAILED"),
    "CANCELLED": ("canceled", "CANCELLED"),
}


def build_agent_card(base_url: str) -> A2AAgentCard:
    """Build the public A2A Agent Card advertising Aimo Annos skills and Bearer auth."""
    clean_base = base_url.rstrip("/")
    return A2AAgentCard(
        name="Aimo Annos Purchasing Gateway",
        description=(
            "Diet-safe, policy-governed B2B foodservice procurement gateway for Valio Aimo "
            "with deterministic allergen/diet verification, live quotes, substitutions, and "
            "human-in-the-loop order approval."
        ),
        url=f"{clean_base}/api/a2a",
        version=settings.version,
        capabilities=A2AAgentCapabilities(
            streaming=False,
            pushNotifications=False,
            stateTransitionHistory=True,
        ),
        skills=[
            A2AAgentSkill(
                id="diet_safe_search",
                name="Diet-Safe Catalog Search",
                description=(
                    "Search Valio Aimo products filtered by Finnish menu and allergen codes "
                    "(L, VL, M, G, VEG, HEART) using deterministic Synkka rules."
                ),
                tags=["catalog", "diet", "allergens", "finland"],
                examples=["[L] 1,000 L milk for 2026-11-16", "[G] rye bread"],
            ),
            A2AAgentSkill(
                id="live_availability_quote",
                name="Live Availability & CO2 Quote",
                description=(
                    "Fetch live Valio Aimo pricing, cut-off times, delivery probability, "
                    "and CO2 basis into a locked 30-minute quote."
                ),
                tags=["pricing", "availability", "co2", "quote"],
                examples=["Quote 1000 x 464306 for 2026-11-16"],
            ),
            A2AAgentSkill(
                id="substitution_check",
                name="Diet-Safe Substitution Check",
                description=(
                    "Propose diet-safe, delivery-feasible, culinarily appropriate substitutes "
                    "with explicit storage and price trade-offs when stock is partial or late."
                ),
                tags=["substitutes", "partial-stock", "tradeoffs"],
                examples=["469363 quantity 1000 for 2026-11-16"],
            ),
            A2AAgentSkill(
                id="governed_order_placement",
                name="Policy-Governed Order Placement",
                description=(
                    "Place orders through Layer 1 deterministic spend, diet, and delivery limits; "
                    "auto-places within limits or pauses in input-required for human approval."
                ),
                tags=["orders", "policy", "approval", "idempotency"],
                examples=["Order 10 x 464306 for 2026-11-16"],
            ),
            A2AAgentSkill(
                id="preliminary_price_negotiation",
                name="Admin-Gated Volume Price Negotiation",
                description=(
                    "Negotiate preliminary prices for larger quantities and bundle orders after "
                    "Synkka diet, live stock, and cut-off checks when enabled for the company "
                    "in the Admin panel (/admin)."
                ),
                tags=["negotiation", "pricing", "quote", "a2a"],
                examples=["Negotiate 200 x 464306 + 50 x 464398 for 2026-11-16"],
            ),
        ],
        supported_matters=build_supported_matters(allow_price_negotiation=True),
        securitySchemes={
            "bearerAuth": A2ASecurityScheme(
                type="http",
                scheme="bearer",
                bearerFormat="JWT",
                description=(
                    f"Exchange agent_id + agent_secret via POST {clean_base}/api/agent-auth/token"
                ),
            )
        },
        security=[{"bearerAuth": []}],
    )


def build_supported_matters(
    *,
    allow_price_negotiation: bool = True,
    company_name: str | None = None,
) -> list[A2ASupportedMatter]:
    """Return the canonical procurement matters advertised on A2A and MCP session handshakes."""
    if allow_price_negotiation:
        if company_name:
            tendering_summary = (
                f"Enabled for {company_name} in the Admin panel (/admin): can negotiate prices "
                "for larger quantities and multi-line bundle orders after Synkka diet, live "
                "stock, and delivery cut-off verification."
            )
        else:
            tendering_summary = (
                "Negotiate company-gated prices for larger quantities and bundle orders after "
                "Synkka diet, live stock, and delivery cut-off verification (managed per company "
                "in Admin panel /admin)."
            )
    else:
        company_clause = f" for {company_name}" if company_name else ""
        tendering_summary = (
            f"Currently disabled{company_clause} (managed per company in Admin panel /admin): "
            "price negotiation for larger quantities is inactive."
        )

    return [
        A2ASupportedMatter(
            id="menu_planning",
            title="Menu Planning",
            summary=(
                "Plan diet-safe Nordic foodservice menus for N pax with Valio recipe pairings, "
                "live on-sale deals, CO2, and ready quote drafts."
            ),
            enabled=True,
            tools_or_skills=["plan_menu", "find_on_sale_products", "diet_safe_search"],
        ),
        A2ASupportedMatter(
            id="order_execution",
            title="Order Execution",
            summary=(
                "Lock 30-minute live quotes and execute SHA-256 content-hashed, policy-governed "
                "orders with 5-hour stock holds and human approval gates."
            ),
            enabled=True,
            tools_or_skills=[
                "create_quote",
                "place_order",
                "get_order",
                "live_availability_quote",
                "governed_order_placement",
            ],
        ),
        A2ASupportedMatter(
            id="preliminary_tendering",
            title="Preliminary Tendering",
            summary=tendering_summary,
            enabled=bool(allow_price_negotiation),
            tools_or_skills=["negotiate_quote", "preliminary_price_negotiation"],
        ),
        A2ASupportedMatter(
            id="exception_handling",
            title="Exception Handling",
            summary=(
                "Resolve partial stock, missed cut-offs, storage ambiguity, and diet substitutions "
                "via multi-turn clarification and ranked Good-Better-Best swaps."
            ),
            enabled=True,
            tools_or_skills=["optimize_quote", "substitution_check", "tasks/get", "tasks/cancel"],
        ),
    ]


def build_session_capabilities_payload(
    db: Session,
    principal: Principal,
) -> dict[str, Any]:
    """Build live company-scoped A2A/MCP session handshake payload."""
    company = db.get(Company, principal.company_id)
    company_name = company.name if company is not None else principal.company_id
    neg_settings = negotiation.get_negotiation_settings(db, principal.company_id)
    allow_neg = bool(neg_settings.allow_price_negotiation)
    matters = build_supported_matters(
        allow_price_negotiation=allow_neg,
        company_name=company_name,
    )
    enabled_titles = [m.title for m in matters if m.enabled]
    tendering_status = (
        f"Preliminary Tendering is enabled for {company_name} in the Admin panel (/admin) — "
        "I can negotiate prices for larger quantities and multi-line bundle orders."
        if allow_neg
        else (
            "Preliminary Tendering (price negotiation for larger quantities) is currently "
            f"disabled for {company_name} and is managed per company in the Admin panel (/admin)."
        )
    )
    greeting = (
        f"Connected to Aimo Annos Purchasing Gateway for {company_name}. "
        f"I can handle: {', '.join(enabled_titles)}. {tendering_status}"
    )
    return {
        "session_greeting": greeting,
        "company_id": principal.company_id,
        "company_name": company_name,
        "allow_price_negotiation": allow_neg,
        "supported_matters": [m.model_dump(mode="json") for m in matters],
        "protocol": "A2A/JSON-RPC-2.0",
    }


def _json_rpc_ok(rpc_id: str | int | None, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": rpc_id, "result": result}


def _json_rpc_error(
    rpc_id: str | int | None,
    *,
    code: int,
    message: str,
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    err_data = dict(data or {})
    err_data.setdefault("request_id", get_request_id())
    return {
        "jsonrpc": "2.0",
        "id": rpc_id,
        "error": {
            "code": code,
            "message": message,
            "data": err_data,
        },
    }


def _storage_label(prod: ProductOut) -> str:
    if prod.temperature_condition in (1, 2, 3, 4):
        return "chilled"
    if prod.temperature_condition == 8 or "uht" in (prod.name or "").lower():
        return "uht"
    if prod.temperature_condition == 5:
        return "frozen"
    return "ambient"


def _make_artifact(
    *,
    name: str,
    description: str,
    data: dict[str, Any],
) -> dict[str, Any]:
    return {
        "artifactId": new_id("art_"),
        "name": name,
        "description": description,
        "parts": [{"kind": "data", "data": data}],
    }


def _upsert_artifact(
    artifacts: list[dict[str, Any]],
    *,
    name: str,
    description: str,
    data: dict[str, Any],
) -> list[dict[str, Any]]:
    updated = [dict(a) for a in artifacts if a.get("name") != name]
    updated.append(_make_artifact(name=name, description=description, data=data))
    return updated


def _append_history(
    context: dict[str, Any],
    *,
    state: A2ATaskState,
    role: Literal["agent", "gateway"],
    message: str,
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    new_ctx = dict(context)
    history = [dict(item) for item in (new_ctx.get("history") or [])]
    entry: dict[str, Any] = {
        "at": clock.now_iso(),
        "state": state,
        "role": role,
        "message": message,
    }
    if data is not None:
        entry["data"] = data
    history.append(entry)
    new_ctx["history"] = history
    return new_ctx


def _task_to_rpc_result(task: A2ATask) -> dict[str, Any]:
    ctx = task.context or {}
    history = list(ctx.get("history") or [])
    last_gw = next(
        (h for h in reversed(history) if h.get("role") == "gateway"),
        history[-1] if history else None,
    )
    msg_parts: list[dict[str, Any]] = []
    if last_gw is not None:
        msg_parts.append({"kind": "text", "text": str(last_gw.get("message") or "")})
        if isinstance(last_gw.get("data"), dict):
            msg_parts.append({"kind": "data", "data": last_gw["data"]})
    else:
        msg_parts.append({"kind": "text", "text": f"Task {task.id} is {task.state}"})

    supported_matters = ctx.get("supported_matters") or [
        m.model_dump(mode="json") for m in build_supported_matters(allow_price_negotiation=True)
    ]
    return {
        "id": task.id,
        "kind": "task",
        "state": task.state,
        "status": {
            "state": task.state,
            "timestamp": clock.format_helsinki_iso(task.updated_at),
            "reason": ctx.get("reason"),
            "message": {
                "role": "agent",
                "parts": msg_parts,
            },
        },
        "artifacts": list(task.artifacts or []),
        "history": history,
        "linked_order_id": task.linked_order_id,
        "metadata": {"supported_matters": supported_matters},
        "created_at": clock.format_helsinki_iso(task.created_at),
        "updated_at": clock.format_helsinki_iso(task.updated_at),
    }


def _resolve_task_principal(task: A2ATask) -> Principal:
    ctx = task.context or {}
    raw_principal = ctx.get("principal")
    if isinstance(raw_principal, dict):
        try:
            return Principal.model_validate(raw_principal)
        except Exception:
            pass
    kind = cast(
        Literal["user", "agent"],
        task.principal_kind if task.principal_kind in ("user", "agent") else "agent",
    )
    return Principal(
        kind=kind,
        id=task.principal_id,
        name=task.principal_id,
        company_id=task.company_id,
        delegated_by=None,
    )


def _task_to_trace_out(task: A2ATask) -> A2ATaskTraceOut:
    ctx = task.context or {}
    history_out = [A2ATraceEntry.model_validate(item) for item in (ctx.get("history") or [])]
    state = cast(
        A2ATaskState,
        task.state
        if task.state in ("working", "input-required", "completed", "failed", "canceled")
        else "failed",
    )
    return A2ATaskTraceOut(
        id=task.id,
        state=state,
        principal=_resolve_task_principal(task),
        created_at=task.created_at,
        updated_at=task.updated_at,
        linked_order_id=task.linked_order_id,
        history=history_out,
    )


def _load_company_task(db: Session, task_id: str, principal: Principal) -> A2ATask:
    task = db.scalar(
        select(A2ATask).where(
            A2ATask.id == task_id,
            A2ATask.company_id == principal.company_id,
        )
    )
    if task is None:
        raise AppError("NOT_FOUND", 404, "A2A task not found")
    if principal.kind == "agent" and (
        task.principal_kind != "agent" or task.principal_id != principal.id
    ):
        raise AppError("NOT_FOUND", 404, "A2A task not found")
    return task


def sync_task_with_linked_order(
    db: Session,
    task: A2ATask,
    *,
    run_expiry: bool = True,
) -> bool:
    """Sync an A2ATask's state and order artifact from its linked Order."""
    if not task.linked_order_id:
        return False

    if run_expiry:
        orders.expire_pending_orders(db, company_id=task.company_id)

    order = db.scalar(
        select(Order)
        .options(selectinload(Order.lines), selectinload(Order.events))
        .where(
            Order.id == task.linked_order_id,
            Order.company_id == task.company_id,
        )
    )
    if order is None:
        return False

    order_out = orders.order_to_out(order)
    order_json = order_out.model_dump(mode="json")
    target_state, reason = _ORDER_TO_TASK_STATE.get(order.status, ("working", order.status))

    existing_order_art = next(
        (a for a in (task.artifacts or []) if a.get("name") == "order"),
        None,
    )
    existing_status = None
    existing_ref = None
    if existing_order_art and existing_order_art.get("parts"):
        art_data = existing_order_art["parts"][0].get("data") or {}
        existing_status = art_data.get("status")
        existing_ref = art_data.get("gateway_reference")

    changed = False
    if existing_status != order.status or existing_ref != order.gateway_reference:
        task.artifacts = _upsert_artifact(
            list(task.artifacts or []),
            name="order",
            description=f"Order {order.id} ({order.status})",
            data=order_json,
        )
        changed = True

    if task.state != target_state:
        now = clock.now()
        task.state = target_state
        task.updated_at = now

        msg_map = {
            "completed": (
                f"Linked order {order.id} placed ({order.gateway_reference or 'confirmed'})"
            ),
            "input-required": (
                f"Linked order {order.id} awaiting human approval "
                f"(content_hash={order.content_hash})"
            ),
            "working": f"Linked order {order.id} is {order.status}",
            "failed": f"Linked order {order.id} ended in status {order.status}",
            "canceled": f"Linked order {order.id} was cancelled",
        }
        sync_data: dict[str, Any] = {
            "reason": reason,
            "linked_order_id": order.id,
            "order_id": order.id,
            "order_status": order.status,
            "content_hash": order.content_hash,
            "gateway_reference": order.gateway_reference,
            "policy": order_json.get("policy"),
            "rule_ids": [r.rule_id for r in order_out.policy.rules],
        }
        if target_state == "completed":
            sync_data["order"] = order_json

        new_ctx = _append_history(
            task.context or {},
            state=target_state,
            role="gateway",
            message=msg_map[target_state],
            data=sync_data,
        )
        new_ctx["reason"] = reason
        new_ctx["order_status"] = order.status
        task.context = new_ctx
        changed = True

    return changed


def _build_typed_option(
    prod: ProductOut,
    line: QuoteLine,
    *,
    option_id: str,
    action: str,
    quantity: float,
    tradeoffs: list[str] | None = None,
) -> dict[str, Any]:
    opt: dict[str, Any] = {
        "option_id": option_id,
        "action": action,
        "product_id": prod.id,
        "name": prod.name,
        "storage": _storage_label(prod),
        "temperature_condition": prod.temperature_condition,
        "sales_unit": line.sales_unit,
        "quantity": quantity,
        "unit_price_eur": line.unit_price_eur,
        "line_total_eur": line.line_total_eur,
        "delivery_status": line.delivery_status,
        "available_amount": line.available_amount,
        "co2_kg": line.co2_kg,
        "co2_basis": line.co2_basis,
        "flags": list(line.flags),
    }
    if tradeoffs:
        opt["tradeoffs"] = tradeoffs
    return opt


def _fail_task(
    db: Session,
    *,
    task: A2ATask,
    principal: Principal,
    code: str,
    message: str,
    reason: str,
    policy: dict[str, Any] | None = None,
    rule_ids: list[str] | None = None,
    extra_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    now = clock.now()
    task.state = "failed"
    task.updated_at = now
    error_payload: dict[str, Any] = {
        "code": code,
        "message": message,
        "reason": reason,
    }
    if policy is not None:
        error_payload["policy"] = policy
    if rule_ids is not None:
        error_payload["rule_ids"] = rule_ids
    if extra_data:
        error_payload.update(extra_data)

    task.artifacts = _upsert_artifact(
        list(task.artifacts or []),
        name="error",
        description=f"{code}: {message}",
        data=error_payload,
    )
    new_ctx = _append_history(
        task.context or {},
        state="failed",
        role="gateway",
        message=message,
        data=error_payload,
    )
    new_ctx["reason"] = reason
    new_ctx["error"] = error_payload
    task.context = new_ctx

    record_audit(
        db,
        company_id=principal.company_id,
        actor=principal,
        action="a2a.message_send",
        entity_type="a2a_task",
        entity_id=task.id,
        outcome="error",
        details={
            "state": "failed",
            "reason": reason,
            "code": code,
            "rule_ids": rule_ids or [],
        },
    )
    db.commit()
    db.refresh(task)
    return _task_to_rpc_result(task)


def _transition_clarification(
    db: Session,
    *,
    task: A2ATask,
    principal: Principal,
    message: str,
    missing_slots: list[str],
    options: list[dict[str, Any]],
) -> dict[str, Any]:
    now = clock.now()
    task.state = "input-required"
    task.updated_at = now

    clar_data: dict[str, Any] = {
        "reason": "CLARIFICATION_REQUIRED",
        "missing_slots": missing_slots,
        "options": options,
    }
    task.artifacts = _upsert_artifact(
        list(task.artifacts or []),
        name="options",
        description="Structured clarification options",
        data={"missing_slots": missing_slots, "options": options},
    )
    new_ctx = _append_history(
        task.context or {},
        state="input-required",
        role="gateway",
        message=message,
        data=clar_data,
    )
    new_ctx["reason"] = "CLARIFICATION_REQUIRED"
    new_ctx["candidates"] = options
    task.context = new_ctx

    record_audit(
        db,
        company_id=principal.company_id,
        actor=principal,
        action="a2a.message_send",
        entity_type="a2a_task",
        entity_id=task.id,
        outcome="ok",
        details={
            "state": "input-required",
            "reason": "CLARIFICATION_REQUIRED",
            "option_count": len(options),
        },
    )
    db.commit()
    db.refresh(task)
    return _task_to_rpc_result(task)


def _quote_and_place_order(
    db: Session,
    *,
    task: A2ATask,
    aimo_client: AimoClient,
    gateway: OrderGateway,
    principal: Principal,
    product_id: str,
    quantity: float,
    requested_delivery_date: str,
    diet: str | None,
    note: str | None,
    turn_number: int,
    recipe_id: str | None = None,
    target_pax: int | None = None,
) -> dict[str, Any]:
    try:
        quote_out: QuoteOut = quotes.create_quote(
            db,
            aimo_client,
            principal,
            QuoteCreateRequest(
                requested_delivery_date=requested_delivery_date,
                lines=[QuoteLineInput(product_id=product_id, quantity=quantity)],
                diet=diet,
                recipe_id=recipe_id,
                target_pax=target_pax,
            ),
            source_context={"a2a_task_id": task.id},
        )
    except AppError as exc:
        return _fail_task(
            db,
            task=task,
            principal=principal,
            code=exc.code,
            message=exc.message,
            reason=exc.code,
            extra_data={"details": exc.details},
        )

    quote_json = quote_out.model_dump(mode="json")
    task.artifacts = _upsert_artifact(
        list(task.artifacts or []),
        name="quote",
        description=f"Locked quote {quote_out.id}",
        data=quote_json,
    )
    new_ctx = dict(task.context or {})
    new_ctx["quote_id"] = quote_out.id
    task.context = new_ctx

    idem_key = f"a2a_{task.id}_turn_{turn_number}"
    try:
        _, body = orders.place_order(
            db,
            aimo_client,
            principal,
            OrderCreateRequest(
                quote_id=quote_out.id,
                note=note,
                diet=diet,
            ),
            raw_idempotency_key=idem_key,
            gateway=gateway,
            source_context={
                "a2a_task_id": task.id,
                "upstream_injection_screened": bool(
                    (task.context or {}).get("turn_injection_screened")
                ),
            },
        )
    except AppError as exc:
        if exc.code == "POLICY_BLOCKED":
            rules = exc.details.get("rules") or []
            rule_ids = [
                str(r.get("rule_id")) for r in rules if isinstance(r, dict) and r.get("rule_id")
            ]
            reason = "R-HARD-DIET" if "R-HARD-DIET" in rule_ids else "POLICY_BLOCKED"
            return _fail_task(
                db,
                task=task,
                principal=principal,
                code="POLICY_BLOCKED",
                message=exc.message,
                reason=reason,
                policy=exc.details,
                rule_ids=rule_ids,
                extra_data={"quote_id": quote_out.id},
            )
        return _fail_task(
            db,
            task=task,
            principal=principal,
            code=exc.code,
            message=exc.message,
            reason=exc.code,
            extra_data={"details": exc.details, "quote_id": quote_out.id},
        )

    order_out = OrderOut.model_validate(body)
    order_json = order_out.model_dump(mode="json")
    task.linked_order_id = order_out.id
    task.artifacts = _upsert_artifact(
        list(task.artifacts or []),
        name="order",
        description=f"Order {order_out.id} ({order_out.status})",
        data=order_json,
    )

    target_state, reason = _ORDER_TO_TASK_STATE.get(order_out.status, ("working", order_out.status))
    now = clock.now()
    task.state = target_state
    task.updated_at = now

    rule_ids = [r.rule_id for r in order_out.policy.rules]
    fired_rules = [r.model_dump(mode="json") for r in order_out.policy.rules]

    if target_state == "input-required":
        msg = (
            f"Order {order_out.id} requires human approval "
            f"(EUR {order_out.total_excl_vat_eur:.2f} excl. VAT; rules: {', '.join(rule_ids)})"
        )
        data: dict[str, Any] = {
            "reason": "PENDING_APPROVAL",
            "linked_order_id": order_out.id,
            "order_id": order_out.id,
            "quote_id": quote_out.id,
            "content_hash": order_out.content_hash,
            "approval_expires_at": (
                clock.format_helsinki_iso(order_out.approval_expires_at)
                if order_out.approval_expires_at
                else None
            ),
            "total_excl_vat_eur": order_out.total_excl_vat_eur,
            "policy": order_json["policy"],
            "fired_rules": fired_rules,
            "rule_ids": rule_ids,
        }
    elif target_state == "completed":
        msg = (
            f"Order {order_out.id} placed automatically "
            f"({order_out.gateway_reference or 'PLACED'}, EUR {order_out.total_excl_vat_eur:.2f})"
        )
        data = {
            "reason": "ORDER_PLACED",
            "linked_order_id": order_out.id,
            "order_id": order_out.id,
            "quote_id": quote_out.id,
            "gateway_reference": order_out.gateway_reference,
            "quote": quote_json,
            "order": order_json,
        }
    elif target_state == "working":
        msg = f"Order {order_out.id} is {order_out.status}"
        data = {
            "reason": reason,
            "linked_order_id": order_out.id,
            "order_id": order_out.id,
            "quote_id": quote_out.id,
            "order_status": order_out.status,
        }
    else:
        msg = f"Order {order_out.id} ended in status {order_out.status}"
        data = {
            "reason": reason,
            "linked_order_id": order_out.id,
            "order_id": order_out.id,
            "quote_id": quote_out.id,
            "order_status": order_out.status,
            "policy": order_json["policy"],
            "rule_ids": rule_ids,
        }

    ctx = _append_history(
        task.context or {},
        state=target_state,
        role="gateway",
        message=msg,
        data=data,
    )
    ctx["reason"] = reason
    ctx["order_id"] = order_out.id
    ctx["order_status"] = order_out.status
    task.context = ctx

    now_iso = clock.format_helsinki_iso(clock.now())
    record_audit(
        db,
        company_id=principal.company_id,
        actor=principal,
        action="a2a.message_send",
        entity_type="a2a_task",
        entity_id=task.id,
        outcome="error" if target_state == "failed" else "ok",
        details={
            "a2a_task_id": task.id,
            "state": target_state,
            "reason": reason,
            "quote_id": quote_out.id,
            "order_id": order_out.id,
            "order_status": order_out.status,
            "rule_ids": rule_ids,
            "reasoning_steps": [
                {
                    "step": 1,
                    "stage": "a2a_quote_and_order",
                    "summary": (
                        f"A2A task {task.id} quoted {quote_out.id} and submitted order "
                        f"{order_out.id} -> {order_out.status} (state: {target_state})"
                    ),
                    "actor_kind": principal.kind,
                    "at": now_iso,
                    "details": {
                        "a2a_task_id": task.id,
                        "quote_id": quote_out.id,
                        "order_id": order_out.id,
                        "rule_ids": rule_ids,
                    },
                }
            ],
        },
    )
    db.commit()
    db.refresh(task)
    return _task_to_rpc_result(task)


def _match_followup_candidate(
    text: str,
    data_payload: dict[str, Any],
    candidates: list[dict[str, Any]],
) -> tuple[str | None, float | None, str | None]:
    action = str(data_payload.get("action") or "").strip().lower() or None
    if data_payload.get("product_id"):
        pid = str(data_payload["product_id"]).strip()
        qty = data_payload.get("quantity")
        return pid, (float(qty) if qty is not None else None), action

    if action == "split":
        for cand in candidates:
            if cand.get("action") == "split" and cand.get("product_id"):
                return str(cand["product_id"]), float(cand["quantity"]), "split"

    ids_in_text = _PRODUCT_ID_RE.findall(text)
    if ids_in_text:
        pid = ids_in_text[0]
        for cand in candidates:
            if cand.get("product_id") == pid:
                return pid, float(cand["quantity"]), str(cand.get("action") or "select_option")
        return pid, None, action

    lowered = text.lower()
    if "split" in lowered:
        for cand in candidates:
            if cand.get("action") == "split" and cand.get("product_id"):
                return str(cand["product_id"]), float(cand["quantity"]), "split"

    if any(tok in lowered for tok in ("chilled", "fresh", "cold", "kylmä", "tuore")):
        for cand in candidates:
            if cand.get("storage") == "chilled":
                return str(cand["product_id"]), float(cand["quantity"]), "select_option"

    if any(tok in lowered for tok in ("uht", "ambient", "huoneenlämpö", "iskukuumennettu")):
        for cand in candidates:
            if cand.get("storage") == "uht":
                return str(cand["product_id"]), float(cand["quantity"]), "select_option"

    return None, None, action


def _extract_message_parts(
    params: dict[str, Any],
    rpc_id: str | int | None,
) -> tuple[str, dict[str, Any], str | None] | dict[str, Any]:
    msg = params.get("message")
    if not isinstance(msg, dict):
        return _json_rpc_error(
            rpc_id,
            code=-32602,
            message="Invalid params: 'message' object is required",
        )
    parts = msg.get("parts")
    if not isinstance(parts, list) or not parts:
        return _json_rpc_error(
            rpc_id,
            code=-32602,
            message="Invalid params: 'message.parts' must be a non-empty list",
        )

    texts: list[str] = []
    data_payload: dict[str, Any] = {}
    for part in parts:
        if not isinstance(part, dict):
            continue
        kind = str(part.get("kind") or part.get("type") or "").lower()
        if kind == "text" or ("text" in part and not kind):
            txt = str(part.get("text") or "").strip()
            if txt:
                texts.append(txt)
        elif kind == "data" or ("data" in part and not kind):
            raw_data = part.get("data")
            if isinstance(raw_data, dict):
                data_payload.update(raw_data)

    if not texts and not data_payload:
        return _json_rpc_error(
            rpc_id,
            code=-32602,
            message="Invalid params: at least one text or data part is required",
        )

    raw_task_id = (
        params.get("taskId")
        or params.get("task_id")
        or params.get("id")
        or msg.get("taskId")
        or msg.get("task_id")
    )
    task_id = (
        str(raw_task_id).strip() if raw_task_id is not None and str(raw_task_id).strip() else None
    )
    return "\n".join(texts), data_payload, task_id


def _handle_message_send(
    db: Session,
    *,
    aimo_client: AimoClient,
    gateway: OrderGateway,
    decision_provider: DecisionProvider,
    principal: Principal,
    rpc_id: str | int | None,
    params: dict[str, Any],
) -> dict[str, Any]:
    extracted = _extract_message_parts(params, rpc_id)
    if isinstance(extracted, dict):
        return extracted
    combined_text, data_payload, task_id = extracted

    now = clock.now()
    if task_id is not None:
        task = _load_company_task(db, task_id, principal)
        sync_task_with_linked_order(db, task)
        if task.state in _TERMINAL_STATES:
            raise AppError(
                "INVALID_STATE",
                409,
                f"A2A task {task.id} is already in terminal state '{task.state}'",
                details={"task_id": task.id, "state": task.state},
            )
    else:
        task = A2ATask(
            id=new_id("tsk_"),
            company_id=principal.company_id,
            principal_kind=principal.kind,
            principal_id=principal.id,
            state="working",
            context={
                "principal": principal.model_dump(mode="json"),
                "history": [],
                "request": {},
                "candidates": [],
                "turn_count": 0,
            },
            artifacts=[],
            linked_order_id=None,
            created_at=now,
            updated_at=now,
        )
        db.add(task)
        db.flush()

    session_caps = build_session_capabilities_payload(db, principal)
    ctx = dict(task.context or {})
    ctx["supported_matters"] = session_caps["supported_matters"]
    turn_number = int(ctx.get("turn_count") or 0) + 1
    ctx["turn_count"] = turn_number
    agent_summary = combined_text or (f"Structured action: {data_payload.get('action') or 'data'}")
    ctx = _append_history(
        ctx,
        state=cast(A2ATaskState, task.state),
        role="agent",
        message=agent_summary,
        data=data_payload or None,
    )
    task.context = ctx
    task.updated_at = now

    # Layer 2 prompt-injection screening (audits if triggered; never overrides Layer 1).
    note_text = str(data_payload.get("note") or "")
    just_text = str(data_payload.get("justification") or "")
    screen_input = "\n".join(x for x in (combined_text, note_text, just_text) if x).strip()
    inj = is_prompt_injection(screen_input, provider=decision_provider)
    ctx["turn_injection_screened"] = bool(inj.answer)
    task.context = ctx
    if inj.answer:
        record_audit(
            db,
            company_id=principal.company_id,
            actor=principal,
            action="a2a.injection_screen",
            entity_type="a2a_task",
            entity_id=task.id,
            outcome="ok",
            details={
                "a2a_task_id": task.id,
                "flagged": True,
                "reason": inj.reason,
                "message_preview": screen_input[:200],
                "reasoning_steps": [
                    {
                        "step": 1,
                        "stage": "layer2_injection_screen",
                        "summary": (
                            f"A2A prompt injection screen flagged suspicious input: {inj.reason}"
                        ),
                        "actor_kind": "system",
                        "at": clock.format_helsinki_iso(now),
                        "details": {"flagged": True, "reason": inj.reason},
                    }
                ],
            },
        )
        db.flush()
        purchasing_guard.check_repeated_injection_and_quarantine(
            db,
            principal=principal,
            reason=inj.reason,
        )

    if data_payload.get("action") == "approve" and principal.kind == "agent":
        record_audit(
            db,
            company_id=principal.company_id,
            actor=principal,
            action="a2a.message_send",
            entity_type="a2a_task",
            entity_id=task.id,
            outcome="error",
            details={"reason": "AGENT_APPROVAL_FORBIDDEN"},
        )
        db.commit()
        raise AppError(
            "FORBIDDEN",
            403,
            "Agents cannot approve orders; human approval via /api/orders/{id}/approve is required",
        )

    if data_payload.get("action") in ("session_connect", "capabilities") or (
        not inj.answer
        and not _PRODUCT_ID_RE.search(combined_text)
        and not re.search(r"\b\d+\b", combined_text)
        and bool(_CAPABILITY_GREETING_RE.search(combined_text))
    ):
        task.state = "completed"
        task.updated_at = clock.now()
        cap_data = {
            "reason": "SESSION_CAPABILITIES",
            **session_caps,
        }
        task.artifacts = _upsert_artifact(
            list(task.artifacts or []),
            name="supported_matters",
            description="Supported procurement matters for this Aimo Annos A2A session",
            data=session_caps,
        )
        ctx_cap = _append_history(
            task.context or {},
            state="completed",
            role="gateway",
            message=session_caps["session_greeting"],
            data=cap_data,
        )
        ctx_cap["reason"] = "SESSION_CAPABILITIES"
        task.context = ctx_cap
        record_audit(
            db,
            company_id=principal.company_id,
            actor=principal,
            action="a2a.session_connect",
            entity_type="a2a_task",
            entity_id=task.id,
            outcome="ok",
            details={
                "method": "message/send",
                "allow_price_negotiation": session_caps["allow_price_negotiation"],
                "supported_matters": [
                    m["id"] for m in session_caps["supported_matters"] if m["enabled"]
                ],
            },
        )
        db.commit()
        db.refresh(task)
        return _json_rpc_ok(rpc_id, _task_to_rpc_result(task))

    if data_payload.get("action") == "negotiate_quote" or data_payload.get("negotiate_price"):
        from pydantic import ValidationError

        from app.schemas import QuoteNegotiateRequest
        from app.services.negotiation import negotiate_quote

        try:
            raw_lines = data_payload.get("lines") or []
            if not isinstance(raw_lines, list):
                raise ValueError("lines must be a list of {product_id, quantity} objects")
            req_lines: list[QuoteLineInput] = []
            for ln in raw_lines:
                if not isinstance(ln, dict):
                    raise ValueError("each line must be an object with product_id and quantity")
                if "product_id" not in ln or "quantity" not in ln:
                    raise ValueError("each line requires product_id and quantity")
                req_lines.append(
                    QuoteLineInput(
                        product_id=str(ln["product_id"]),
                        quantity=float(ln["quantity"]),
                    )
                )
            if (
                not req_lines
                and data_payload.get("product_id") is not None
                and data_payload.get("quantity") is not None
            ):
                req_lines = [
                    QuoteLineInput(
                        product_id=str(data_payload["product_id"]),
                        quantity=float(data_payload["quantity"]),
                    )
                ]
            neg_req = QuoteNegotiateRequest(
                requested_delivery_date=str(
                    data_payload.get("requested_delivery_date") or default_delivery_date()
                ),
                lines=req_lines,
                diet=(str(data_payload["diet"]) if data_payload.get("diet") else None),
                target_discount_pct=(
                    float(data_payload["target_discount_pct"])
                    if data_payload.get("target_discount_pct") is not None
                    else None
                ),
                justification=(
                    str(data_payload["justification"])
                    if data_payload.get("justification")
                    else (combined_text or None)
                ),
            )
        except (ValidationError, TypeError, ValueError) as exc:
            return _json_rpc_ok(
                rpc_id,
                _fail_task(
                    db,
                    task=task,
                    principal=principal,
                    code="VALIDATION_ERROR",
                    message=f"Invalid negotiation request: {exc}",
                    reason="VALIDATION_ERROR",
                ),
            )

        try:
            neg_res = negotiate_quote(
                db=db,
                aimo_client=aimo_client,
                principal=principal,
                req=neg_req,
                existing_a2a_task=task,
            )
        except AppError as exc:
            if exc.code in {"AGENT_DISABLED", "AGENT_QUARANTINED"}:
                raise
            return _json_rpc_ok(
                rpc_id,
                _fail_task(
                    db,
                    task=task,
                    principal=principal,
                    code=exc.code,
                    message=exc.message,
                    reason=exc.code,
                    extra_data={"details": exc.details} if exc.details else None,
                ),
            )

        task.state = "completed"
        task.updated_at = clock.now()
        ctx_neg = _append_history(
            task.context or {},
            state="completed",
            role="gateway",
            message=neg_res.rounds[-1].message,
            data={
                "reason": neg_res.status,
                "quote_id": neg_res.quote.id,
                "savings_eur": neg_res.savings_eur,
                "savings_pct": neg_res.savings_pct,
                "negotiation": neg_res.model_dump(mode="json"),
            },
        )
        ctx_neg["reason"] = neg_res.status
        task.context = ctx_neg
        db.commit()
        db.refresh(task)
        return _json_rpc_ok(rpc_id, _task_to_rpc_result(task))

    if task.linked_order_id:
        record_audit(
            db,
            company_id=principal.company_id,
            actor=principal,
            action="a2a.message_send",
            entity_type="a2a_task",
            entity_id=task.id,
            outcome="ok",
            details={
                "state": task.state,
                "linked_order_id": task.linked_order_id,
            },
        )
        db.commit()
        db.refresh(task)
        return _json_rpc_ok(rpc_id, _task_to_rpc_result(task))

    parsed = parse_prep_list(combined_text) if combined_text else None
    first_item = parsed.items[0] if (parsed and parsed.items) else None
    prior_req = dict(ctx.get("request") or {})
    prior_candidates = list(ctx.get("candidates") or [])

    raw_diet = data_payload.get("diet")
    if isinstance(raw_diet, list):
        diet: str | None = ",".join(str(x).strip() for x in raw_diet if str(x).strip()) or None
    elif isinstance(raw_diet, str) and raw_diet.strip():
        diet = raw_diet.strip()
    elif parsed and parsed.global_diets:
        diet = ",".join(parsed.global_diets)
    else:
        diet = prior_req.get("diet")

    if diet:
        try:
            catalog.resolve_diet_keys(diet, None)
        except AppError as exc:
            if exc.code in {"AGENT_DISABLED", "AGENT_QUARANTINED"}:
                raise
            return _json_rpc_ok(
                rpc_id,
                _fail_task(
                    db,
                    task=task,
                    principal=principal,
                    code=exc.code,
                    message=exc.message,
                    reason="VALIDATION_ERROR",
                    extra_data={"details": exc.details},
                ),
            )

    delivery_date = str(
        data_payload.get("requested_delivery_date")
        or (parsed.requested_delivery_date if parsed else None)
        or prior_req.get("requested_delivery_date")
        or default_delivery_date()
    ).strip()

    matched_pid, matched_qty, action = _match_followup_candidate(
        combined_text, data_payload, prior_candidates
    )
    if matched_pid is None and first_item and first_item.product_ids:
        matched_pid = first_item.product_ids[0]

    raw_qty = (
        matched_qty
        if matched_qty is not None
        else (
            data_payload.get("quantity")
            if data_payload.get("quantity") is not None
            else (
                first_item.quantity
                if (first_item and first_item.quantity is not None)
                else prior_req.get("quantity")
            )
        )
    )
    try:
        quantity = float(raw_qty) if raw_qty is not None else 1.0
        if quantity <= 0:
            raise ValueError("quantity must be > 0")
    except (TypeError, ValueError):
        return _json_rpc_ok(
            rpc_id,
            _fail_task(
                db,
                task=task,
                principal=principal,
                code="VALIDATION_ERROR",
                message="Quantity must be greater than 0",
                reason="VALIDATION_ERROR",
            ),
        )

    query = str(
        data_payload.get("query")
        or data_payload.get("q")
        or (first_item.query if first_item else "")
        or prior_req.get("query")
        or ""
    ).strip()
    note = str(data_payload.get("note") or prior_req.get("note") or "").strip() or None
    raw_recipe_id = data_payload.get("recipe_id") or prior_req.get("recipe_id")
    recipe_id = str(raw_recipe_id).strip() if raw_recipe_id else None
    raw_target_pax = data_payload.get("target_pax") or prior_req.get("target_pax")
    target_pax: int | None = None
    if raw_target_pax is not None:
        try:
            target_pax = int(raw_target_pax)
        except (TypeError, ValueError):
            target_pax = None

    ctx["request"] = {
        "product_id": matched_pid,
        "query": query,
        "quantity": quantity,
        "requested_delivery_date": delivery_date,
        "diet": diet,
        "note": note,
        "recipe_id": recipe_id,
        "target_pax": target_pax,
    }
    task.context = ctx

    # Case 1: Explicit product ID (from initial request or clarification follow-up)
    if matched_pid is not None:
        prod = db.get(Product, matched_pid)
        if prod is None:
            return _json_rpc_ok(
                rpc_id,
                _fail_task(
                    db,
                    task=task,
                    principal=principal,
                    code="NOT_FOUND",
                    message=f"Product {matched_pid} not found",
                    reason="NOT_FOUND",
                    extra_data={"product_id": matched_pid},
                ),
            )

        # Initial explicit product request (not yet clarified): check PARTIAL / delivery.
        if action not in ("split", "select_option", "switch_substitute"):
            live_lines = check_products_live(
                db=db,
                aimo_client=aimo_client,
                product_ids=[matched_pid],
                quantities={matched_pid: quantity},
                requested_delivery_date=delivery_date,
                customer_id=principal.company_id,
            )
            line = live_lines[0]
            if (
                line.delivery_status == "NOT_DELIVERABLE"
                or "NO_PRICE" in line.flags
                or "SALES_BLOCKED" in line.flags
            ):
                return _json_rpc_ok(
                    rpc_id,
                    _fail_task(
                        db,
                        task=task,
                        principal=principal,
                        code="POLICY_BLOCKED",
                        message=f"Product {matched_pid} is not deliverable for {delivery_date}",
                        reason="R-NOT-DELIVERABLE",
                        policy={
                            "outcome": "BLOCKED",
                            "rules": [
                                {
                                    "rule_id": "R-NOT-DELIVERABLE",
                                    "outcome": "BLOCKED",
                                    "message": (
                                        f"Product {matched_pid} cannot be delivered on "
                                        f"{delivery_date}"
                                    ),
                                }
                            ],
                        },
                        rule_ids=["R-NOT-DELIVERABLE"],
                    ),
                )

            # Check hard diet before offering partial split options.
            if diet:
                diet_keys = catalog.resolve_diet_keys(diet, None)
                passed, reasons = catalog.product_passes_diets(prod, diet_keys)
                if not passed:
                    return _json_rpc_ok(
                        rpc_id,
                        _quote_and_place_order(
                            db,
                            task=task,
                            aimo_client=aimo_client,
                            gateway=gateway,
                            principal=principal,
                            product_id=matched_pid,
                            quantity=quantity,
                            requested_delivery_date=delivery_date,
                            diet=diet,
                            note=note,
                            turn_number=turn_number,
                        ),
                    )

            clar_res, _ = needs_clarification(
                combined_text or matched_pid,
                [],
                provider=decision_provider,
                checked_lines=live_lines,
            )
            if clar_res.answer and "PARTIAL" in line.flags and line.available_amount is not None:
                prod_out = catalog.product_to_out(prod)
                split_qty = float(line.available_amount)
                split_line = check_products_live(
                    db=db,
                    aimo_client=aimo_client,
                    product_ids=[matched_pid],
                    quantities={matched_pid: split_qty},
                    requested_delivery_date=delivery_date,
                    customer_id=principal.company_id,
                )[0]
                options = [
                    _build_typed_option(
                        prod_out,
                        split_line,
                        option_id="split",
                        action="split",
                        quantity=split_qty,
                        tradeoffs=[
                            (
                                f"Partial fill: {split_qty:g} of {quantity:g} "
                                f"{line.sales_unit} available"
                            )
                        ],
                    )
                ]
                sub_resp = substitutes.find_substitutes(
                    db=db,
                    aimo_client=aimo_client,
                    product_id=matched_pid,
                    quantity=quantity,
                    requested_delivery_date=delivery_date,
                    diet=diet,
                    customer_id=principal.company_id,
                    decision_provider=decision_provider,
                )
                for sub in sub_resp.items[:3]:
                    options.append(
                        _build_typed_option(
                            sub.product,
                            sub.quote_line,
                            option_id=f"sub_{sub.product.id}",
                            action="select_option",
                            quantity=quantity,
                            tradeoffs=sub.tradeoffs,
                        )
                    )
                return _json_rpc_ok(
                    rpc_id,
                    _transition_clarification(
                        db,
                        task=task,
                        principal=principal,
                        message=clar_res.reason,
                        missing_slots=clar_res.missing_slots,
                        options=options,
                    ),
                )

        return _json_rpc_ok(
            rpc_id,
            _quote_and_place_order(
                db,
                task=task,
                aimo_client=aimo_client,
                gateway=gateway,
                principal=principal,
                product_id=matched_pid,
                quantity=quantity,
                requested_delivery_date=delivery_date,
                diet=diet,
                note=note,
                turn_number=turn_number,
                recipe_id=recipe_id,
                target_pax=target_pax,
            ),
        )

    # Case 2: Natural-language / structured search query without an explicit product ID
    if not query:
        if inj.answer:
            return _json_rpc_ok(
                rpc_id,
                _fail_task(
                    db,
                    task=task,
                    principal=principal,
                    code="POLICY_BLOCKED",
                    message=(
                        "Request blocked: policy-override instructions detected "
                        "and no valid product specified"
                    ),
                    reason="PROMPT_INJECTION_BLOCKED",
                    policy={
                        "outcome": "BLOCKED",
                        "rules": [
                            {
                                "rule_id": "R-PROMPT-INJECTION",
                                "outcome": "BLOCKED",
                                "message": inj.reason,
                            }
                        ],
                    },
                    rule_ids=["R-PROMPT-INJECTION"],
                ),
            )
        return _json_rpc_ok(
            rpc_id,
            _fail_task(
                db,
                task=task,
                principal=principal,
                code="VALIDATION_ERROR",
                message="Could not determine a product or search query from the message",
                reason="VALIDATION_ERROR",
            ),
        )

    items, _total = catalog.search_products(db, q=query, diet=diet, limit=20, offset=0)
    if not items:
        if diet:
            msg = f"No products matching '{query}' satisfy hard diet requirement '{diet}'"
            return _json_rpc_ok(
                rpc_id,
                _fail_task(
                    db,
                    task=task,
                    principal=principal,
                    code="POLICY_BLOCKED",
                    message=msg,
                    reason="R-HARD-DIET",
                    policy={
                        "outcome": "BLOCKED",
                        "rules": [
                            {
                                "rule_id": "R-HARD-DIET",
                                "outcome": "BLOCKED",
                                "message": msg,
                            }
                        ],
                    },
                    rule_ids=["R-HARD-DIET"],
                ),
            )
        return _json_rpc_ok(
            rpc_id,
            _fail_task(
                db,
                task=task,
                principal=principal,
                code="NOT_FOUND",
                message=f"No matching products found for '{query}'",
                reason="NOT_FOUND",
            ),
        )

    clar_res, _ = needs_clarification(
        combined_text or query,
        items,
        provider=decision_provider,
    )
    if clar_res.answer:
        top_products = items[:6]
        pids = [p.id for p in top_products]
        checked = check_products_live(
            db=db,
            aimo_client=aimo_client,
            product_ids=pids,
            quantities={pid: quantity for pid in pids},
            requested_delivery_date=delivery_date,
            customer_id=principal.company_id,
        )
        prod_by_id = {p.id: p for p in top_products}
        clean_pairs = [
            (prod_by_id[ln.product_id], ln)
            for ln in checked
            if ln.delivery_status == "ON_TIME"
            and "PARTIAL" not in ln.flags
            and "CUTOFF_PASSED" not in ln.flags
            and "NO_PRICE" not in ln.flags
        ]
        if not clean_pairs:
            clean_pairs = [
                (prod_by_id[ln.product_id], ln)
                for ln in checked
                if ln.delivery_status != "NOT_DELIVERABLE" and "NO_PRICE" not in ln.flags
            ]
        if not clean_pairs:
            clean_pairs = [(prod_by_id[ln.product_id], ln) for ln in checked[:3]]

        options: list[dict[str, Any]] = []
        if "storage_temperature" in clar_res.missing_slots:
            chilled_pair = next(
                ((p, ln) for p, ln in clean_pairs if _storage_label(p) == "chilled"),
                None,
            )
            uht_pair = next(
                ((p, ln) for p, ln in clean_pairs if _storage_label(p) == "uht"),
                None,
            )
            selected_pairs = [pair for pair in (chilled_pair, uht_pair) if pair is not None]
            if not selected_pairs:
                selected_pairs = clean_pairs[:3]
        else:
            selected_pairs = clean_pairs[:3]

        for idx, (prod_out, line) in enumerate(selected_pairs):
            options.append(
                _build_typed_option(
                    prod_out,
                    line,
                    option_id=f"opt_{idx + 1}_{prod_out.id}",
                    action="select_option",
                    quantity=quantity,
                )
            )

        return _json_rpc_ok(
            rpc_id,
            _transition_clarification(
                db,
                task=task,
                principal=principal,
                message=clar_res.reason,
                missing_slots=clar_res.missing_slots,
                options=options,
            ),
        )

    # Unambiguous single/top search result -> quote and place order
    selected_id = items[0].id
    return _json_rpc_ok(
        rpc_id,
        _quote_and_place_order(
            db,
            task=task,
            aimo_client=aimo_client,
            gateway=gateway,
            principal=principal,
            product_id=selected_id,
            quantity=quantity,
            requested_delivery_date=delivery_date,
            diet=diet,
            note=note,
            turn_number=turn_number,
            recipe_id=recipe_id,
            target_pax=target_pax,
        ),
    )


def _handle_tasks_get(
    db: Session,
    *,
    principal: Principal,
    rpc_id: str | int | None,
    params: dict[str, Any],
) -> dict[str, Any]:
    raw_task_id = params.get("taskId") or params.get("task_id") or params.get("id")
    if not isinstance(raw_task_id, str) or not raw_task_id.strip():
        return _json_rpc_error(
            rpc_id,
            code=-32602,
            message="Invalid params: 'taskId' is required",
        )
    task = _load_company_task(db, raw_task_id.strip(), principal)
    sync_task_with_linked_order(db, task)
    record_audit(
        db,
        company_id=principal.company_id,
        actor=principal,
        action="a2a.tasks_get",
        entity_type="a2a_task",
        entity_id=task.id,
        outcome="ok",
        details={
            "state": task.state,
            "linked_order_id": task.linked_order_id,
        },
    )
    db.commit()
    db.refresh(task)
    return _json_rpc_ok(rpc_id, _task_to_rpc_result(task))


def _handle_tasks_cancel(
    db: Session,
    *,
    principal: Principal,
    rpc_id: str | int | None,
    params: dict[str, Any],
) -> dict[str, Any]:
    raw_task_id = params.get("taskId") or params.get("task_id") or params.get("id")
    if not isinstance(raw_task_id, str) or not raw_task_id.strip():
        return _json_rpc_error(
            rpc_id,
            code=-32602,
            message="Invalid params: 'taskId' is required",
        )
    task = _load_company_task(db, raw_task_id.strip(), principal)
    sync_task_with_linked_order(db, task)

    if task.state == "canceled":
        record_audit(
            db,
            company_id=principal.company_id,
            actor=principal,
            action="a2a.tasks_cancel",
            entity_type="a2a_task",
            entity_id=task.id,
            outcome="ok",
            details={"state": task.state, "linked_order_id": task.linked_order_id},
        )
        db.commit()
        db.refresh(task)
        return _json_rpc_ok(rpc_id, _task_to_rpc_result(task))

    if task.state in ("completed", "failed"):
        raise AppError(
            "INVALID_STATE",
            409,
            f"Cannot cancel A2A task in terminal state '{task.state}'",
            details={"task_id": task.id, "state": task.state},
        )

    if task.linked_order_id:
        order = db.scalar(
            select(Order).where(
                Order.id == task.linked_order_id,
                Order.company_id == task.company_id,
            )
        )
        if order is not None and order.status == "PENDING_APPROVAL":
            orders.cancel_order(
                db,
                principal,
                order.id,
                OrderDecisionRequest(note="Cancelled via A2A tasks/cancel"),
            )
            sync_task_with_linked_order(db, task, run_expiry=False)

    now = clock.now()
    ctx = _append_history(
        task.context or {},
        state=cast(A2ATaskState, task.state),
        role="agent",
        message="Requested task cancellation via tasks/cancel",
    )
    if task.state != "canceled":
        task.state = "canceled"
        ctx = _append_history(
            ctx,
            state="canceled",
            role="gateway",
            message=f"Task {task.id} canceled",
            data={"reason": "CANCELLED", "linked_order_id": task.linked_order_id},
        )
    ctx["reason"] = "CANCELLED"
    task.context = ctx
    task.updated_at = now

    record_audit(
        db,
        company_id=principal.company_id,
        actor=principal,
        action="a2a.tasks_cancel",
        entity_type="a2a_task",
        entity_id=task.id,
        outcome="ok",
        details={
            "state": task.state,
            "linked_order_id": task.linked_order_id,
        },
    )
    db.commit()
    db.refresh(task)
    return _json_rpc_ok(rpc_id, _task_to_rpc_result(task))


def _handle_session_connect(
    db: Session,
    *,
    principal: Principal,
    rpc_id: str | int | None,
    method: str,
) -> dict[str, Any]:
    payload = build_session_capabilities_payload(db, principal)
    record_audit(
        db,
        company_id=principal.company_id,
        actor=principal,
        action="a2a.session_connect",
        entity_type="a2a_session",
        entity_id=principal.id,
        outcome="ok",
        details={
            "method": method,
            "allow_price_negotiation": payload["allow_price_negotiation"],
            "supported_matters": [
                m["id"] for m in payload["supported_matters"] if m["enabled"]
            ],
        },
    )
    db.commit()
    return _json_rpc_ok(rpc_id, payload)


def handle_rpc(
    db: Session,
    *,
    aimo_client: AimoClient,
    gateway: OrderGateway,
    decision_provider: DecisionProvider,
    principal: Principal,
    rpc: A2ARpcRequest,
) -> dict[str, Any]:
    """Dispatch a JSON-RPC 2.0 A2A request (session/connect, message/send, tasks/*)."""
    if rpc.jsonrpc != "2.0":
        return _json_rpc_error(
            rpc.id,
            code=-32600,
            message="Invalid Request: 'jsonrpc' must be '2.0'",
        )
    if rpc.method not in _SUPPORTED_METHODS:
        return _json_rpc_error(
            rpc.id,
            code=-32601,
            message=f"Method not found: {rpc.method}",
        )

    params = rpc.params if isinstance(rpc.params, dict) else {}
    if rpc.method in ("session/connect", "session/capabilities"):
        return _handle_session_connect(
            db,
            principal=principal,
            rpc_id=rpc.id,
            method=rpc.method,
        )
    if rpc.method == "message/send":
        return _handle_message_send(
            db,
            aimo_client=aimo_client,
            gateway=gateway,
            decision_provider=decision_provider,
            principal=principal,
            rpc_id=rpc.id,
            params=params,
        )
    if rpc.method == "tasks/get":
        return _handle_tasks_get(
            db,
            principal=principal,
            rpc_id=rpc.id,
            params=params,
        )
    return _handle_tasks_cancel(
        db,
        principal=principal,
        rpc_id=rpc.id,
        params=params,
    )


def list_a2a_tasks(
    db: Session,
    *,
    principal: Principal,
    limit: int = 20,
    offset: int = 0,
) -> A2ATaskListResponse:
    """List company-scoped A2A tasks newest first, syncing linked order state on read."""
    orders.expire_pending_orders(db, company_id=principal.company_id)

    total = (
        db.scalar(
            select(func.count())
            .select_from(A2ATask)
            .where(A2ATask.company_id == principal.company_id)
        )
        or 0
    )
    rows = db.scalars(
        select(A2ATask)
        .where(A2ATask.company_id == principal.company_id)
        .order_by(A2ATask.created_at.desc(), A2ATask.id.desc())
        .offset(offset)
        .limit(limit)
    ).all()

    any_changed = False
    for row in rows:
        if sync_task_with_linked_order(db, row, run_expiry=False):
            any_changed = True
    if any_changed:
        db.commit()
        for row in rows:
            db.refresh(row)

    return A2ATaskListResponse(
        items=[_task_to_trace_out(row) for row in rows],
        total=total,
    )
