"""Restaurant Menu Bot: Valio recipe pairing, deal optimization, diet safety & policy preview."""

from __future__ import annotations

import math
from uuid import uuid4

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app import clock
from app.aimo.client import AimoClient
from app.aimo.normalise import check_products_live
from app.audit import record_audit
from app.auth import Principal, compute_agent_spent_today
from app.domain.valio_recipes import (
    RecipeIngredientSpec,
    ValioRecipe,
    match_recipes,
)
from app.errors import request_id_var
from app.llm.prep_list import default_delivery_date
from app.models import Agent, Product
from app.schemas import (
    MenuIngredientSelectionOut,
    MenuPlanRequest,
    MenuPlanResponse,
    MenuRecipeSummaryOut,
    QuoteLine,
    QuoteLineInput,
    ReasoningStepOut,
)
from app.services.catalog import product_passes_diets, product_to_out, resolve_diet_keys
from app.services.deals import (
    compute_discount_metrics,
    extract_list_prices,
    fixture_priced_ids,
)
from app.services.orders import compute_last_available_delivery_date
from app.services.policy import evaluate_policy
from app.services.promotions import evaluate_product_promotions, get_active_promotions


def _safe_delivery_date(requested: str | None) -> str:
    if requested and requested.strip():
        return requested.strip()
    return default_delivery_date()


def _escape_like(token: str) -> str:
    return token.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _fetch_candidate_products(
    db: Session,
    aimo_client: AimoClient,
    recipes: list[ValioRecipe],
) -> list[Product]:
    """Fetch targeted recipe candidates from DB without scanning the full 17k+ catalog."""
    preferred_ids: list[str] = []
    keywords: list[str] = []
    cat_prefixes: list[str] = []

    for recipe in recipes:
        for spec in recipe.ingredients:
            preferred_ids.extend(spec.preferred_product_ids)
            keywords.extend(spec.search_keywords)
            cat_prefixes.extend(spec.category_prefixes)

    targeted_ids = list(dict.fromkeys([*preferred_ids, *sorted(fixture_priced_ids(aimo_client))]))
    by_id: dict[str, Product] = {}

    if targeted_ids:
        for prod in db.scalars(select(Product).where(Product.id.in_(targeted_ids))).all():
            by_id[prod.id] = prod

    conditions = []
    for kw in dict.fromkeys(k.strip().lower() for k in keywords if k and k.strip()):
        pattern = f"%{_escape_like(kw)}%"
        conditions.append(func.lower(Product.search_text).like(pattern, escape="\\"))
        conditions.append(
            and_(
                Product.search_text == "",
                or_(
                    func.lower(Product.name).like(pattern, escape="\\"),
                    func.lower(func.coalesce(Product.name_fi, "")).like(pattern, escape="\\"),
                    func.lower(func.coalesce(Product.name_en, "")).like(pattern, escape="\\"),
                    func.lower(func.coalesce(Product.brand, "")).like(pattern, escape="\\"),
                ),
            )
        )
    for pref in dict.fromkeys(p.strip() for p in cat_prefixes if p and p.strip()):
        conditions.append(Product.category.like(f"{_escape_like(pref)}%", escape="\\"))

    if conditions:
        stmt = select(Product).where(or_(*conditions)).order_by(Product.id.asc()).limit(250)
        for prod in db.scalars(stmt).all():
            by_id.setdefault(prod.id, prod)

    return list(by_id.values())


