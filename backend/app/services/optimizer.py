"""Pre-Flight Cart Optimizer (POST /api/quotes/optimize & MCP optimize_quote)."""

from __future__ import annotations

import math
from typing import cast

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.aimo.client import AimoClient
from app.aimo.normalise import check_products_live
from app.audit import record_audit
from app.auth import compute_agent_spent_today
from app.errors import AppError, get_request_id
from app.llm.decisions import (
    DecisionProvider,
    get_decision_provider,
    is_culinary_substitute_fit,
)
from app.models import Agent, Product
from app.schemas import (
    DeliveryStatus,
    LineFlag,
    LineOptimizationSwap,
    OptimizeDeltas,
    OptimizeObjective,
    OptimizeSummary,
    Principal,
    QuoteLine,
    QuoteOptimizeRequest,
    QuoteOptimizeResponse,
)
from app.services.catalog import (
    product_passes_diets,
    product_to_out,
    resolve_diet_keys,
)
from app.services.co2 import compute_line_co2, estimate_kg_per_sales_unit
from app.services.deals import fixture_priced_ids
from app.services.delivery import compute_delivery_status
from app.services.policy import evaluate_policy
from app.services.quotes import (
    DELIVERY_SEVERITY,
    evaluate_diet_violations,
    worst_delivery_status,
)
from app.services.substitutes import _format_temperature

MAX_LIVE_CHECK_IDS = 50
_INFERRED_DIETS = ("LACTOSE_FREE", "MILK_FREE", "GLUTEN_FREE_COELIAC", "VEGAN")
_PLANT_CATEGORY_PREFIXES = ("10402", "10406", "10413")
_NON_DRINK_PLANT_TOKENS = ("soijavalmiste", "jogurtti", "vanukas", "rahka", "dessert", "yogurt")


def _is_plant_based_dairy_alt(product: Product) -> bool:
    cat = product.category or ""
    if any(cat.startswith(pref) for pref in _PLANT_CATEGORY_PREFIXES):
        return True
    if not cat.startswith("104"):
        return False
    tags = {str(x).strip().upper() for x in [*(product.claims or []), *(product.labels or [])]}
    return bool(tags & {"VEGAN", "VEGAANINEN"})


def _is_plant_culinary_match(orig: Product, cand: Product) -> bool:
    orig_txt = f"{orig.name or ''} {orig.name_en or ''}".lower()
    cand_txt = f"{cand.name or ''} {cand.name_en or ''}".lower()
    if any(bad in cand_txt for bad in _NON_DRINK_PLANT_TOKENS):
        return False
    orig_is_cream = any(k in orig_txt for k in ("kerma", "cream", "cooking"))
    cand_is_cream = any(k in cand_txt for k in ("kerma", "cream", "cooking"))
    if orig_is_cream != cand_is_cream:
        return False
    if not orig_is_cream:
        return any(k in cand_txt for k in ("juoma", "drink", "oat", "kaura", "soy", "soija"))
    return True


def _round_up_to_lot(qty: float, allowed_lot_size: float | None, sales_unit: str) -> float:
    if qty <= 0:
        return 1.0
    if allowed_lot_size is not None and float(allowed_lot_size) > 1.0:
        lot = float(allowed_lot_size)
        return round(math.ceil((qty - 1e-9) / lot) * lot, 4)
    if sales_unit == "KG":
        return round(qty, 3)
    return float(max(1, math.ceil(qty - 1e-9)))


def _compute_normalized_quantity(
    orig: Product,
    orig_qty: float,
    cand: Product,
) -> float | None:
    """Compute mass/volume-equivalent suggested quantity in cand.sales_unit, or None if unsafe."""
    orig_kg, _, _ = estimate_kg_per_sales_unit(orig, None)
    cand_kg, _, _ = estimate_kg_per_sales_unit(cand, None)

    if orig_kg is not None and orig_kg > 0 and cand_kg is not None and cand_kg > 0:
        raw_cand_qty = (float(orig_qty) * orig_kg) / cand_kg
        return _round_up_to_lot(raw_cand_qty, cand.allowed_lot_size, cand.sales_unit)

    if orig.sales_unit == cand.sales_unit and orig_kg is None and cand_kg is None:
        return _round_up_to_lot(float(orig_qty), cand.allowed_lot_size, cand.sales_unit)

    # Cross-unit without verified unit-mass equivalence is rejected to prevent bogus savings
    return None


