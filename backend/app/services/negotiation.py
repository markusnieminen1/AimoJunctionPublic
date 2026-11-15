"""Account-gated preliminary A2A price negotiation with deterministic volume floors."""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app import clock
from app.aimo.client import AimoClient
from app.aimo.normalise import check_products_live
from app.audit import record_audit
from app.auth import compute_agent_spent_today
from app.errors import AppError, request_id_var
from app.ids import new_id
from app.llm.decisions import is_prompt_injection
from app.models import (
    A2ATask,
    Agent,
    Company,
    CompanyNegotiationSetting,
    Product,
    Quote,
    QuoteLineModel,
)
from app.schemas import (
    CompanyNegotiationAdminListResponse,
    CompanyNegotiationAdminOut,
    NegotiationSettingsOut,
    NegotiationSettingsUpdateRequest,
    NegotiationStatus,
    NegotiationTurnOut,
    Principal,
    QuoteLine,
    QuoteNegotiateRequest,
    QuoteNegotiateResponse,
)
from app.services import purchasing_guard
from app.services.admin_ranking import (
    evaluate_brand_preference_score,
    get_active_brand_preferences,
)
from app.services.catalog import product_passes_diets, resolve_diet_keys
from app.services.policy import evaluate_policy
from app.services.quotes import (
    compute_quote_expires_at,
    evaluate_diet_violations,
    quote_to_out,
    worst_delivery_status,
)
from app.services.recipe_sanity import evaluate_recipe_sanity

_PCT_IN_TEXT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*%")
_BLOCKER_FLAGS = {"CUTOFF_PASSED", "NOT_DELIVERABLE", "SALES_BLOCKED", "NO_PRICE", "PARTIAL"}


# Private commercial volume-discount ceilings by Synkka category prefix.
# Never serialized or exposed in any API response, A2A message, or audit payload.
_CATEGORY_VOLUME_CEILINGS_PCT: tuple[tuple[str, float], ...] = (
    ("22", 6.0),  # Dairy & plant-based chilled/ambient
    ("18", 4.5),  # Fresh fish, meat & proteins
    ("24", 7.5),  # Bakery & grain products
    ("25", 8.0),  # Dry pantry & staples
    ("26", 7.5),  # Beverages & foodservice condiments
)
_DEFAULT_CATEGORY_CEILING_PCT = 5.5
DEFAULT_MAX_AUTO_DISCOUNT_PCT = 8.0


def _category_ceiling_pct(category: str | None) -> float:
    cat = (category or "").strip()
    for prefix, cap in _CATEGORY_VOLUME_CEILINGS_PCT:
        if cat.startswith(prefix):
            return cap
    return _DEFAULT_CATEGORY_CEILING_PCT


def _effective_max_auto_discount_pct(row: CompanyNegotiationSetting | None) -> float:
    if row is not None and row.max_discount_override_pct is not None:
        return round(min(DEFAULT_MAX_AUTO_DISCOUNT_PCT, float(row.max_discount_override_pct)), 2)
    return DEFAULT_MAX_AUTO_DISCOUNT_PCT


def negotiation_settings_to_out(
    company_id: str,
    row: CompanyNegotiationSetting | None,
) -> NegotiationSettingsOut:
    """Serialize company negotiation settings (defaulting to disabled when unconfigured)."""
    if row is None:
        return NegotiationSettingsOut(
            company_id=company_id,
            allow_price_negotiation=False,
            max_auto_discount_pct=DEFAULT_MAX_AUTO_DISCOUNT_PCT,
            max_discount_override_pct=None,
            updated_at=None,
        )
    return NegotiationSettingsOut(
        company_id=row.company_id,
        allow_price_negotiation=bool(row.allow_price_negotiation),
        max_auto_discount_pct=_effective_max_auto_discount_pct(row),
        max_discount_override_pct=(
            float(row.max_discount_override_pct)
            if row.max_discount_override_pct is not None
            else None
        ),
        updated_at=row.updated_at,
    )


def company_negotiation_admin_to_out(
    company: Company,
    row: CompanyNegotiationSetting | None,
) -> CompanyNegotiationAdminOut:
    """Serialize per-company negotiation setting for GET/PUT /api/admin/company-negotiations."""
    return CompanyNegotiationAdminOut(
        company_id=company.id,
        company_name=company.name,
        allow_price_negotiation=bool(row.allow_price_negotiation) if row is not None else False,
        max_auto_discount_pct=_effective_max_auto_discount_pct(row),
        max_discount_override_pct=(
            float(row.max_discount_override_pct)
            if row is not None and row.max_discount_override_pct is not None
            else None
        ),
        updated_at=row.updated_at if row is not None else None,
    )


