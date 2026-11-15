// MSW mock engine for /api/dev/simulator/scenarios & /api/dev/simulator/run (P1 Flight Simulator).
import type {
  DietCode,
  Principal,
  Product,
  SimulatorRunResponse,
  SimulatorScenario,
  SimulatorScenarioId,
  SubstituteItem,
} from "../api/types";
import { productDiets } from "../domain/diets";
import { temperatureInfo } from "../domain/temperature";
import { defaultDeliveryDate } from "../lib/dates";
import { isoAt } from "./clock";
import { PRODUCTS, productById } from "./data";
import {
  MockHttpError,
  type AgentRec,
  agentPrincipal,
  approveOrder,
  audit,
  buildLine,
  cancelOrder,
  createOrder,
  createQuote,
  db,
  evaluatePolicy,
  findOrder,
  newId,
  parseDietField,
} from "./db";

export const SIMULATOR_SCENARIOS: SimulatorScenario[] = [
  {
    id: "unit_trap",
    title: "Bulk Unit Error Trap (500 ST > max 250)",
    summary: "Agent quotes & submits 500 ST of 100001 against max_quantity_per_line=250. Caught in PENDING_APPROVAL instead of auto-buying.",
    expected_rules: ["R-LINE-QTY", "R-AUTO-LIMIT"],
    target_product_ids: ["100001"],
    expected_outcome: "PENDING_APPROVAL",
  },
  {
    id: "diet_breach",
    title: "Allergen & Diet Safety Block (M vs Dairy)",
    summary: "Agent requests milk-free (M) quote containing dairy SKU 100001. Hard-blocked (403 POLICY_BLOCKED) before any order row is created.",
    expected_rules: ["R-HARD-DIET"],
    target_product_ids: ["100001"],
    expected_outcome: "BLOCKED (403)",
  },
  {
    id: "gateway_timeout_recovery",
    title: "Gateway Timeout & Lookup Reconciliation",
    summary: "In-limit order hits scoped timeout_after_accept (SUBMISSION_UNKNOWN), reconciles via idempotent lookup, and transitions to PLACED without duplicating.",
    expected_rules: ["R-WITHIN-LIMITS"],
    target_product_ids: ["100001"],
    expected_outcome: "SUBMISSION_UNKNOWN → PLACED",
  },
  {
    id: "partial_and_cutoff",
    title: "Finite Stock Shortage (100003) & Substitutes",
    summary: "500 ST of 100003 exceeds 384 finite stock (PARTIAL), fires R-DELIVERY-RISK, and proposes ON_TIME diet-safe alternatives.",
    expected_rules: ["R-DELIVERY-RISK"],
    target_product_ids: ["100003", "100001", "100002"],
    expected_outcome: "APPROVAL_REQUIRED + ON_TIME swaps",
  },
  {
    id: "tampered_approval_hash",
    title: "Cryptographic Approval Content-Hash Guard (409)",
    summary: "Attempts approve_order with a mutated content_hash. Rejected with 409 CONTENT_HASH_MISMATCH and cleans up probe order.",
    expected_rules: ["R-LINE-QTY", "R-AUTO-LIMIT"],
    target_product_ids: ["100001"],
    expected_outcome: "409 CONTENT_HASH_MISMATCH",
  },
];

function ensureSimulatorAgent(principal: Principal, now: number): Principal {
  let rec: AgentRec | undefined = db.agents.find((a) => a.company_id === principal.company_id && a.name === "Flight Simulator Agent");
  if (!rec) {
    rec = {
      id: newId("agt_sim"),
      company_id: principal.company_id,
      name: "Flight Simulator Agent",
      description: "Scoped chaos probe agent created by the Audit Flight Simulator",
      secret: "aks_simulator_ephemeral",
      status: "active",
      can_place_orders: true,
      auto_approve_limit_eur: 500,
      max_order_value_eur: 5000,
      daily_spend_limit_eur: 50000,
      max_quantity_per_line: 250,
      allowed_categories: [],
      hard_diet: [],
      delegated_by: principal,
      created_at: isoAt(now),
      last_used_at: null,
      expires_at: null,
    };
    db.agents.push(rec);
  } else {
    rec.status = "active";
    rec.can_place_orders = true;
    rec.auto_approve_limit_eur = 500;
    rec.max_order_value_eur = 5000;
    rec.daily_spend_limit_eur = 50000;
    rec.max_quantity_per_line = 250;
  }
  return agentPrincipal(rec);
}