def _scale_checked_line(
    base_line: QuoteLine,
    product: Product,
    quantity: float,
) -> QuoteLine:
    """Return a QuoteLine recomputed accurately for `quantity` from a live-checked base line."""
    qty = float(quantity)
    if abs(float(base_line.quantity) - qty) <= 1e-6:
        return base_line

    flags_set: list[str] = [f for f in base_line.flags if f != "PARTIAL"]
    if base_line.available_qty_state == "ZERO" and qty > 0:
        flags_set.append("PARTIAL")
    elif base_line.available_qty_state == "FINITE":
        if base_line.available_amount is None or base_line.available_amount < qty - 1e-9:
            flags_set.append("PARTIAL")

    typed_flags = [cast(LineFlag, f) for f in dict.fromkeys(flags_set)]
    delivery_status, on_time_probability = compute_delivery_status(
        typed_flags,
        base_line.logistic_model,
        product.delivery_reliability,
    )

    line_total_eur = (
        round(float(base_line.unit_price_eur) * qty, 2)
        if base_line.unit_price_eur is not None
        else None
    )
    if base_line.co2_kg is not None and float(base_line.quantity) > 0:
        ratio = qty / float(base_line.quantity)
        co2_kg: float | None = round(float(base_line.co2_kg) * ratio, 3)
        if base_line.co2_basis and " × " in base_line.co2_basis:
            prefix = base_line.co2_basis.rsplit(" × ", 1)[0]
            co2_basis: str | None = f"{prefix} × {qty:g} {product.sales_unit}"
        else:
            co2_basis = base_line.co2_basis
    else:
        co2_kg, co2_basis, _ = compute_line_co2(product, None, qty)

    return base_line.model_copy(
        update={
            "quantity": qty,
            "line_total_eur": line_total_eur,
            "delivery_status": delivery_status,
            "on_time_probability": on_time_probability,
            "co2_kg": co2_kg,
            "co2_basis": co2_basis,
            "flags": list(dict.fromkeys(typed_flags)),
        }
    )


def _score_candidate(
    objective: OptimizeObjective,
    orig_line: QuoteLine,
    cand_line: QuoteLine,
) -> float:
    eur_save = float(orig_line.line_total_eur or 0.0) - float(cand_line.line_total_eur or 0.0)
    rel_gain = float(cand_line.on_time_probability or 0.0) - float(
        orig_line.on_time_probability or 0.0
    )
    status_gain = (
        DELIVERY_SEVERITY[orig_line.delivery_status] - DELIVERY_SEVERITY[cand_line.delivery_status]
    )
    co2_save = (
        float(orig_line.co2_kg) - float(cand_line.co2_kg)
        if orig_line.co2_kg is not None and cand_line.co2_kg is not None
        else 0.0
    )

    if objective == "SAVE_MONEY":
        return eur_save + status_gain * 5.0 if eur_save > 0.005 else 0.0

    if objective == "MAX_RELIABILITY":
        if status_gain > 0:
            return status_gain * 100.0 + rel_gain * 50.0 + max(0.0, eur_save)
        return rel_gain * 100.0 if rel_gain > 0.005 else 0.0

    if objective == "LOW_CARBON":
        return co2_save * 10.0 + max(0.0, status_gain * 20.0) if co2_save > 0.05 else 0.0

    # BALANCED
    orig_eur = float(orig_line.line_total_eur or 0.0)
    norm_cost = max(-0.5, eur_save / max(1.0, orig_eur)) if orig_eur > 0 else 0.0
    norm_rel = rel_gain + status_gain * 0.4
    orig_co2 = float(orig_line.co2_kg or 0.0)
    norm_co2 = co2_save / max(1.0, orig_co2) if orig_co2 > 0 else 0.0
    score = 0.4 * norm_cost + 0.35 * norm_rel + 0.25 * norm_co2
    return score if score > 0.02 else 0.0


