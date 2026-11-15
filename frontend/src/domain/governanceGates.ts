import type { GovernanceGateId, PolicyRule } from "../api/types";

export type { GovernanceGateId };

export const GOVERNANCE_GATES: readonly GovernanceGateId[] = [
  "SAFETY_LOCK",
  "OPERATIONAL_SANITY",
  "AUTONOMY_ENVELOPE",
] as const;

const GATE1_RULES = new Set(["R-HARD-DIET", "R-NOT-DELIVERABLE", "R-SALES-BLOCKED"]);
const GATE2_RULES = new Set(["R-DELIVERY-RISK", "R-RECIPE-SANITY", "R-STANDING-DRIFT", "R-SEMANTIC-GUARD", "R-LIVE-ADVISORY"]);
const HARD_CEILING_RULES = new Set(["R-HARD-DIET", "R-NOT-DELIVERABLE", "R-SALES-BLOCKED", "R-AGENT-NO-ORDER", "R-MAX-VALUE"]);

export function classifyRuleGate(ruleId: string, explicitGate?: GovernanceGateId | null): GovernanceGateId {
  if (explicitGate) return explicitGate;
  if (GATE1_RULES.has(ruleId)) return "SAFETY_LOCK";
  if (GATE2_RULES.has(ruleId)) return "OPERATIONAL_SANITY";
  return "AUTONOMY_ENVELOPE";
}

export function isRuleOverridable(ruleId: string, outcome?: string, explicit?: boolean | null): boolean {
  if (typeof explicit === "boolean") return explicit;
  if (HARD_CEILING_RULES.has(ruleId)) return false;
  if (outcome === "BLOCKED") return false;
  return true;
}

export type RemediationAction = {
  kind: "recipe" | "substitutes" | "limits" | "diet";
  to: string;
  customLabel?: string;
};

export function inferRemediation(
  rule: Pick<PolicyRule, "rule_id" | "outcome" | "remediation_hint">,
  opts: { agentId?: string; locale?: "en" | "fi" } = {},
): RemediationAction | null {
  if (rule.outcome === "AUTO") return null;
  const hint = rule.remediation_hint;
  const customLabel =
    (opts.locale === "fi" ? hint?.label_fi ?? hint?.label_en : hint?.label_en ?? hint?.label_fi) ?? undefined;

  if (hint?.action_type === "ADJUST_TO_RECIPE_YIELD" || rule.rule_id === "R-RECIPE-SANITY") {
    return { kind: "recipe", to: "/cart", customLabel };
  }
  if (hint?.action_type === "OPEN_SUBSTITUTES" || rule.rule_id === "R-DELIVERY-RISK" || rule.rule_id === "R-NOT-DELIVERABLE") {
    return { kind: "substitutes", to: "/cart", customLabel };
  }
  if (hint?.action_type === "REMOVE_BLOCKED_LINES" || rule.rule_id === "R-HARD-DIET" || rule.rule_id === "R-SALES-BLOCKED") {
    return { kind: "diet", to: "/cart", customLabel };
  }
  if (
    hint?.action_type === "EDIT_AGENT_LIMITS" ||
    rule.rule_id === "R-MAX-VALUE" ||
    rule.rule_id === "R-AGENT-NO-ORDER" ||
    rule.rule_id === "R-AUTO-LIMIT" ||
    rule.rule_id === "R-DAILY-SPEND" ||
    rule.rule_id === "R-CATEGORY" ||
    rule.rule_id === "R-LINE-QTY"
  ) {
    return {
      kind: "limits",
      to: opts.agentId ? `/agents/${encodeURIComponent(opts.agentId)}` : "/agents",
      customLabel,
    };
  }
  return null;
}
