"""Canonical 3-Gate Governance Rule Registry for Layer 1 & Layer 2 policy decisions."""

from __future__ import annotations

from dataclasses import dataclass

from app.schemas import (
    GovernanceGateId,
    GovernanceInvariantOut,
    PolicyOutcome,
    PolicyRuleOut,
    RemediationHintOut,
)


@dataclass(frozen=True)
class GovernanceRuleSpec:
    """Single-source metadata for a deterministic purchasing governance rule."""

    rule_id: str
    default_outcome: PolicyOutcome
    gate: GovernanceGateId
    overridable_via_approval: bool
    description: str
    remediation_action_type: str | None = None
    remediation_label_en: str | None = None
    remediation_label_fi: str | None = None


GOVERNANCE_RULE_SPECS: tuple[GovernanceRuleSpec, ...] = (
    # Gate 1 — SAFETY_LOCK (Hard red lock, never overridable via approval)
    GovernanceRuleSpec(
        rule_id="R-HARD-DIET",
        default_outcome="BLOCKED",
        gate="SAFETY_LOCK",
        overridable_via_approval=False,
        description="Blocks orders violating hard Finnish diet/allergen rules (even for humans).",
        remediation_action_type="OPEN_SUBSTITUTES",
        remediation_label_en="Find diet-safe substitute",
        remediation_label_fi="Etsi ruokavalioturvallinen korvike",
    ),
    GovernanceRuleSpec(
        rule_id="R-NOT-DELIVERABLE",
        default_outcome="BLOCKED",
        gate="SAFETY_LOCK",
        overridable_via_approval=False,
        description="Blocks orders containing NOT_DELIVERABLE, NO_PRICE, or SALES_BLOCKED items.",
        remediation_action_type="REMOVE_BLOCKED_LINES",
        remediation_label_en="Replace or remove undeliverable lines",
        remediation_label_fi="Korvaa tai poista toimittamattomat rivit",
    ),
    # Gate 2 — OPERATIONAL_SANITY (Amber shield, human-reviewable via 1-tap approval/rescue)
    GovernanceRuleSpec(
        rule_id="R-DELIVERY-RISK",
        default_outcome="APPROVAL_REQUIRED",
        gate="OPERATIONAL_SANITY",
        overridable_via_approval=True,
        description=(
            "Routes agent orders with CUTOFF_PASSED, LATE, PARTIAL, or UNIT_MISMATCH to approval."
        ),
        remediation_action_type="OPEN_SUBSTITUTES",
        remediation_label_en="Swap risky lines to ON_TIME substitutes",
        remediation_label_fi="Vaihda riskirivit ajoissa toimitettaviin korvikkeisiin",
    ),
    GovernanceRuleSpec(
        rule_id="R-RECIPE-SANITY",
        default_outcome="APPROVAL_REQUIRED",
        gate="OPERATIONAL_SANITY",
        overridable_via_approval=True,
        description=(
            "Routes orders whose ingredient quantities exceed 1.5x expected recipe yield "
            "for target_pax to human review."
        ),
        remediation_action_type="ADJUST_TO_RECIPE_YIELD",
        remediation_label_en="Right-size quantities to recipe yield",
        remediation_label_fi="Sovita määrät reseptin annosmäärään",
    ),
    GovernanceRuleSpec(
        rule_id="R-STANDING-DRIFT",
        default_outcome="APPROVAL_REQUIRED",
        gate="OPERATIONAL_SANITY",
        overridable_via_approval=True,
        description=(
            "Holds recurring SKU orders when quantity or spend drifts >25% above "
            "prior historical PLACED baseline."
        ),
        remediation_action_type="APPROVE_ONCE",
        remediation_label_en="Verify recurring order drift and approve",
        remediation_label_fi="Tarkista toistuvan tilauksen poikkeama ja hyväksy",
    ),
    GovernanceRuleSpec(
        rule_id="R-SEMANTIC-GUARD",
        default_outcome="APPROVAL_REQUIRED",
        gate="OPERATIONAL_SANITY",
        overridable_via_approval=True,
        description=(
            "Holds orders carrying suspicious split-order smurfing or coercive prompt notes "
            "for human verification."
        ),
        remediation_action_type="APPROVE_ONCE",
        remediation_label_en="Verify intent and approve manually",
        remediation_label_fi="Tarkista tarkoitus ja hyväksy manuaalisesti",
    ),
    # Gate 3 — AUTONOMY_ENVELOPE (Delegated budget, category, and authority envelope)
    GovernanceRuleSpec(
        rule_id="R-USER-DIRECT",
        default_outcome="AUTO",
        gate="AUTONOMY_ENVELOPE",
        overridable_via_approval=False,
        description="Human operator orders bypass spend caps once safety & delivery pass.",
    ),
    GovernanceRuleSpec(
        rule_id="R-AGENT-NO-ORDER",
        default_outcome="BLOCKED",
        gate="AUTONOMY_ENVELOPE",
        overridable_via_approval=False,
        description="Blocks delegated agents whose can_place_orders flag is false.",
        remediation_action_type="EDIT_AGENT_LIMITS",
        remediation_label_en="Enable order authority in Agent settings",
        remediation_label_fi="Salli tilausoikeus agentin asetuksissa",
    ),
    GovernanceRuleSpec(
        rule_id="R-MAX-VALUE",
        default_outcome="BLOCKED",
        gate="AUTONOMY_ENVELOPE",
        overridable_via_approval=False,
        description="Blocks agent orders exceeding max_order_value_eur.",
        remediation_action_type="EDIT_AGENT_LIMITS",
        remediation_label_en="Place as human or adjust max_order_value_eur",
        remediation_label_fi="Tilaa ihmisenä tai muuta max_order_value_eur-rajaa",
    ),
    GovernanceRuleSpec(
        rule_id="R-AUTO-LIMIT",
        default_outcome="APPROVAL_REQUIRED",
        gate="AUTONOMY_ENVELOPE",
        overridable_via_approval=True,
        description="Routes agent orders exceeding auto_approve_limit_eur to human approval.",
        remediation_action_type="APPROVE_ONCE",
        remediation_label_en="Approve order once (content_hash verified)",
        remediation_label_fi="Hyväksy tilaus kerran (content_hash tarkistettu)",
    ),
    GovernanceRuleSpec(
        rule_id="R-DAILY-SPEND",
        default_outcome="APPROVAL_REQUIRED",
        gate="AUTONOMY_ENVELOPE",
        overridable_via_approval=True,
        description=(
            "Routes agent orders pushing daily spend over daily_spend_limit_eur to approval."
        ),
        remediation_action_type="APPROVE_ONCE",
        remediation_label_en="Approve above daily spend cap",
        remediation_label_fi="Hyväksy päiväbudjetin ylitys",
    ),
    GovernanceRuleSpec(
        rule_id="R-CATEGORY",
        default_outcome="APPROVAL_REQUIRED",
        gate="AUTONOMY_ENVELOPE",
        overridable_via_approval=True,
        description="Routes agent orders outside allowed_categories prefixes to approval.",
        remediation_action_type="APPROVE_ONCE",
        remediation_label_en="Approve out-of-category item",
        remediation_label_fi="Hyväksy sallitun kategorian ulkopuolinen tuote",
    ),
    GovernanceRuleSpec(
        rule_id="R-LINE-QTY",
        default_outcome="APPROVAL_REQUIRED",
        gate="AUTONOMY_ENVELOPE",
        overridable_via_approval=True,
        description="Routes agent orders exceeding max_quantity_per_line to approval.",
        remediation_action_type="APPROVE_ONCE",
        remediation_label_en="Approve oversized line quantity",
        remediation_label_fi="Hyväksy suuri rivimäärä",
    ),
    GovernanceRuleSpec(
        rule_id="R-AGENT-WITHIN-LIMITS",
        default_outcome="AUTO",
        gate="AUTONOMY_ENVELOPE",
        overridable_via_approval=False,
        description="Auto-submits agent orders within every limit and free of risk flags.",
    ),
)

