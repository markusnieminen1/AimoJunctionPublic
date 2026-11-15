"""Deterministic purchasing policy evaluation engine."""

from __future__ import annotations

from app.governance_registry import build_policy_rule
from app.models import Agent
from app.schemas import (
    PolicyDecision,
    PolicyOutcome,
    PolicyRuleOut,
    Principal,
    QuoteLine,
    RecipeSanityWarning,
)

_OUTCOME_RANK: dict[PolicyOutcome, int] = {
    "AUTO": 0,
    "APPROVAL_REQUIRED": 1,
    "BLOCKED": 2,
}

_RISK_FLAGS = frozenset({"CUTOFF_PASSED", "LATE", "PARTIAL", "UNIT_MISMATCH"})


def _most_restrictive(rules: list[PolicyRuleOut]) -> PolicyOutcome:
    if not rules:
        return "AUTO"
    return max(rules, key=lambda r: _OUTCOME_RANK[r.outcome]).outcome


def _extract_diet_product_ids(lines: list[QuoteLine], diet_violations: list[str]) -> list[str]:
    known_ids = {ln.product_id for ln in lines}
    matched: list[str] = []
    for violation in diet_violations:
        token = violation.split(":", 1)[0].split("(", 1)[0].strip()
        if token in known_ids and token not in matched:
            matched.append(token)
    return matched or [ln.product_id for ln in lines]