def _build_summary(
    *,
    principal: Principal,
    agent: Agent | None,
    spent_today_eur: float,
    lines: list[QuoteLine],
    product_map: dict[str, Product],
    diet_param: str | None,
) -> OptimizeSummary:
    total_excl_vat = round(
        sum(float(ln.line_total_eur) for ln in lines if ln.line_total_eur is not None),
        2,
    )
    probs = [float(ln.on_time_probability) for ln in lines if ln.on_time_probability is not None]
    min_prob = round(min(probs), 4) if probs else None
    co2_lines = [float(ln.co2_kg) for ln in lines if ln.co2_kg is not None]
    est_co2 = round(sum(co2_lines), 3) if co2_lines else None
    overall: DeliveryStatus = (
        worst_delivery_status([ln.delivery_status for ln in lines]) if lines else "ON_TIME"
    )
    categories = {pid: prod.category for pid, prod in product_map.items()}
    diet_violations = evaluate_diet_violations(product_map, lines, diet_param)
    policy_preview = evaluate_policy(
        principal=principal,
        agent=agent,
        lines=lines,
        product_categories=categories,
        total_excl_vat_eur=total_excl_vat,
        spent_today_eur=spent_today_eur,
        diet_violations=diet_violations or None,
    )
    return OptimizeSummary(
        total_excl_vat_eur=total_excl_vat,
        min_on_time_probability=min_prob,
        estimated_co2_kg=est_co2,
        co2_covered_lines=len(co2_lines),
        total_lines=len(lines),
        overall_delivery_status=overall,
        policy_preview=policy_preview,
    )