RULE_SPECS_BY_ID: dict[str, GovernanceRuleSpec] = {
    spec.rule_id: spec for spec in GOVERNANCE_RULE_SPECS
}


def build_policy_rule(
    rule_id: str,
    outcome: PolicyOutcome,
    message: str,
    *,
    target_product_ids: list[str] | None = None,
) -> PolicyRuleOut:
    """Construct a PolicyRuleOut enriched with canonical 3-gate metadata & remediation hint."""
    spec = RULE_SPECS_BY_ID.get(rule_id)
    if spec is None:
        return PolicyRuleOut(rule_id=rule_id, outcome=outcome, message=message)

    hint: RemediationHintOut | None = None
    if spec.remediation_action_type:
        hint = RemediationHintOut(
            action_type=spec.remediation_action_type,
            label_en=spec.remediation_label_en,
            label_fi=spec.remediation_label_fi,
            target_product_ids=list(target_product_ids or []),
        )
    return PolicyRuleOut(
        rule_id=rule_id,
        outcome=outcome,
        message=message,
        gate=spec.gate,
        overridable_via_approval=spec.overridable_via_approval,
        remediation_hint=hint,
    )


def build_layer1_invariants() -> list[GovernanceInvariantOut]:
    """Return canonical GovernanceInvariantOut items driven by GOVERNANCE_RULE_SPECS."""
    return [
        GovernanceInvariantOut(
            rule_id=spec.rule_id,
            outcome=spec.default_outcome,
            description=spec.description,
            gate=spec.gate,
            overridable_via_approval=spec.overridable_via_approval,
        )
        for spec in GOVERNANCE_RULE_SPECS
    ]