def get_negotiation_settings(db: Session, company_id: str) -> NegotiationSettingsOut:
    """Fetch company negotiation settings for any authenticated principal."""
    row = db.get(CompanyNegotiationSetting, company_id)
    return negotiation_settings_to_out(company_id, row)


def update_negotiation_settings(
    db: Session,
    principal: Principal,
    req: NegotiationSettingsUpdateRequest,
) -> NegotiationSettingsOut:
    """Create or update company negotiation settings (human user only) and record audit."""
    now = clock.now()
    override_pct = (
        req.max_discount_override_pct
        if req.max_discount_override_pct is not None
        else req.max_auto_discount_pct
    )
    row = db.get(CompanyNegotiationSetting, principal.company_id)
    if row is None:
        row = CompanyNegotiationSetting(
            company_id=principal.company_id,
            allow_price_negotiation=bool(req.allow_price_negotiation),
            max_discount_override_pct=override_pct,
            updated_at=now,
        )
        db.add(row)
    else:
        row.allow_price_negotiation = bool(req.allow_price_negotiation)
        row.max_discount_override_pct = override_pct
        row.updated_at = now
    db.flush()

    record_audit(
        db,
        company_id=principal.company_id,
        actor=principal,
        action="account.negotiation_settings.update",
        entity_type="negotiation_settings",
        entity_id=principal.company_id,
        outcome="ok",
        details={
            "allow_price_negotiation": row.allow_price_negotiation,
            "max_discount_override_pct": row.max_discount_override_pct,
        },
    )
    db.commit()
    db.refresh(row)
    return negotiation_settings_to_out(principal.company_id, row)


def list_company_negotiations_admin(
    db: Session,
    principal: Principal,
) -> CompanyNegotiationAdminListResponse:
    """List per-company negotiation toggles (caller's company first, then alphabetical)."""
    companies = list(db.scalars(select(Company)).all())
    companies.sort(key=lambda c: (0 if c.id == principal.company_id else 1, c.name.lower(), c.id))
    settings_rows = db.scalars(select(CompanyNegotiationSetting)).all()
    by_company = {r.company_id: r for r in settings_rows}
    return CompanyNegotiationAdminListResponse(
        items=[company_negotiation_admin_to_out(c, by_company.get(c.id)) for c in companies]
    )


def update_company_negotiation_admin(
    db: Session,
    principal: Principal,
    company_id: str,
    req: NegotiationSettingsUpdateRequest,
) -> CompanyNegotiationAdminOut:
    """Toggle allow_price_negotiation for a specific company via Admin panel (human user only)."""
    company = db.get(Company, company_id)
    if company is None:
        raise AppError("NOT_FOUND", 404, f"Company '{company_id}' not found")

    now = clock.now()
    explicit_override = (
        req.max_discount_override_pct
        if req.max_discount_override_pct is not None
        else req.max_auto_discount_pct
    )
    row = db.get(CompanyNegotiationSetting, company.id)
    if row is None:
        row = CompanyNegotiationSetting(
            company_id=company.id,
            allow_price_negotiation=bool(req.allow_price_negotiation),
            max_discount_override_pct=explicit_override,
            updated_at=now,
        )
        db.add(row)
    else:
        row.allow_price_negotiation = bool(req.allow_price_negotiation)
        if explicit_override is not None:
            row.max_discount_override_pct = explicit_override
        row.updated_at = now
    db.flush()

    audit_details = {
        "company_id": company.id,
        "company_name": company.name,
        "allow_price_negotiation": row.allow_price_negotiation,
        "max_auto_discount_pct": _effective_max_auto_discount_pct(row),
        "max_discount_override_pct": row.max_discount_override_pct,
    }
    record_audit(
        db,
        company_id=principal.company_id,
        actor=principal,
        action="admin.company_negotiation.update",
        entity_type="negotiation_settings",
        entity_id=company.id,
        outcome="ok",
        details=audit_details,
    )
    if company.id != principal.company_id:
        record_audit(
            db,
            company_id=company.id,
            actor=principal,
            action="admin.company_negotiation.update",
            entity_type="negotiation_settings",
            entity_id=company.id,
            outcome="ok",
            details=audit_details,
        )
    db.commit()
    db.refresh(row)
    return company_negotiation_admin_to_out(company, row)