def _candidate_pool_for_ingredient(
    diet_safe_products: list[Product],
    spec: RecipeIngredientSpec,
) -> list[Product]:
    """Gather pre-filtered diet-safe candidate products matching an ingredient spec."""
    by_id = {p.id: p for p in diet_safe_products}

    ordered: list[Product] = []
    seen: set[str] = set()

    # 1. Preferred SKUs in spec order
    for pid in spec.preferred_product_ids:
        prod = by_id.get(pid)
        if prod is not None and prod.id not in seen:
            seen.add(prod.id)
            ordered.append(prod)

    # 2. Keyword matches in Finnish/English name or brand
    kw_lower = [kw.lower() for kw in spec.search_keywords if kw]
    for prod in diet_safe_products:
        if prod.id in seen:
            continue
        haystack = (
            f"{prod.name} {prod.name_fi or ''} {prod.name_en or ''} {prod.brand or ''}"
        ).lower()
        if any(kw in haystack for kw in kw_lower):
            seen.add(prod.id)
            ordered.append(prod)

    # 3. Category prefix fallback
    if len(ordered) < 6 and spec.category_prefixes:
        for prod in diet_safe_products:
            if prod.id in seen or not prod.category:
                continue
            if any(prod.category.startswith(pref) for pref in spec.category_prefixes):
                seen.add(prod.id)
                ordered.append(prod)
            if len(ordered) >= 10:
                break

    return ordered[:10]


def _compute_quantity(spec: RecipeIngredientSpec, pax: int, lot_size: float | None) -> float:
    raw = max(1, math.ceil((pax / 10.0) * spec.qty_per_10_pax))
    if lot_size is not None and lot_size > 1:
        lot = max(1, int(round(lot_size)))
        remainder = raw % lot
        if remainder != 0:
            raw += lot - remainder
    return float(raw)


def _format_selection_reason(
    *,
    product: Product,
    on_sale: bool,
    discount_pct: float,
    promoted: bool,
    promotion_reasons: list[str],
    diet_keys: list[str],
) -> str:
    parts: list[str] = [f"Selected {product.name} ({product.id})"]
    if on_sale and discount_pct > 0:
        parts.append(f"at -{discount_pct:.1f}% off list price")
    if promoted and promotion_reasons:
        parts.append(f"+ promotion ({'; '.join(promotion_reasons)})")
    if diet_keys:
        parts.append(f"verified [{', '.join(diet_keys)}] diet-safe")
    return "; ".join(parts)