def evaluate_policy(
    principal: Principal,
    agent: Agent | None,
    lines: list[QuoteLine],
    product_categories: dict[str, str],
    total_excl_vat_eur: float,
    spent_today_eur: float,
    diet_violations: list[str] | None = None,
    approval_override: bool = False,
    recipe_sanity_warning: RecipeSanityWarning | None = None,
    semantic_guard_reason: str | None = None,
    standing_drift_reason: str | None = None,
    standing_drift_product_ids: list[str] | None = None,
) -> PolicyDecision:
    """Evaluate all applicable purchasing rules and return the most restrictive decision."""
    rules: list[PolicyRuleOut] = []
    total_eur = round(float(total_excl_vat_eur), 2)
    spent_eur = round(float(spent_today_eur), 2)

    # Gate 1: Hard safety rules always apply (even for humans and approval re-validation).
    if diet_violations:
        rules.append(
            build_policy_rule(
                "R-HARD-DIET",
                "BLOCKED",
                f"Dietary safety violation: {'; '.join(diet_violations)}",
                target_product_ids=_extract_diet_product_ids(lines, diet_violations),
            )
        )

    non_deliverable_ids = [
        line.product_id
        for line in lines
        if line.delivery_status == "NOT_DELIVERABLE"
        or "NO_PRICE" in line.flags
        or "SALES_BLOCKED" in line.flags
    ]
    if non_deliverable_ids:
        rules.append(
            build_policy_rule(
                "R-NOT-DELIVERABLE",
                "BLOCKED",
                (
                    "Undeliverable, sales-blocked, or unpriced product(s): "
                    f"{', '.join(non_deliverable_ids)}"
                ),
                target_product_ids=non_deliverable_ids,
            )
        )

    blocked_above = any(r.outcome == "BLOCKED" for r in rules)

    if approval_override:
        if not blocked_above:
            rules.append(
                build_policy_rule(
                    "R-USER-DIRECT",
                    "AUTO",
                    "Human approval overrides agent limit and delivery risk rules",
                )
            )
        return PolicyDecision(outcome=_most_restrictive(rules), rules=rules)

    # Gate 2: Recipe-aware over-ordering sanity check (holds >1.5x recipe yield until approved)
    if recipe_sanity_warning is not None and recipe_sanity_warning.triggered:
        offending_ids = [ol.product_id for ol in recipe_sanity_warning.offending_lines]
        rules.append(
            build_policy_rule(
                "R-RECIPE-SANITY",
                "APPROVAL_REQUIRED",
                recipe_sanity_warning.message,
                target_product_ids=offending_ids,
            )
        )

    if principal.kind == "user":
        if not blocked_above and not any(r.outcome == "APPROVAL_REQUIRED" for r in rules):
            rules.append(
                build_policy_rule(
                    "R-USER-DIRECT",
                    "AUTO",
                    "Direct human user order",
                )
            )
        return PolicyDecision(outcome=_most_restrictive(rules), rules=rules)

    # Gate 2: Semantic guard on suspicious coercive/smurfing notes from delegated agents
    if semantic_guard_reason:
        rules.append(
            build_policy_rule(
                "R-SEMANTIC-GUARD",
                "APPROVAL_REQUIRED",
                (
                    "Semantic governance firewall flagged untrusted instructions: "
                    f"{semantic_guard_reason}"
                ),
            )
        )

    # Gate 2: Standing-order drift (>25% portion/cost creep vs >=2 prior-day PLACED orders)
    if standing_drift_reason:
        rules.append(
            build_policy_rule(
                "R-STANDING-DRIFT",
                "APPROVAL_REQUIRED",
                standing_drift_reason,
                target_product_ids=list(standing_drift_product_ids or []),
            )
        )

    # Gate 3: Agent policy rules
    if agent is None or not agent.can_place_orders:
        rules.append(
            build_policy_rule(
                "R-AGENT-NO-ORDER",
                "BLOCKED",
                "Agent is not authorized to place orders (can_place_orders=false)",
            )
        )

    if agent is not None:
        max_order_eur = round(float(agent.max_order_value_eur), 2)
        if total_eur > max_order_eur:
            rules.append(
                build_policy_rule(
                    "R-MAX-VALUE",
                    "BLOCKED",
                    (
                        f"Order total EUR {total_eur:.2f} exceeds agent max order value "
                        f"EUR {max_order_eur:.2f}"
                    ),
                )
            )

        auto_limit_eur = round(float(agent.auto_approve_limit_eur), 2)
        if total_eur > auto_limit_eur:
            rules.append(
                build_policy_rule(
                    "R-AUTO-LIMIT",
                    "APPROVAL_REQUIRED",
                    (
                        f"Order total EUR {total_eur:.2f} exceeds auto-approve limit "
                        f"EUR {auto_limit_eur:.2f}"
                    ),
                )
            )

        daily_limit_eur = round(float(agent.daily_spend_limit_eur), 2)
        projected_daily_eur = round(spent_eur + total_eur, 2)
        if projected_daily_eur > daily_limit_eur:
            rules.append(
                build_policy_rule(
                    "R-DAILY-SPEND",
                    "APPROVAL_REQUIRED",
                    (
                        f"Projected daily spend EUR {projected_daily_eur:.2f} exceeds "
                        f"daily limit EUR {daily_limit_eur:.2f}"
                    ),
                )
            )

        allowed_prefixes = [
            str(prefix).strip()
            for prefix in (agent.allowed_categories or [])
            if str(prefix).strip()
        ]
        if allowed_prefixes:
            disallowed_products = [
                line.product_id
                for line in lines
                if not any(
                    product_categories.get(line.product_id, "").startswith(prefix)
                    for prefix in allowed_prefixes
                )
            ]
            if disallowed_products:
                rules.append(
                    build_policy_rule(
                        "R-CATEGORY",
                        "APPROVAL_REQUIRED",
                        (
                            "Product category outside allowed categories for: "
                            f"{', '.join(disallowed_products)}"
                        ),
                        target_product_ids=disallowed_products,
                    )
                )

        if agent.max_quantity_per_line is not None:
            max_qty = float(agent.max_quantity_per_line)
            oversized = [line.product_id for line in lines if float(line.quantity) > max_qty]
            if oversized:
                rules.append(
                    build_policy_rule(
                        "R-LINE-QTY",
                        "APPROVAL_REQUIRED",
                        f"Line quantity exceeds max {max_qty:g} for: {', '.join(oversized)}",
                        target_product_ids=oversized,
                    )
                )

    risky_products = [
        line.product_id
        for line in lines
        if line.delivery_status in ("LATE", "AT_RISK")
        or any(flag in _RISK_FLAGS for flag in line.flags)
    ]
    if risky_products:
        rules.append(
            build_policy_rule(
                "R-DELIVERY-RISK",
                "APPROVAL_REQUIRED",
                f"Delivery risk detected for: {', '.join(risky_products)}",
                target_product_ids=risky_products,
            )
        )

    if not rules:
        rules.append(
            build_policy_rule(
                "R-AGENT-WITHIN-LIMITS",
                "AUTO",
                "Order satisfies all agent limits and delivery checks",
            )
        )

    return PolicyDecision(outcome=_most_restrictive(rules), rules=rules)