def _find_compatible_alternatives(
    db: Session,
    *,
    orig: Product,
    diet_keys: list[str],
    exclude_ids: set[str],
    limit: int = 2,
) -> list[str]:
    cat_prefix = (orig.category or "")[:6]
    if not cat_prefix:
        return []
    candidates = db.scalars(
        select(Product)
        .where(
            Product.category.like(f"{cat_prefix}%"),
            Product.id.not_in(exclude_ids),
        )
        .order_by(Product.id.asc())
        .limit(20)
    ).all()
    suggestions: list[str] = []
    for cand in candidates:
        if diet_keys:
            passed, _ = product_passes_diets(cand, diet_keys)
            if not passed:
                continue
        suggestions.append(f"{cand.id} ({cand.name})")
        if len(suggestions) >= limit:
            break
    return suggestions


def _resolve_requested_discount_pct(
    req: QuoteNegotiateRequest,
    list_total_eur: float,
) -> float:
    if req.target_discount_pct is not None:
        return max(0.0, float(req.target_discount_pct))
    if req.target_total_eur is not None and list_total_eur > 0:
        implied = (1.0 - (float(req.target_total_eur) / list_total_eur)) * 100.0
        return round(max(0.0, implied), 2)
    if req.justification:
        match = _PCT_IN_TEXT_RE.search(req.justification)
        if match:
            try:
                return max(0.0, float(match.group(1)))
            except ValueError:
                pass
    return 3.0


def _compute_line_allowed_discount_pct(
    *,
    line: QuoteLine,
    product: Product | None,
    eligible_line_count: int,
    basket_total_eur: float,
    brand_pref_score: float,
    override_cap_pct: float | None,
) -> tuple[float, str | None]:
    """Compute deterministic max allowed commercial discount % for one line."""
    if line.unit_price_eur is None or line.unit_price_eur <= 0:
        return 0.0, "No negotiable unit price available"
    if _BLOCKER_FLAGS & set(line.flags):
        flag_str = ", ".join(sorted(_BLOCKER_FLAGS & set(line.flags)))
        return 0.0, f"Tight availability / delivery constraint ({flag_str}) — 0% discount"
    if line.available_qty_state == "ZERO":
        return 0.0, "Out of stock (ZERO) — 0% discount"
    if (
        line.available_qty_state == "FINITE"
        and line.available_amount is not None
        and float(line.available_amount) < float(line.quantity) * 1.5
    ):
        return 0.0, "Tight finite stock buffer — 0% discount"
    if line.delivery_status not in ("ON_TIME", "UNKNOWN"):
        return 0.0, f"Delivery status {line.delivery_status} — 0% discount"

    line_sub = float(line.line_total_eur or 0.0)
    qty = float(line.quantity)

    earned = 0.0
    if line_sub >= 500.0 or qty >= 200.0:
        earned += 3.5
    elif line_sub >= 150.0 or qty >= 50.0:
        earned += 2.0
    elif line_sub >= 50.0 or qty >= 12.0:
        earned += 1.0

    if eligible_line_count >= 3 and basket_total_eur >= 300.0:
        earned += 2.5
    elif eligible_line_count >= 2 and basket_total_eur >= 120.0:
        earned += 1.5

    brand_lower = ((product.brand if product else "") or "").lower()
    if brand_pref_score > 0 or brand_lower in {"valio", "oddlygood"}:
        if earned > 0:
            earned += 0.5

    ceiling = _category_ceiling_pct(product.category if product else None)
    if override_cap_pct is not None:
        ceiling = min(ceiling, float(override_cap_pct))

    return round(max(0.0, min(earned, ceiling)), 2), None