def plan_menu(
    db: Session,
    aimo_client: AimoClient,
    principal: Principal,
    req: MenuPlanRequest,
) -> MenuPlanResponse:
    """Plan a diet-safe, promotion- and deal-aware Nordic foodservice menu for `pax`."""
    delivery_date = _safe_delivery_date(req.requested_delivery_date)
    diet_keys = resolve_diet_keys(req.diet, None)
    now_iso = clock.format_helsinki_iso(clock.now())
    steps: list[ReasoningStepOut] = []

    def add_step(stage: str, summary: str, details: dict[str, object] | None = None) -> None:
        steps.append(
            ReasoningStepOut(
                step=len(steps) + 1,
                stage=stage,
                summary=summary,
                actor_kind="system",
                at=now_iso,
                details=details or {},
            )
        )

    add_step(
        "menu_request",
        (
            f"Planning {'Valio-paired ' if req.include_valio_recipes else ''}"
            f"{req.meal_type or 'mixed'} menu for {req.pax} pax on {delivery_date}"
            + (f" with hard diet [{', '.join(diet_keys)}]" if diet_keys else "")
        ),
        {
            "pax": req.pax,
            "meal_type": req.meal_type,
            "requested_delivery_date": delivery_date,
            "diet": req.diet,
            "diet_keys": diet_keys,
            "prefer_on_sale": req.prefer_on_sale,
            "max_budget_eur": req.max_budget_eur,
        },
    )

    customer_id = principal.company_id
    active_rules = get_active_promotions(db, principal.company_id)

    recipes: list[ValioRecipe] = []
    if req.include_valio_recipes:
        recipes = match_recipes(meal_type=req.meal_type, diet_tokens=diet_keys)[:2]
        if not recipes:
            recipes = match_recipes(meal_type=req.meal_type)[:2]

    add_step(
        "recipe_selection",
        f"Matched {len(recipes)} Nordic recipe(s) and {len(active_rules)} active promotion rule(s)",
        {
            "recipe_ids": [r.id for r in recipes],
            "active_promotion_titles": [r.title for r in active_rules],
        },
    )

    candidate_products = _fetch_candidate_products(db, aimo_client, recipes) if recipes else []
    diet_safe_candidates = [
        p for p in candidate_products if (not diet_keys or product_passes_diets(p, diet_keys)[0])
    ]

    # Gather candidate products per (recipe, ingredient) so we can batch live checks
    ingredient_candidates: list[tuple[ValioRecipe, RecipeIngredientSpec, list[Product]]] = []
    candidate_ids: list[str] = []
    seen_cand_ids: set[str] = set()
    product_map: dict[str, Product] = {p.id: p for p in candidate_products}

    for recipe in recipes:
        for spec in recipe.ingredients:
            pool = _candidate_pool_for_ingredient(diet_safe_candidates, spec)
            if pool:
                ingredient_candidates.append((recipe, spec, pool))
                for p in pool:
                    if p.id not in seen_cand_ids:
                        seen_cand_ids.add(p.id)
                        candidate_ids.append(p.id)

    # Fallback for sparse synthetic DBs or include_valio_recipes=False (bounded to 40 rows)
    if not ingredient_candidates:
        fallback_rows = list(db.scalars(select(Product).order_by(Product.id.asc()).limit(40)).all())
        fallback_safe = [
            p for p in fallback_rows if (not diet_keys or product_passes_diets(p, diet_keys)[0])
        ][:4]
        if fallback_safe:
            synth_recipe = ValioRecipe(
                id="chef_curated_selection",
                name="Chef's Diet-Safe Nordic Selection",
                meal_type=req.meal_type or "lunch",
                diet_tags=tuple(diet_keys),
                ingredients=(),
                chef_note="Curated diet-safe selection from available catalog items.",
            )
            recipes = [synth_recipe]
            for idx, prod in enumerate(fallback_safe):
                product_map[prod.id] = prod
                synth_spec = RecipeIngredientSpec(
                    role=f"item_{idx + 1}",
                    label=prod.name,
                    preferred_product_ids=(prod.id,),
                    search_keywords=(),
                    category_prefixes=(),
                    qty_per_10_pax=1.0,
                    unit_hint=prod.sales_unit or "ST",
                )
                ingredient_candidates.append((synth_recipe, synth_spec, [prod]))
                if prod.id not in seen_cand_ids:
                    seen_cand_ids.add(prod.id)
                    candidate_ids.append(prod.id)

    warnings: list[str] = []
    live_lines: dict[str, QuoteLine] = {}
    list_prices: dict[str, float] = {}

    if candidate_ids:
        valid_ids = [pid for pid in candidate_ids if pid in product_map]
        if valid_ids:
            checked = check_products_live(
                db=db,
                aimo_client=aimo_client,
                product_ids=valid_ids,
                quantities={pid: 1.0 for pid in valid_ids},
                requested_delivery_date=delivery_date,
                customer_id=customer_id,
            )
            live_lines = {line.product_id: line for line in checked}
            list_prices = extract_list_prices(
                aimo_client,
                valid_ids,
                requested_delivery_date=delivery_date,
                customer_id=customer_id,
            )

    selected_items: list[MenuIngredientSelectionOut] = []
    promotions_applied_set: set[str] = set()
    used_recipes: dict[str, ValioRecipe] = {}
    from app.services.admin_ranking import (
        evaluate_aimo_campaign_score,
        evaluate_brand_preference_score,
        get_active_aimo_campaigns,
        get_active_brand_preferences,
    )

    active_brand_prefs = get_active_brand_preferences(db, principal.company_id)
    active_aimo_campaigns = get_active_aimo_campaigns(db)

    for recipe, spec, pool in ingredient_candidates:
        best_choice: (
            tuple[
                tuple[float, float, float, float],
                Product,
                QuoteLine,
                float | None,
                bool,
                float,
                float,
                bool,
                list[str],
            ]
            | None
        ) = None

        for idx, prod in enumerate(pool):
            base_line = live_lines.get(prod.id)
            if base_line is None:
                continue
            deliverable_score = (
                1.0
                if base_line.delivery_status in {"ON_TIME", "LATE"}
                and "NO_PRICE" not in base_line.flags
                and "SALES_BLOCKED" not in base_line.flags
                else 0.0
            )
            list_price = list_prices.get(prod.id)
            on_sale, discount_pct, savings_unit = compute_discount_metrics(base_line, list_price)
            promoted, promo_reasons, promo_score = evaluate_product_promotions(
                product_id=prod.id,
                brand=prod.brand,
                category=prod.category,
                rules=active_rules,
                on_sale=on_sale,
            )
            brand_pref_score = evaluate_brand_preference_score(
                brand=prod.brand,
                category=prod.category,
                preferences=active_brand_prefs,
            )
            aimo_campaign_score = evaluate_aimo_campaign_score(
                product_id=prod.id,
                brand=prod.brand,
                category=prod.category,
                campaigns=active_aimo_campaigns,
            )
            valio_bonus = (
                0.25 if (prod.brand or "").strip().lower() in {"valio", "oddlygood"} else 0.0
            )
            deal_score = (discount_pct / 20.0) if (req.prefer_on_sale and on_sale) else 0.0
            spec_preference = (len(pool) - idx) * 0.05
            rank = (
                deliverable_score,
                deal_score
                + promo_score
                + brand_pref_score
                + (aimo_campaign_score * 0.35)
                + valio_bonus
                + spec_preference,
                1.0 if base_line.unit_price_eur is not None else 0.0,
                -(base_line.unit_price_eur or 9999.0),
            )
            if best_choice is None or rank > best_choice[0]:
                best_choice = (
                    rank,
                    prod,
                    base_line,
                    list_price,
                    on_sale,
                    discount_pct,
                    savings_unit,
                    promoted,
                    promo_reasons,
                )

        if best_choice is None:
            warnings.append(f"No live-checked product found for {recipe.name}: {spec.label}")
            continue

        (
            _rank,
            prod,
            base_line,
            list_price,
            on_sale,
            discount_pct,
            savings_unit,
            promoted,
            promo_reasons,
        ) = best_choice

        qty = _compute_quantity(spec, req.pax, prod.allowed_lot_size)
        unit_ex = base_line.unit_price_eur
        scaled_line = base_line.model_copy(
            update={
                "quantity": qty,
                "line_total_eur": round(unit_ex * qty, 2) if unit_ex is not None else None,
                "co2_kg": round(base_line.co2_kg * qty, 4)
                if base_line.co2_kg is not None
                else None,
            }
        )
        savings_eur = round(savings_unit * qty, 2)
        if promoted:
            promotions_applied_set.update(promo_reasons)

        reason = _format_selection_reason(
            product=prod,
            on_sale=on_sale,
            discount_pct=discount_pct,
            promoted=promoted,
            promotion_reasons=promo_reasons,
            diet_keys=diet_keys,
        )
        used_recipes[recipe.id] = recipe
        selected_items.append(
            MenuIngredientSelectionOut(
                recipe_id=recipe.id,
                recipe_name=recipe.name,
                ingredient_label=spec.label,
                product=product_to_out(prod),
                quote_line=scaled_line,
                list_price_ex_vat=round(list_price, 2) if list_price is not None else None,
                discount_pct=discount_pct,
                savings_eur=savings_eur,
                promoted=promoted,
                promotion_reasons=promo_reasons,
                selection_reason=reason,
            )
        )

    # Merge duplicate SKUs into clean quote_draft_lines
    draft_qty_by_pid: dict[str, float] = {}
    for item in selected_items:
        pid = item.product.id
        draft_qty_by_pid[pid] = draft_qty_by_pid.get(pid, 0.0) + item.quote_line.quantity
    quote_draft_lines = [
        QuoteLineInput(product_id=pid, quantity=qty) for pid, qty in draft_qty_by_pid.items()
    ]

    # Second live check at actual scaled pax quantities so finite-stock SKUs get PARTIAL / AT_RISK
    policy_eval_lines: list[QuoteLine] = []
    if draft_qty_by_pid:
        scaled_checked = check_products_live(
            db=db,
            aimo_client=aimo_client,
            product_ids=list(draft_qty_by_pid.keys()),
            quantities=draft_qty_by_pid,
            requested_delivery_date=delivery_date,
            customer_id=customer_id,
        )
        has_explicit_user_date = bool(
            req.requested_delivery_date and req.requested_delivery_date.strip()
        )
        if not has_explicit_user_date and any(
            ln.delivery_status != "NOT_DELIVERABLE"
            and (
                (ln.available_date and ln.available_date > delivery_date)
                or ln.delivery_status == "LATE"
                or "LATE" in ln.flags
            )
            for ln in scaled_checked
        ):
            advanced_date = compute_last_available_delivery_date(delivery_date, scaled_checked)
            if advanced_date > delivery_date:
                delivery_date = advanced_date
                scaled_checked = check_products_live(
                    db=db,
                    aimo_client=aimo_client,
                    product_ids=list(draft_qty_by_pid.keys()),
                    quantities=draft_qty_by_pid,
                    requested_delivery_date=delivery_date,
                    customer_id=customer_id,
                )
        policy_eval_lines = list(scaled_checked)
        scaled_by_pid = {ln.product_id: ln for ln in scaled_checked}

        refreshed_items: list[MenuIngredientSelectionOut] = []
        for item in selected_items:
            fresh_ln = scaled_by_pid.get(item.product.id)
            if fresh_ln is None:
                refreshed_items.append(item)
                continue
            item_qty = item.quote_line.quantity
            if abs(fresh_ln.quantity - item_qty) <= 1e-6:
                updated_line = fresh_ln
            else:
                unit_ex = fresh_ln.unit_price_eur
                per_unit_co2 = (
                    (fresh_ln.co2_kg / fresh_ln.quantity)
                    if (fresh_ln.co2_kg is not None and fresh_ln.quantity > 0)
                    else None
                )
                updated_line = fresh_ln.model_copy(
                    update={
                        "quantity": item_qty,
                        "line_total_eur": round(unit_ex * item_qty, 2)
                        if unit_ex is not None
                        else None,
                        "co2_kg": round(per_unit_co2 * item_qty, 3)
                        if per_unit_co2 is not None
                        else None,
                    }
                )
            refreshed_items.append(item.model_copy(update={"quote_line": updated_line}))
        selected_items = refreshed_items

        for ln in scaled_checked:
            if (
                ln.delivery_status == "NOT_DELIVERABLE"
                or "NO_PRICE" in ln.flags
                or "SALES_BLOCKED" in ln.flags
            ):
                warnings.append(
                    f"{ln.product_name} ({ln.product_id}): NOT_DELIVERABLE on {delivery_date}."
                )
            elif "PARTIAL" in ln.flags:
                avail_txt = (
                    f"{ln.available_amount:g}" if ln.available_amount is not None else "limited"
                )
                warnings.append(
                    f"{ln.product_name} ({ln.product_id}): PARTIAL stock "
                    f"(available {avail_txt} {ln.sales_unit} vs requested {ln.quantity:g})."
                )
            elif ln.delivery_status == "LATE" or "CUTOFF_PASSED" in ln.flags:
                warnings.append(
                    f"{ln.product_name} ({ln.product_id}): delivery is {ln.delivery_status} "
                    f"({', '.join(ln.flags) or 'schedule risk'})."
                )
            if ln.co2_kg is None:
                warnings.append(f"{ln.product_name} ({ln.product_id}): CO2 footprint unavailable.")

    est_ex = round(
        sum(
            i.quote_line.line_total_eur
            for i in selected_items
            if i.quote_line.line_total_eur is not None
        ),
        2,
    )
    est_inc = round(
        sum(
            round(i.quote_line.line_total_eur * (1.0 + (i.quote_line.tax_rate or 0.0) / 100.0), 2)
            for i in selected_items
            if i.quote_line.line_total_eur is not None
        ),
        2,
    )
    est_savings = round(sum(i.savings_eur for i in selected_items), 2)
    est_list_ex = round(est_ex + est_savings, 2)

    co2_values = [i.quote_line.co2_kg for i in selected_items if i.quote_line.co2_kg is not None]
    estimated_co2 = round(sum(co2_values), 4) if co2_values else None

    budget_exceeded = bool(req.max_budget_eur is not None and est_ex > req.max_budget_eur)
    if budget_exceeded and req.max_budget_eur is not None:
        warnings.append(
            f"Estimated menu total {est_ex:.2f} EUR exceeds budget {req.max_budget_eur:.2f} EUR."
        )

    add_step(
        "ingredient_resolution",
        (
            f"Resolved {len(selected_items)} ingredient line(s) across "
            f"{len(used_recipes)} recipe(s); estimated total {est_ex:.2f} EUR excl. VAT "
            f"(savings {est_savings:.2f} EUR)"
        ),
        {
            "selected_product_ids": [i.product.id for i in selected_items],
            "on_sale_count": sum(1 for i in selected_items if i.discount_pct > 0),
            "promoted_count": sum(1 for i in selected_items if i.promoted),
            "estimated_total_excl_vat_eur": est_ex,
            "estimated_savings_eur": est_savings,
            "budget_exceeded": budget_exceeded,
        },
    )

    agent: Agent | None = None
    spent_today_eur = 0.0
    if principal.kind == "agent":
        agent = db.get(Agent, principal.id)
        spent_today_eur = compute_agent_spent_today(db, principal.id, principal.company_id)

    product_categories = {i.product.id: i.product.category for i in selected_items}
    policy_preview = evaluate_policy(
        principal=principal,
        agent=agent,
        lines=policy_eval_lines or [i.quote_line for i in selected_items],
        product_categories=product_categories,
        total_excl_vat_eur=est_ex,
        spent_today_eur=spent_today_eur,
        diet_violations=None,
    )
    fired_rule_ids = [r.rule_id for r in policy_preview.rules]

    add_step(
        "policy_preview",
        (
            f"Layer 1 policy preview: {policy_preview.outcome} "
            f"(fired rules: {', '.join(fired_rule_ids) or 'none'})"
        ),
        {
            "outcome": policy_preview.outcome,
            "fired_rules": fired_rule_ids,
            "messages": [r.message for r in policy_preview.rules],
        },
    )

    plan_id = f"mpl_{uuid4().hex[:12]}"
    record_audit(
        db,
        company_id=principal.company_id,
        actor=principal,
        action="menu.plan",
        entity_type="menu_plan",
        entity_id=plan_id,
        outcome="ok",
        details={
            "request_id": request_id_var.get() or None,
            "pax": req.pax,
            "meal_type": req.meal_type,
            "requested_delivery_date": delivery_date,
            "diet": req.diet,
            "recipe_ids": list(used_recipes.keys()),
            "estimated_total_excl_vat_eur": est_ex,
            "estimated_savings_eur": est_savings,
            "promotions_applied": sorted(promotions_applied_set),
            "policy_outcome": policy_preview.outcome,
            "fired_rules": fired_rule_ids,
            "reasoning_steps": [s.model_dump(mode="json") for s in steps],
        },
    )
    db.commit()

    return MenuPlanResponse(
        plan_id=plan_id,
        pax=req.pax,
        meal_type=req.meal_type,
        requested_delivery_date=delivery_date,
        diet=req.diet,
        recipes=[
            MenuRecipeSummaryOut(
                id=r.id,
                name=r.name,
                meal_type=r.meal_type,
                diet_tags=list(r.diet_tags),
                chef_note=r.chef_note,
            )
            for r in used_recipes.values()
        ],
        items=selected_items,
        quote_draft_lines=quote_draft_lines,
        estimated_total_excl_vat_eur=est_ex,
        estimated_total_incl_vat_eur=est_inc,
        estimated_list_total_excl_vat_eur=est_list_ex,
        estimated_savings_eur=est_savings,
        estimated_co2_kg=estimated_co2,
        budget_exceeded=budget_exceeded,
        policy_preview=policy_preview,
        promotions_applied=sorted(promotions_applied_set),
        warnings=warnings,
        reasoning_steps=steps,
    )
