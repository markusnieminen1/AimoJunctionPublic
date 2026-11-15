import { ApiError } from "../api/client";
import { endpoints } from "../api/endpoints";
import type { Order, Quote, QuoteLine, SubstituteItem } from "../api/types";
import { classifyRuleGate, isRuleOverridable } from "./governanceGates";

export type RescueFixKind = "ADJUST_TO_RECIPE_YIELD" | "SWAP_TO_VERIFIED_SUBSTITUTE";

export type RescueEligibility = {
  eligible: boolean;
  hasRecipeFix: boolean;
  hasDeliveryRiskFix: boolean;
  riskyLines: QuoteLine[];
};

const PLANT_BASED_RE = /\b(oat|kaura|soy|soija|almond|manteli|plant|kasvipohj)/i;

/** Merge comma-separated diet codes into a deduplicated uppercase list (or undefined). */
export function mergeDiets(...diets: Array<string | null | undefined>): string | undefined {
  const seen = new Set<string>();
  for (const raw of diets) {
    if (!raw) continue;
    for (const part of raw.split(",")) {
      const code = part.trim().toUpperCase();
      if (code) seen.add(code);
    }
  }
  return seen.size > 0 ? [...seen].join(",") : undefined;
}

/**
 * Evaluate whether a PENDING_APPROVAL order qualifies for 1-Tap Safe Fix & Place.
 * Strictly forbids Gate 1 safety locks, Gate 3 hard ceilings, BLOCKED rules, and unresolved UNIT_MISMATCH lines.
 */
export function evaluateRescueEligibility(order: Order): RescueEligibility {
  if (order.status !== "PENDING_APPROVAL" || order.lines.length === 0) {
    return { eligible: false, hasRecipeFix: false, hasDeliveryRiskFix: false, riskyLines: [] };
  }

  const hasUnresolvedUnitMismatch = order.lines.some((l) => l.flags.includes("UNIT_MISMATCH"));
  if (hasUnresolvedUnitMismatch) {
    return { eligible: false, hasRecipeFix: false, hasDeliveryRiskFix: false, riskyLines: [] };
  }

  const hasHardLock = order.policy.rules.some((r) => {
    const gate = classifyRuleGate(r.rule_id, r.gate);
    if (gate === "SAFETY_LOCK") return true;
    if (!isRuleOverridable(r.rule_id, r.outcome, r.overridable_via_approval)) return true;
    return r.outcome === "BLOCKED";
  });
  if (hasHardLock) {
    return { eligible: false, hasRecipeFix: false, hasDeliveryRiskFix: false, riskyLines: [] };
  }

  const hasRecipeFix =
    order.policy.rules.some((r) => r.rule_id === "R-RECIPE-SANITY" && r.outcome !== "AUTO") &&
    !!order.recipe_sanity_warning?.triggered &&
    Array.isArray(order.recipe_sanity_warning.offending_lines) &&
    order.recipe_sanity_warning.offending_lines.length > 0;

  const riskyLines = order.lines.filter(
    (l) =>
      l.delivery_status !== "ON_TIME" ||
      l.flags.includes("PARTIAL") ||
      l.flags.includes("CUTOFF_PASSED") ||
      l.flags.includes("LATE"),
  );
  const hasDeliveryRiskFix =
    order.policy.rules.some((r) => r.rule_id === "R-DELIVERY-RISK" && r.outcome !== "AUTO") &&
    riskyLines.length > 0 &&
    riskyLines.length <= 3;

  return {
    eligible: hasRecipeFix || hasDeliveryRiskFix,
    hasRecipeFix,
    hasDeliveryRiskFix,
    riskyLines,
  };
}

/** Build right-sized order lines from server R-RECIPE-SANITY offending_lines metadata. */
export function buildRecipeRightSizeLines(order: Order): { product_id: string; quantity: number }[] | null {
  const offending = order.recipe_sanity_warning?.offending_lines;
  if (!Array.isArray(offending) || offending.length === 0) return null;
  const expectedByProduct = new Map(
    offending.map((line) => [line.product_id, Math.max(1, Math.ceil(line.expected_qty))]),
  );
  let changed = false;
  const next = order.lines.map((l) => {
    const target = expectedByProduct.get(l.product_id);
    if (target != null && target !== l.quantity) {
      changed = true;
      return { product_id: l.product_id, quantity: target };
    }
    return { product_id: l.product_id, quantity: l.quantity };
  });
  return changed ? next : null;
}