def negotiate_quote(
    db: Session,
    aimo_client: AimoClient,
    principal: Principal,
    req: QuoteNegotiateRequest,
    *,
    source_context: dict[str, Any] | None = None,
    existing_a2a_task: A2ATask | None = None,
) -> QuoteNegotiateResponse:
    """Run account-gated preliminary A2A price negotiation and mint a locked 30-min Quote."""
    setting = db.get(CompanyNegotiationSetting, principal.company_id)
    if setting is None or not setting.allow_price_negotiation:
        raise AppError(
            "NEGOTIATION_DISABLED",
            403,
            (
                "Preliminary price negotiation is disabled for this company. "
                "A human manager can enable it in the Admin panel (/admin)."
            ),
        )

    diet_keys = resolve_diet_keys(req.diet, None) if (req.diet and req.diet.strip()) else []

    aggregated_qty: dict[str, float] = {}
    for item in req.lines:
        aggregated_qty[item.product_id] = aggregated_qty.get(item.product_id, 0.0) + float(
            item.quantity
        )
    product_ids = list(aggregated_qty.keys())

    base_lines = check_products_live(
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
    active_brand_prefs = get_active_brand_preferences(db, principal.company_id)

    compatibility_notes: list[str] = []
    injection_flagged = False
    semantic_guard_reason: str | None = None
    if req.justification and req.justification.strip():
        inj = is_prompt_injection(req.justification)
        if inj.answer:
            injection_flagged = True
            semantic_guard_reason = inj.reason
            compatibility_notes.append(
                "Adversarial override instructions in justification were ignored; "
                "deterministic commercial volume tiers applied."
            )
            already_screened_upstream = bool(
                (
                    existing_a2a_task is not None
                    and (existing_a2a_task.context or {}).get("turn_injection_screened")
                )
                or (source_context and source_context.get("upstream_injection_screened"))
            )
            if not already_screened_upstream:
                record_audit(
                    db,
                    company_id=principal.company_id,
                    actor=principal,
                    action="order.injection_screen",
                    entity_type="quote",
                    entity_id=None,
                    outcome="ok",
                    details={
                        "request_id": request_id_var.get() or None,
                        "flagged": True,
                        "reason": inj.reason,
                        "verifier_model": inj.verifier_model,
                        "source": "quote_negotiation_justification",
                        "note_preview": req.justification[:200],
                    },
                )
                db.flush()
                purchasing_guard.check_repeated_injection_and_quarantine(
                    db,
                    principal=principal,
                    reason=inj.reason,
                )

    # Step 1: Diet PASS, live stock, and cut-off compatibility checks FIRST
    diet_violations = evaluate_diet_violations(product_map, base_lines, req.diet)
    violating_pids: set[str] = set()
    for viol in diet_violations:
        compatibility_notes.append(f"Diet incompatibility: {viol}")
        pid_prefix = viol.split(":", 1)[0].split(" ", 1)[0].strip()
        if pid_prefix in product_map:
            violating_pids.add(pid_prefix)
            alts = _find_compatible_alternatives(
                db,
                orig=product_map[pid_prefix],
                diet_keys=diet_keys,
                exclude_ids=set(product_ids),
            )
            if alts:
                compatibility_notes.append(
                    f"Compatible diet-safe alternatives for {pid_prefix}: {', '.join(alts)}"
                )

    for ln in base_lines:
        line_issues: list[str] = []
        if "PARTIAL" in ln.flags:
            avail = f"{ln.available_amount:g}" if ln.available_amount is not None else "limited"
            line_issues.append(f"PARTIAL stock ({avail} {ln.sales_unit} available)")
        if "CUTOFF_PASSED" in ln.flags:
            line_issues.append("delivery cut-off passed")
        if ln.delivery_status in ("LATE", "NOT_DELIVERABLE"):
            line_issues.append(f"delivery status {ln.delivery_status}")
        if line_issues:
            note = f"{ln.product_id} ({ln.product_name}): {', '.join(line_issues)} — 0% discount"
            orig_prod = product_map.get(ln.product_id)
            if orig_prod is not None:
                alts = _find_compatible_alternatives(
                    db,
                    orig=orig_prod,
                    diet_keys=diet_keys,
                    exclude_ids=set(product_ids),
                )
                if alts:
                    note += f"; consider {', '.join(alts)}"
            compatibility_notes.append(note)

    if not compatibility_notes:
        compatibility_notes.append(
            "All lines passed Synkka diet verification, stock sufficiency, and cut-off checks."
        )

    list_total_eur = round(
        sum(ln.line_total_eur for ln in base_lines if ln.line_total_eur is not None),
        2,
    )
    requested_pct = _resolve_requested_discount_pct(req, list_total_eur)

    eligible_count = sum(
        1
        for ln in base_lines
        if ln.product_id not in violating_pids
        and not (_BLOCKER_FLAGS & set(ln.flags))
        and ln.delivery_status in ("ON_TIME", "UNKNOWN")
        and ln.unit_price_eur is not None
    )

    # Step 2: Deterministic per-line commercial floor evaluation
    base_unit_price_by_sku: dict[str, float] = {}
    negotiated_lines: list[QuoteLine] = []

    for ln in base_lines:
        if ln.unit_price_eur is not None:
            base_unit_price_by_sku[ln.product_id] = round(float(ln.unit_price_eur), 4)

        prod = product_map.get(ln.product_id)
        if ln.product_id in violating_pids or ln.unit_price_eur is None:
            negotiated_lines.append(ln)
            continue

        b_score = (
            evaluate_brand_preference_score(
                brand=prod.brand if prod else None,
                category=(prod.category if prod else ""),
                preferences=active_brand_prefs,
            )
            if prod is not None
            else 0.0
        )
        max_line_pct, _ = _compute_line_allowed_discount_pct(
            line=ln,
            product=prod,
            eligible_line_count=eligible_count,
            basket_total_eur=list_total_eur,
            brand_pref_score=b_score,
            override_cap_pct=setting.max_discount_override_pct,
        )
        applied_pct = min(requested_pct, max_line_pct)
        if applied_pct <= 0.0:
            negotiated_lines.append(ln)
            continue

        base_unit = float(ln.unit_price_eur)
        neg_unit = round(base_unit * (1.0 - applied_pct / 100.0), 2)
        neg_unit = max(0.01, min(base_unit, neg_unit))
        neg_line_total = round(neg_unit * float(ln.quantity), 2)
        negotiated_lines.append(
            ln.model_copy(
                update={
                    "unit_price_eur": neg_unit,
                    "line_total_eur": neg_line_total,
                }
            )
        )

    negotiated_total_eur = round(
        sum(ln.line_total_eur for ln in negotiated_lines if ln.line_total_eur is not None),
        2,
    )
    total_incl_vat_eur = round(
        sum(
            round(ln.line_total_eur * (1.0 + (ln.tax_rate or 0.0) / 100.0), 2)
            for ln in negotiated_lines
            if ln.line_total_eur is not None
        ),
        2,
    )
    savings_eur = round(max(0.0, list_total_eur - negotiated_total_eur), 2)
    savings_pct = (
        round((savings_eur / list_total_eur) * 100.0, 2) if list_total_eur > 0 else 0.0
    )

    status: NegotiationStatus
    if injection_flagged or savings_eur <= 0.0 or requested_pct > savings_pct + 5.0:
        status = "FLOOR_REACHED"
    elif requested_pct <= savings_pct + 0.35:
        status = "ACCEPTED"
    else:
        status = "COUNTER_OFFER"

    final_action = (
        "ACCEPT"
        if status == "ACCEPTED"
        else ("COUNTER_OFFER" if status == "COUNTER_OFFER" else "FLOOR_HOLD")
    )
    proposed_target_eur = round(list_total_eur * (1.0 - min(requested_pct, 99.0) / 100.0), 2)
    rounds: list[NegotiationTurnOut] = [
        NegotiationTurnOut(
            round=1,
            actor="buyer",
            action="PROPOSE",
            proposed_discount_pct=round(requested_pct, 2),
            proposed_total_eur=proposed_target_eur,
            message=(
                f"Preliminary volume quote request across {len(negotiated_lines)} line(s) "
                f"(catalog baseline €{list_total_eur:.2f}, target -{requested_pct:.1f}%)."
            ),
        ),
        NegotiationTurnOut(
            round=2,
            actor="aimo_sales_agent",
            action="COMPATIBILITY_CHECK",
            proposed_discount_pct=None,
            proposed_total_eur=list_total_eur,
            message=compatibility_notes[0],
        ),
        NegotiationTurnOut(
            round=3,
            actor="aimo_sales_agent",
            action=final_action,
            proposed_discount_pct=savings_pct,
            proposed_total_eur=negotiated_total_eur,
            message=(
                f"{status}: Locked 30-minute quote at €{negotiated_total_eur:.2f} "
                f"(-{savings_pct:.2f}%, saving €{savings_eur:.2f} vs €{list_total_eur:.2f})."
            ),
        ),
    ]

    # Step 3: Evaluate Layer 1 policy preview on negotiated basket & persist locked 30-min Quote
    co2_values = [ln.co2_kg for ln in negotiated_lines if ln.co2_kg is not None]
    total_co2_kg = round(sum(co2_values), 4) if co2_values else None
    overall_delivery_status = worst_delivery_status([ln.delivery_status for ln in negotiated_lines])
    recipe_warning = evaluate_recipe_sanity(
        product_map=product_map,
        lines=negotiated_lines,
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
                lines=negotiated_lines,
            )
        )

    policy_preview = evaluate_policy(
        principal=principal,
        agent=agent,
        lines=negotiated_lines,
        product_categories=product_categories,
        total_excl_vat_eur=negotiated_total_eur,
        spent_today_eur=spent_today_eur,
        diet_violations=diet_violations or None,
        recipe_sanity_warning=recipe_warning,
        standing_drift_reason=standing_drift_reason,
        standing_drift_product_ids=standing_drift_product_ids,
        semantic_guard_reason=semantic_guard_reason,
    )

    now = clock.now()
    now_iso = clock.format_helsinki_iso(now)
    expires_at = compute_quote_expires_at(negotiated_lines, now)

    task_row = existing_a2a_task
    if task_row is None:
        task_id = new_id("tsk_")
        task_history = [
            {
                "at": now_iso,
                "state": "working",
                "role": "agent",
                "message": rounds[0].message,
                "data": {
                    "round": 1,
                    "requested_discount_pct": round(requested_pct, 2),
                    "list_total_eur": list_total_eur,
                },
            },
            {
                "at": now_iso,
                "state": "completed",
                "role": "gateway",
                "message": rounds[-1].message,
                "data": {
                    "reason": status,
                    "status": status,
                    "negotiated_total_eur": negotiated_total_eur,
                    "savings_eur": savings_eur,
                    "savings_pct": savings_pct,
                },
            },
        ]
        task_row = A2ATask(
            id=task_id,
            company_id=principal.company_id,
            principal_kind=principal.kind,
            principal_id=principal.id,
            state="completed",
            context={
                "principal": principal.model_dump(mode="json"),
                "reason": status,
                "history": task_history,
            },
            artifacts=[],
            linked_order_id=None,
            created_at=now,
            updated_at=now,
        )
        db.add(task_row)
        db.flush()

    merged_src: dict[str, Any] = {"a2a_task_id": task_row.id}
    if source_context:
        merged_src.update(source_context)
    merged_src.pop("upstream_injection_screened", None)

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
    policy_preview_json["_source_context"] = merged_src
    policy_preview_json["_negotiation"] = {
        "status": status,
        "negotiated_savings_eur": savings_eur,
        "savings_pct": savings_pct,
        "list_total_eur": list_total_eur,
        "base_unit_price_eur_by_sku": base_unit_price_by_sku,
        "semantic_guard_reason": semantic_guard_reason,
    }

    quote = Quote(
        id=new_id("qte_"),
        company_id=principal.company_id,
        status="OPEN",
        created_at=now,
        expires_at=expires_at,
        requested_delivery_date=req.requested_delivery_date,
        total_excl_vat_eur=negotiated_total_eur,
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

    for idx, ln in enumerate(negotiated_lines):
        db.add(
            QuoteLineModel(
                quote_id=quote.id,
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
    db.flush()

    loaded_quote = db.scalar(
        select(Quote).options(selectinload(Quote.lines)).where(Quote.id == quote.id)
    )
    assert loaded_quote is not None
    quote_out = quote_to_out(loaded_quote)

    task_row.artifacts = [
        {
            "name": "negotiated_quote",
            "description": f"Negotiated quote {quote_out.id} ({status})",
            "parts": [{"kind": "data", "data": quote_out.model_dump(mode="json")}],
        }
    ]
    db.flush()

    record_audit(
        db,
        company_id=principal.company_id,
        actor=principal,
        action="quote.negotiated",
        entity_type="quote",
        entity_id=quote.id,
        outcome="ok",
        details={
            "request_id": request_id_var.get() or None,
            "a2a_task_id": task_row.id,
            "status": status,
            "list_total_eur": list_total_eur,
            "negotiated_total_eur": negotiated_total_eur,
            "savings_eur": savings_eur,
            "savings_pct": savings_pct,
            "requested_discount_pct": round(requested_pct, 2),
            "injection_clamped": injection_flagged,
            "policy_outcome": policy_preview.outcome,
        },
    )
    db.commit()

    return QuoteNegotiateResponse(
        status=status,
        rounds=rounds,
        quote=quote_out,
        list_total_eur=list_total_eur,
        negotiated_total_eur=negotiated_total_eur,
        savings_eur=savings_eur,
        savings_pct=savings_pct,
        compatibility_notes=compatibility_notes,
    )