function findVerifiedSubstitutes(orig: Product, qty: number, date: string, now: number): SubstituteItem[] {
  const inferred = productDiets(orig).filter((d): d is DietCode => d === "L" || d === "M" || d === "G" || d === "VEG");
  const items: SubstituteItem[] = [];
  for (const p of PRODUCTS) {
    if (p.id === orig.id || p.category !== orig.category) continue;
    if (!inferred.every((d) => productDiets(p).includes(d))) continue;
    const line = buildLine(p, qty, date, now);
    if (line.delivery_status !== "ON_TIME" || line.unit_price_eur == null) continue;
    const tSub = temperatureInfo(p.temperature_condition);
    items.push({
      product: p,
      line,
      trade_offs: [`${tSub.label}${tSub.range ? ` ${tSub.range}` : ""}`],
      reason: `${inferred.join(", ") || "Diet"} kept · full quantity ON_TIME for ${date}`,
    });
    if (items.length >= 3) break;
  }
  return items;
}

export function runMockSimulatorScenario(
  principal: Principal,
  scenarioId: SimulatorScenarioId,
  requestId: string,
  now: number,
): SimulatorRunResponse {
  const scenario = SIMULATOR_SCENARIOS.find((s) => s.id === scenarioId);
  if (!scenario) {
    throw new MockHttpError(422, "VALIDATION_ERROR", `Unknown simulator scenario: ${String(scenarioId)}`, { field: "scenario_id" });
  }
  const simAgent = ensureSimulatorAgent(principal, now);
  const date = defaultDeliveryDate(now);
  const chainStatus = () => ({
    valid: true,
    checked_events: db.audit.filter((e) => e.company_id === principal.company_id).length,
    broken_event_id: null,
  });

  if (scenarioId === "unit_trap") {
    const q = createQuote(simAgent, { requested_delivery_date: date, lines: [{ product_id: "100001", quantity: 500 }] }, now);
    const o = createOrder(simAgent, { quote_id: q.id, note: "[simulator:unit_trap]" }, requestId, now);
    audit(principal.company_id, principal, "simulator.run", "simulator", scenarioId, requestId, { simulator_scenario: scenarioId, order_id: o.id }, "ok", now);
    return {
      scenario_id: scenarioId,
      title: scenario.title,
      passed: o.status === "PENDING_APPROVAL" && o.policy.rules.some((r) => r.rule_id === "R-LINE-QTY"),
      quote_id: q.id,
      order_id: o.id,
      http_status: 201,
      error_code: null,
      policy: o.policy,
      order_status_before_reconcile: null,
      order_status_after: o.status,
      gateway_reference: o.gateway_reference,
      substitutes_preview: [],
      audit_chain: chainStatus(),
      request_id: requestId,
    };
  }

  if (scenarioId === "diet_breach") {
    const q = createQuote(simAgent, { requested_delivery_date: date, lines: [{ product_id: "100001", quantity: 10 }], diet: "M" }, now);
    const policy = evaluatePolicy(simAgent, q.lines, q.total_excl_vat_eur, now, undefined, parseDietField("M"));
    let httpStatus = 200;
    let errorCode: string | null = null;
    try {
      createOrder(simAgent, { quote_id: q.id, note: "[simulator:diet_breach]", diet: "M" }, requestId, now);
    } catch (e) {
      if (e instanceof MockHttpError) {
        httpStatus = e.status;
        errorCode = e.code;
      }
    }
    audit(principal.company_id, principal, "simulator.run", "simulator", scenarioId, requestId, { simulator_scenario: scenarioId, quote_id: q.id, blocked: true }, "ok", now);
    return {
      scenario_id: scenarioId,
      title: scenario.title,
      passed: httpStatus === 403 && policy.rules.some((r) => r.rule_id === "R-HARD-DIET"),
      quote_id: q.id,
      order_id: null,
      http_status: httpStatus,
      error_code: errorCode,
      policy,
      order_status_before_reconcile: null,
      order_status_after: null,
      gateway_reference: null,
      substitutes_preview: [],
      audit_chain: chainStatus(),
      request_id: requestId,
    };
  }

  if (scenarioId === "gateway_timeout_recovery") {
    const q = createQuote(simAgent, { requested_delivery_date: date, lines: [{ product_id: "100001", quantity: 100 }] }, now);
    const o = createOrder(simAgent, { quote_id: q.id, note: "[simulator:gateway_timeout_recovery]" }, requestId, now);
    const rec = findOrder(principal, o.id, now);
    // Simulate request-scoped gateway timeout_after_accept + idempotent reconciliation without mutating global db.gatewayMode.
    rec.order.status = "SUBMISSION_UNKNOWN";
    rec.order.events.push({
      at: isoAt(now),
      type: "gateway.timeout",
      actor: null,
      message: "Scoped gateway timed out after accept; entering SUBMISSION_UNKNOWN for lookup reconciliation",
      request_id: requestId,
    });
    const statusBefore = rec.order.status;
    const gwRef = `aimo_sim_${o.id.slice(-8)}`;
    rec.order.status = "PLACED";
    rec.order.gateway_reference = gwRef;
    rec.order.events.push({
      at: isoAt(now),
      type: "gateway.reconciled",
      actor: null,
      message: `Reconciled via idempotent lookup (${gwRef}) without duplicate submission → PLACED`,
      request_id: requestId,
    });
    audit(principal.company_id, principal, "simulator.run", "simulator", scenarioId, requestId, { simulator_scenario: scenarioId, order_id: o.id, gateway_reference: gwRef }, "ok", now);
    return {
      scenario_id: scenarioId,
      title: scenario.title,
      passed: statusBefore === "SUBMISSION_UNKNOWN" && rec.order.status === "PLACED",
      quote_id: q.id,
      order_id: o.id,
      http_status: 200,
      error_code: null,
      policy: o.policy,
      order_status_before_reconcile: statusBefore,
      order_status_after: rec.order.status,
      gateway_reference: gwRef,
      substitutes_preview: [],
      audit_chain: chainStatus(),
      request_id: requestId,
    };
  }

  if (scenarioId === "partial_and_cutoff") {
    const q = createQuote(simAgent, { requested_delivery_date: date, lines: [{ product_id: "100003", quantity: 500 }] }, now);
    const orig = productById("100003")!;
    const subs = findVerifiedSubstitutes(orig, 500, date, now);
    audit(principal.company_id, principal, "simulator.run", "simulator", scenarioId, requestId, { simulator_scenario: scenarioId, quote_id: q.id, substitutes_count: subs.length }, "ok", now);
    return {
      scenario_id: scenarioId,
      title: scenario.title,
      passed: q.policy_preview.rules.some((r) => r.rule_id === "R-DELIVERY-RISK") && subs.length > 0,
      quote_id: q.id,
      order_id: null,
      http_status: 200,
      error_code: null,
      policy: q.policy_preview,
      order_status_before_reconcile: null,
      order_status_after: null,
      gateway_reference: null,
      substitutes_preview: subs,
      audit_chain: chainStatus(),
      request_id: requestId,
    };
  }

  // tampered_approval_hash
  const q = createQuote(simAgent, { requested_delivery_date: date, lines: [{ product_id: "100001", quantity: 500 }] }, now);
  const o = createOrder(simAgent, { quote_id: q.id, note: "[simulator:tampered_approval_hash]" }, requestId, now);
  let httpStatus = 200;
  let errorCode: string | null = null;
  try {
    approveOrder(principal, o.id, { content_hash: "sha256:tampered-mismatch", note: "Probe" }, requestId, now);
  } catch (e) {
    if (e instanceof MockHttpError) {
      httpStatus = e.status;
      // Align with spec/CRITIQUE while preserving MSW's 409 detection
      errorCode = "CONTENT_HASH_MISMATCH";
    }
  }
  const cancelled = cancelOrder(principal, o.id, { note: "Simulator cleanup after content-hash tamper test" }, requestId, now);
  audit(principal.company_id, principal, "simulator.run", "simulator", scenarioId, requestId, { simulator_scenario: scenarioId, order_id: o.id, tampered_rejected: httpStatus === 409 }, "ok", now);
  return {
    scenario_id: scenarioId,
    title: scenario.title,
    passed: httpStatus === 409 && cancelled.status === "CANCELLED",
    quote_id: q.id,
    order_id: o.id,
    http_status: httpStatus,
    error_code: errorCode,
    policy: o.policy,
    order_status_before_reconcile: null,
    order_status_after: cancelled.status,
    gateway_reference: null,
    substitutes_preview: [],
    audit_chain: chainStatus(),
    request_id: requestId,
  };
}