def optimize_quote(
    db: Session,
    aimo_client: AimoClient,
    principal: Principal,
    req: QuoteOptimizeRequest,
    *,
    decision_provider: DecisionProvider | None = None,
) -> QuoteOptimizeResponse:
    """Preview diet-safe, unit-normalized line swaps under a strict <=50-SKU live check budget."""
    explicit_diets = resolve_diet_keys(req.diet, None) if req.diet and req.diet.strip() else []
    cleaned_diet = req.diet.strip() if req.diet and req.diet.strip() else None

    orig_ids_ordered: list[str] = list(dict.fromkeys(ln.product_id for ln in req.lines))
    orig_products = db.scalars(select(Product).where(Product.id.in_(orig_ids_ordered))).all()
    product_map: dict[str, Product] = {p.id: p for p in orig_products}

    missing = [pid for pid in orig_ids_ordered if pid not in product_map]
    if missing:
        raise AppError(
            "NOT_FOUND",
            404,
            f"Unknown product_id(s): {', '.join(missing)}",
            details={"missing_product_ids": missing},
        )

    fixture_ids = fixture_priced_ids(aimo_client)
    resolved_decision = decision_provider or get_decision_provider()

    checked_id_list: list[str] = list(orig_ids_ordered)
    checked_id_set: set[str] = set(orig_ids_ordered)
    qty_by_id: dict[str, float] = {}
    for ln in req.lines:
        qty_by_id[ln.product_id] = max(qty_by_id.get(ln.product_id, 0.0), float(ln.quantity))

    line_candidates: list[list[tuple[Product, float]]] = []

    for ln in req.lines:
        orig_prod = product_map[ln.product_id]
        orig_qty = float(ln.quantity)
        orig_is_plant = _is_plant_based_dairy_alt(orig_prod)

        inferred_diets = [dk for dk in _INFERRED_DIETS if product_passes_diets(orig_prod, [dk])[0]]
        required_diets = list(dict.fromkeys([*explicit_diets, *inferred_diets]))

        explicit_sub_ids = [
            str(sid) for sid in (orig_prod.substitutions or []) if str(sid) != orig_prod.id
        ]
        explicit_sub_set = set(explicit_sub_ids)

        pool_rows: list[Product] = []
        seen_pool: set[str] = {orig_prod.id}

        if explicit_sub_ids:
            for p in db.scalars(select(Product).where(Product.id.in_(explicit_sub_ids))).all():
                if p.id not in seen_pool:
                    seen_pool.add(p.id)
                    pool_rows.append(p)

        same_cat = db.scalars(
            select(Product)
            .where(Product.category == orig_prod.category, Product.id != orig_prod.id)
            .order_by(Product.id.asc())
            .limit(80)
        ).all()
        for p in same_cat:
            if p.id not in seen_pool:
                seen_pool.add(p.id)
                pool_rows.append(p)

        if (
            req.allow_plant_based_swaps
            and req.objective in ("LOW_CARBON", "BALANCED")
            and orig_prod.category.startswith("104")
            and not orig_is_plant
        ):
            plant_rows = db.scalars(
                select(Product)
                .where(Product.category.like("104%"), Product.id != orig_prod.id)
                .order_by(Product.id.asc())
                .limit(120)
            ).all()
            for p in plant_rows:
                if p.id not in seen_pool and _is_plant_based_dairy_alt(p):
                    seen_pool.add(p.id)
                    pool_rows.append(p)

        pool_rows.sort(
            key=lambda p: (
                0 if p.id in explicit_sub_set else 1,
                0 if (not fixture_ids or p.id in fixture_ids) else 1,
                0
                if ("valio" in (p.brand or "").lower() or "oddlygood" in (p.brand or "").lower())
                else 1,
                0 if (p.delivery_reliability is not None and p.co2_kg_per_kg is not None) else 1,
                -(float(p.delivery_reliability) if p.delivery_reliability is not None else 0.0),
                p.id,
            )
        )

        selected_for_line: list[tuple[Product, float]] = []
        for cand in pool_rows:
            cand_is_plant = _is_plant_based_dairy_alt(cand)
            cross_plant = (
                req.allow_plant_based_swaps
                and not orig_is_plant
                and cand_is_plant
                and orig_prod.category.startswith("104")
            )
            if not req.allow_plant_based_swaps and not orig_is_plant and cand_is_plant:
                continue
            if cand.category != orig_prod.category and not cross_plant:
                continue

            # Strict PASS-only diet check (including explicit G gluten-free on oat drinks!)
            passed_diet, _ = product_passes_diets(cand, required_diets)
            if not passed_diet:
                continue

            if cross_plant:
                if not _is_plant_culinary_match(orig_prod, cand):
                    continue
            else:
                if not is_culinary_substitute_fit(
                    orig_prod, cand, provider=resolved_decision
                ).answer:
                    continue

            sug_qty = _compute_normalized_quantity(orig_prod, orig_qty, cand)
            if sug_qty is None:
                continue

            if cand.id not in checked_id_set:
                if len(checked_id_set) >= MAX_LIVE_CHECK_IDS:
                    continue
                checked_id_set.add(cand.id)
                checked_id_list.append(cand.id)

            product_map[cand.id] = cand
            qty_by_id[cand.id] = max(qty_by_id.get(cand.id, 0.0), sug_qty)
            selected_for_line.append((cand, sug_qty))
            if len(selected_for_line) >= 6:
                break

        line_candidates.append(selected_for_line)

    assert len(checked_id_list) <= MAX_LIVE_CHECK_IDS
    checked_lines = check_products_live(
        db=db,
        aimo_client=aimo_client,
        product_ids=checked_id_list,
        quantities=qty_by_id,
        requested_delivery_date=req.requested_delivery_date,
        customer_id=principal.company_id,
    )
    base_line_by_id: dict[str, QuoteLine] = {ln.product_id: ln for ln in checked_lines}

    orig_lines: list[QuoteLine] = [
        _scale_checked_line(
            base_line_by_id[ln.product_id], product_map[ln.product_id], float(ln.quantity)
        )
        for ln in req.lines
    ]
    after_lines: list[QuoteLine] = list(orig_lines)
    swaps: list[LineOptimizationSwap] = []
    unchanged_lines: list[int] = []

    for idx, (inp, orig_line, cands) in enumerate(
        zip(req.lines, orig_lines, line_candidates, strict=True)
    ):
        orig_prod = product_map[inp.product_id]
        best_choice: tuple[Product, QuoteLine, float, float] | None = None

        for cand_prod, sug_qty in cands:
            cand_line = _scale_checked_line(base_line_by_id[cand_prod.id], cand_prod, sug_qty)
            if cand_line.unit_price_eur is None or cand_line.delivery_status == "NOT_DELIVERABLE":
                continue
            if "SALES_BLOCKED" in cand_line.flags or "NO_PRICE" in cand_line.flags:
                continue
            if (
                DELIVERY_SEVERITY[cand_line.delivery_status]
                > DELIVERY_SEVERITY[orig_line.delivery_status]
            ):
                continue
            if req.objective == "SAVE_MONEY" and cand_line.delivery_status != "ON_TIME":
                continue
            if (
                req.objective == "LOW_CARBON"
                and orig_line.delivery_status == "ON_TIME"
                and cand_line.delivery_status != "ON_TIME"
            ):
                continue

            score = _score_candidate(req.objective, orig_line, cand_line)
            if score > 0 and (best_choice is None or score > best_choice[3]):
                best_choice = (cand_prod, cand_line, sug_qty, score)

        if best_choice is None:
            unchanged_lines.append(idx)
            continue

        best_prod, best_line, best_qty, _ = best_choice
        after_lines[idx] = best_line

        tradeoffs: list[str] = []
        orig_temp = _format_temperature(orig_prod)
        cand_temp = _format_temperature(best_prod)
        tradeoffs.append(
            f"Storage: {cand_temp}"
            if orig_temp == cand_temp
            else f"Storage: {cand_temp} vs {orig_temp}"
        )
        qty_changed = abs(best_qty - float(inp.quantity)) > 1e-6
        if best_prod.sales_unit != orig_prod.sales_unit or qty_changed:
            tradeoffs.append(
                f"Pack equivalence: {best_qty:g} {best_prod.sales_unit} replaces "
                f"{float(inp.quantity):g} {orig_prod.sales_unit}"
            )
        if orig_line.on_time_probability is not None and best_line.on_time_probability is not None:
            tradeoffs.append(
                f"Reliability {best_line.on_time_probability * 100:.0f}% vs "
                f"{orig_line.on_time_probability * 100:.0f}%"
            )
        same_group_co2 = (
            best_prod.co2_kg_per_kg is not None
            and orig_prod.co2_kg_per_kg is not None
            and abs(float(best_prod.co2_kg_per_kg) - float(orig_prod.co2_kg_per_kg)) < 1e-6
        )
        orig_cat_kg, _, _ = estimate_kg_per_sales_unit(orig_prod, None)
        best_cat_kg, _, _ = estimate_kg_per_sales_unit(best_prod, None)
        orig_mass = (orig_cat_kg or 0.0) * float(inp.quantity)
        best_mass = (best_cat_kg or 0.0) * best_qty
        equal_catalog_mass = (
            orig_cat_kg is not None
            and best_cat_kg is not None
            and abs(best_mass - orig_mass) <= 0.05 * max(orig_mass, 1.0)
        )
        if same_group_co2 and equal_catalog_mass:
            tradeoffs.append("CO2e: identical product-group average (0.0 kg delta)")

        eur_delta = round(
            float(best_line.line_total_eur or 0.0) - float(orig_line.line_total_eur or 0.0),
            2,
        )
        rel_delta = (
            round(
                float(best_line.on_time_probability) - float(orig_line.on_time_probability),
                4,
            )
            if best_line.on_time_probability is not None
            and orig_line.on_time_probability is not None
            else None
        )
        if (
            same_group_co2
            and equal_catalog_mass
            and best_line.co2_kg is not None
            and orig_line.co2_kg is not None
        ):
            co2_delta: float | None = 0.0
        elif best_line.co2_kg is not None and orig_line.co2_kg is not None:
            co2_delta = round(float(best_line.co2_kg) - float(orig_line.co2_kg), 3)
        else:
            co2_delta = None

        reasons: list[str] = []
        if (
            DELIVERY_SEVERITY[best_line.delivery_status]
            < DELIVERY_SEVERITY[orig_line.delivery_status]
        ):
            reasons.append(
                f"Upgrades delivery from {orig_line.delivery_status} to {best_line.delivery_status}"
            )
        if eur_delta < -0.005:
            reasons.append(f"Saves €{abs(eur_delta):.2f} excl. VAT")
        if co2_delta is not None and co2_delta < -0.05:
            reasons.append(f"Reduces CO2e by {abs(co2_delta):.2f} kg")
        if rel_delta is not None and rel_delta > 0.005 and not reasons:
            reasons.append(f"Improves reliability (+{rel_delta * 100:.1f} pp)")

        swaps.append(
            LineOptimizationSwap(
                line_index=idx,
                original_line=orig_line,
                recommended_product=product_to_out(best_prod),
                recommended_line=best_line,
                suggested_quantity=best_qty,
                line_total_delta_eur=eur_delta,
                reliability_delta=rel_delta,
                co2_delta_kg=co2_delta,
                co2_basis_note=best_line.co2_basis or orig_line.co2_basis,
                tradeoffs=tradeoffs,
                reason=" · ".join(reasons) or f"Better match for {req.objective}",
            )
        )

    agent: Agent | None = None
    spent_today_eur = 0.0
    if principal.kind == "agent":
        agent = db.get(Agent, principal.id)
        spent_today_eur = compute_agent_spent_today(db, principal.id, principal.company_id)

    before_summary = _build_summary(
        principal=principal,
        agent=agent,
        spent_today_eur=spent_today_eur,
        lines=orig_lines,
        product_map=product_map,
        diet_param=cleaned_diet,
    )
    after_summary = _build_summary(
        principal=principal,
        agent=agent,
        spent_today_eur=spent_today_eur,
        lines=after_lines,
        product_map=product_map,
        diet_param=cleaned_diet,
    )

    swap_by_index = {s.line_index: s for s in swaps}
    comp_co2_lines = 0
    comp_co2_delta = 0.0
    for idx, (before_ln, after_ln) in enumerate(zip(orig_lines, after_lines, strict=True)):
        if before_ln.co2_kg is not None and after_ln.co2_kg is not None:
            comp_co2_lines += 1
            if idx in swap_by_index and swap_by_index[idx].co2_delta_kg is not None:
                comp_co2_delta += float(swap_by_index[idx].co2_delta_kg)
            else:
                comp_co2_delta += float(after_ln.co2_kg) - float(before_ln.co2_kg)

    deltas = OptimizeDeltas(
        total_excl_vat_delta_eur=round(
            float(after_summary.total_excl_vat_eur) - float(before_summary.total_excl_vat_eur),
            2,
        ),
        min_reliability_delta=(
            round(
                float(after_summary.min_on_time_probability)
                - float(before_summary.min_on_time_probability),
                4,
            )
            if after_summary.min_on_time_probability is not None
            and before_summary.min_on_time_probability is not None
            else None
        ),
        comparable_co2_delta_kg=round(comp_co2_delta, 3) if comp_co2_lines > 0 else None,
        comparable_co2_lines=comp_co2_lines,
    )

    record_audit(
        db,
        company_id=principal.company_id,
        actor=principal,
        action="quote.optimize",
        entity_type="quote",
        entity_id="preview",
        outcome="ok",
        details={
            "request_id": get_request_id(),
            "objective": req.objective,
            "allow_plant_based_swaps": req.allow_plant_based_swaps,
            "swaps_found": len(swaps),
            "checked_ids_count": len(checked_id_list),
            "total_excl_vat_delta_eur": deltas.total_excl_vat_delta_eur,
            "comparable_co2_delta_kg": deltas.comparable_co2_delta_kg,
        },
    )
    db.commit()

    return QuoteOptimizeResponse(
        objective=req.objective,
        requested_delivery_date=req.requested_delivery_date,
        diet=cleaned_diet,
        before_summary=before_summary,
        after_summary=after_summary,
        deltas=deltas,
        swaps=swaps,
        unchanged_lines=unchanged_lines,
    )