/** Select top ON_TIME diet-compatible non-plant-based substitute returned by /api/products/substitutes. */
export function pickVerifiedSubstitute(origLine: QuoteLine, items: SubstituteItem[]): SubstituteItem | null {
  const origIsPlant = PLANT_BASED_RE.test(origLine.product_name);
  for (const item of items) {
    if (item.line.delivery_status !== "ON_TIME") continue;
    if (
      item.line.flags.includes("PARTIAL") ||
      item.line.flags.includes("CUTOFF_PASSED") ||
      item.line.flags.includes("LATE") ||
      item.line.flags.includes("UNIT_MISMATCH") ||
      item.line.flags.includes("NO_PRICE")
    ) {
      continue;
    }
    if (item.line.unit_price_eur == null || item.line.line_total_eur == null) continue;
    if (!origIsPlant && PLANT_BASED_RE.test(item.product.name)) continue;
    return item;
  }
  return null;
}

export type RescueExecutionInput = {
  order: Order;
  fixKind: RescueFixKind;
  cleanLines: { product_id: string; quantity: number }[];
  effectiveDiet?: string;
  savingsEur?: number | null;
};

export type RescueExecutionResult = {
  quote: Quote;
  newOrder: Order;
  supersededOrder: Order;
};

/**
 * Path A composed Core-API rescue:
 * 1. Fresh GET /api/orders/{id} hash & status check (no draft mutation on drift)
 * 2. POST /api/quotes + verify non-BLOCKED and all lines ON_TIME
 * 3. POST /api/orders with deterministic Idempotency-Key -> PLACED
 * 4. Reject superseded draft ONLY after new order succeeds
 */
export async function executeSafeRescue({
  order,
  fixKind,
  cleanLines,
  effectiveDiet,
  savingsEur,
}: RescueExecutionInput): Promise<RescueExecutionResult> {
  const fresh = await endpoints.getOrder(order.id);
  if (fresh.status !== "PENDING_APPROVAL") {
    throw new ApiError(409, "INVALID_STATE", `Order ${order.id} is already ${fresh.status}`, null, {
      status: fresh.status,
    });
  }
  if (fresh.content_hash !== order.content_hash) {
    throw new ApiError(
      409,
      "APPROVAL_CONTENT_CHANGED",
      "Order details changed before remediation could run. Review refreshed order first.",
      null,
    );
  }

  const quote = await endpoints.createQuote({
    requested_delivery_date: fresh.requested_delivery_date,
    lines: cleanLines,
    diet: effectiveDiet,
  });

  if (
    quote.policy_preview.outcome === "BLOCKED" ||
    quote.lines.some(
      (l) =>
        l.delivery_status !== "ON_TIME" ||
        l.flags.includes("UNIT_MISMATCH") ||
        l.flags.includes("PARTIAL") ||
        l.flags.includes("CUTOFF_PASSED"),
    )
  ) {
    throw new ApiError(
      422,
      "NO_SAFE_REMEDIATION",
      "Replacement quote failed safety or on-time delivery checks. Held draft left untouched.",
      null,
    );
  }

  // backend validate_idempotency_key enforces len <= 128; prefixing hash keeps retries deterministic within limit.
  const idempotencyKey = `rescue-place:${fresh.id}:${fresh.content_hash.slice(0, 24)}:${fixKind}`.slice(0, 128);
  const savingsSuffix =
    typeof savingsEur === "number" && savingsEur > 0 ? ` · saved €${savingsEur.toFixed(2)}` : "";

  const newOrder = await endpoints.createOrder(
    {
      quote_id: quote.id,
      diet: effectiveDiet,
      note: `Rescued from ${fresh.id} (${fixKind}${savingsSuffix})`,
    },
    idempotencyKey,
  );

  const supersededOrder = await endpoints.rejectOrder(fresh.id, {
    note: `Superseded by rescued order ${newOrder.id} (${fixKind}${savingsSuffix})`,
  });

  return { quote, newOrder, supersededOrder };
}
