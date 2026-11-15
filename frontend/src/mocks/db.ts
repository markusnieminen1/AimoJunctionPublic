// In-memory mock backend state + domain engine (quotes, policy, orders, audit, A2A).
// This simulates the BACKEND. UI code must never import from src/mocks.
import type {
  A2AHistoryEntry,
  A2ATask,
  Agent,
  AimoCampaignOut,
  AssistantSettingsOut,
  AuditEvent,
  BrandPreferenceOut,
  CompanyBranchOut,
  DeliveryStatus,
  DietCode,
  LineFlag,
  NegotiationSettingsOut,
  Order,
  OrderEvent,
  PolicyDecision,
  PolicyOutcome,
  PolicyRule,
  Principal,
  Product,
  PromotionOut,
  Quote,
  QuoteLine,
  QuarantinedAgentAlertOut,
  TelegramOptInOut,
} from "../api/types";
import { addDays, defaultDeliveryDate, helsinkiDate, weekdayOf } from "../lib/dates";
import { DIETS, DIET_CODES, productDiets } from "../domain/diets";
import { isoAt, mockNowMs } from "./clock";
import { PRODUCTS, SEED_AGENT_SECRETS, SEED_COMPANY, SEED_EXTRA_TENANTS, SEED_USER, SUPPLY, VAT_FOOD_PERCENT, productById } from "./data";
import { sha256Hex } from "./sha256";

// ---------- Errors ----------

export class MockHttpError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
    readonly details: Record<string, unknown> = {},
  ) {
    super(message);
  }
}

// ---------- State ----------

export type UserRec = { id: string; name: string; email: string; password: string; company_id: string };
export type AgentRec = Omit<Agent, "spent_today_eur"> & { company_id: string; secret: string; hard_diet: DietCode[] };
export type TokenRec = { kind: "user" | "agent"; id: string; expires_ms: number };
export type QuoteRec = { quote: Quote; company_id: string };
export type OrderRec = {
  order: Order;
  company_id: string;
  /** mock ms when SUBMITTING started / SUBMISSION_UNKNOWN started */
  submit_ms: number | null;
  unknown_ms: number | null;
  gateway: GatewayMode;
};
export type IdemRec = { body: string; status: number; response: unknown };
export type GatewayMode = "ok" | "timeout" | "error";
type CompanyAudit = AuditEvent & { company_id: string };

export type MockDb = {
  companies: Map<string, { id: string; name: string; business_id?: string }>;
  users: UserRec[];
  agents: AgentRec[];
  tokens: Map<string, TokenRec>;
  quotes: Map<string, QuoteRec>;
  orders: OrderRec[];
  idem: Map<string, IdemRec>;
  audit: CompanyAudit[];
  a2a: (A2ATask & { company_id: string })[];
  promotions: PromotionOut[];
  aimoCampaigns: AimoCampaignOut[];
  brandPreferences: BrandPreferenceOut[];
  companyBranches: (CompanyBranchOut & { company_id: string })[];
  negotiationSettings: Map<string, NegotiationSettingsOut>;
  assistantSettings: Map<string, AssistantSettingsOut>;
  telegramOptIns: Map<string, TelegramOptInOut>;
  anomalies: (QuarantinedAgentAlertOut & { company_id: string })[];
  conversations: Map<string, { id: string; principal_id: string; last_quote_id: string | null; pending_qty: number | null }>;
  priceOverrides: Record<string, number>;
  gatewayMode: GatewayMode;
  /** Dev helper: assistant answers 503 LLM_UNAVAILABLE (simulates the LLM budget guard / provider outage). */
  assistantDown: boolean;
  seq: number;
};

export let db: MockDb = emptyDb();

/** Swap in a restored database (mock persistence, see persist.ts). */
export function replaceMockDb(next: MockDb): void {
  db = next;
}

/** A fresh, empty database with every field set (restore merges a snapshot over it). */
export function emptyMockDb(): MockDb {
  return emptyDb();
}

function emptyDb(): MockDb {
  return {
    companies: new Map(),
    users: [],
    agents: [],
    tokens: new Map(),
    quotes: new Map(),
    orders: [],
    idem: new Map(),
    audit: [],
    a2a: [],
    promotions: [],
    aimoCampaigns: [],
    brandPreferences: [],
    companyBranches: [],
    negotiationSettings: new Map(),
    assistantSettings: new Map(),
    telegramOptIns: new Map(),
    anomalies: [],
    conversations: new Map(),
    priceOverrides: {},
    gatewayMode: "ok",
    assistantDown: false,
    seq: 1,
  };
}

const ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ";
/** Short pseudo-ULID ids: prefix + 12 chars. Deterministic sequence + randomness. */
export function newId(prefix: string): string {
  let s = (db.seq++).toString(36).toUpperCase().padStart(4, "0");
  for (let i = 0; i < 10; i++) s += ALPHABET[Math.floor(Math.random() * ALPHABET.length)];
  return `${prefix}_${s}`;
}

export function newRequestId(): string {
  let s = "01J";
  for (let i = 0; i < 23; i++) s += ALPHABET[Math.floor(Math.random() * ALPHABET.length)];
  return `req_${s}`;
}

const round2 = (n: number) => Math.round(n * 100) / 100;
const round1 = (n: number) => Math.round(n * 10) / 10;
const eur = (n: number) => `€${n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
const fmtQty = (n: number) => n.toLocaleString("en-US");

// ---------- Principals ----------

export function userPrincipal(u: UserRec): Principal {
  return { kind: "user", id: u.id, name: u.name, company_id: u.company_id, delegated_by: null };
}

export function agentPrincipal(a: AgentRec): Principal {
  return { kind: "agent", id: a.id, name: a.name, company_id: a.company_id, delegated_by: a.delegated_by };
}

export function principalFor(kind: "user" | "agent", id: string): Principal | null {
  if (kind === "user") {
    const u = db.users.find((x) => x.id === id);
    return u ? userPrincipal(u) : null;
  }
  const a = db.agents.find((x) => x.id === id);
  return a ? agentPrincipal(a) : null;
}

export function issueToken(kind: "user" | "agent", id: string, ttlMs: number, now = mockNowMs()): { token: string; expires_at: string } {
  let rand = "";
  for (let i = 0; i < 32; i++) rand += ALPHABET[Math.floor(Math.random() * ALPHABET.length)];
  const token = `mock.${kind}.${rand}`;
  db.tokens.set(token, { kind, id, expires_ms: now + ttlMs });
  return { token, expires_at: isoAt(now + ttlMs) };
}

// ---------- Agents ----------

const ACTIVE_STATUSES: Order["status"][] = ["PLACED", "SUBMITTING", "SUBMISSION_UNKNOWN", "PENDING_APPROVAL"];

/** Sum of the agent's placed + submitting + pending orders created today (Helsinki), excl. VAT. */
export function agentSpentToday(agentId: string, now = mockNowMs()): number {
  const today = helsinkiDate(now);
  return round2(
    db.orders
      .filter((r) => r.order.created_by.id === agentId && ACTIVE_STATUSES.includes(r.order.status) && helsinkiDate(Date.parse(r.order.created_at)) === today)
      .reduce((s, r) => s + r.order.total_excl_vat_eur, 0),
  );
}

export function toAgent(a: AgentRec, now = mockNowMs()): Agent {
  const { company_id, secret, hard_diet, ...rest } = a;
  void company_id;
  void secret;
  void hard_diet;
  return { ...rest, spent_today_eur: agentSpentToday(a.id, now) };
}

// ---------- Lines & quotes ----------

function kgPerSalesUnit(p: Product): number {
  const u = p.units.find((x) => x.unit_id === p.sales_unit);
  return u ? u.size_in_base_units : 1;
}

/** Live price + availability for one product (what /products/check and quotes return). */
export function buildLine(p: Product, quantity: number, deliveryDate: string, now: number): QuoteLine {
  const s = SUPPLY[p.id] ?? { price: null, model: "Stock", qty: "UNLIMITED" };
  const price = p.id in db.priceOverrides ? db.priceOverrides[p.id]! : s.price;
  const flags: LineFlag[] = [];
  const earliest = defaultDeliveryDate(now);
  let status: DeliveryStatus = "ON_TIME";
  let available_date: string | null = deliveryDate;
  let closing_time: string | null = `${addDays(deliveryDate, -1)}T17:00:00+02:00`;
  let prob: number | null = p.delivery_reliability != null ? Math.min(0.98, p.delivery_reliability) : s.model === "Stock" ? 0.9 : null;

  const worse = (st: DeliveryStatus) => {
    const order: DeliveryStatus[] = ["ON_TIME", "UNKNOWN", "AT_RISK", "LATE", "NOT_DELIVERABLE"];
    if (order.indexOf(st) > order.indexOf(status)) status = st;
  };

  if (s.qty === "ZERO") {
    worse("NOT_DELIVERABLE");
    flags.push("NOT_DELIVERABLE");
    available_date = null;
    prob = 0;
  } else if (s.model === "Terminal" && s.cutoffPassed) {
    let fixedAvail = addDays(defaultDeliveryDate(now), 2);
    while (weekdayOf(fixedAvail) === 0 || weekdayOf(fixedAvail) === 6) fixedAvail = addDays(fixedAvail, 1);
    if (deliveryDate < fixedAvail) {
      worse("LATE");
      flags.push("LATE", "CUTOFF_PASSED", "SUPPLIER_QTY_UNKNOWN");
      closing_time = `${addDays(helsinkiDate(now), -1)}T12:00:00+02:00`;
      available_date = fixedAvail;
      prob = 0.15;
    } else {
      available_date = fixedAvail;
      flags.push("SUPPLIER_QTY_UNKNOWN");
      worse("AT_RISK");
      prob = 0.7;
    }
  } else {
    if (s.model === "Terminal") {
      flags.push("SUPPLIER_QTY_UNKNOWN");
      worse("AT_RISK");
      prob = prob == null ? 0.7 : Math.min(prob, 0.7);
    }
    const weekend = weekdayOf(deliveryDate) === 0 || weekdayOf(deliveryDate) === 6;
    if (deliveryDate < earliest || weekend || Date.parse(closing_time) < now) {
      worse("LATE");
      flags.push("LATE");
      if (closing_time && Date.parse(closing_time) < now) flags.push("CUTOFF_PASSED");
      available_date = deliveryDate < earliest ? earliest : defaultDeliveryDate(Date.parse(`${deliveryDate}T12:00:00+02:00`));
      if (deliveryDate < earliest) closing_time = `${addDays(earliest, -1)}T17:00:00+02:00`;
    }
    if (s.qty === "FINITE" && s.amount != null && quantity > s.amount) {
      worse("AT_RISK");
      flags.push("PARTIAL");
      prob = prob == null ? 0.4 : Math.min(prob, 0.4);
    }
  }
  if (p.delivery_reliability != null && p.delivery_reliability < 0.8) flags.push("LOW_RELIABILITY");
  if (price == null) flags.push("NO_PRICE");

  let co2_kg: number | null = null;
  let co2_basis: string | null = null;
  if (p.co2_kg_per_kg != null) {
    const kg = kgPerSalesUnit(p);
    co2_kg = round1(p.co2_kg_per_kg * kg * quantity);
    const assumed = p.base_unit === "KG" ? "" : " (assumed 1 kg per " + p.base_unit + ")";
    co2_basis = `category avg ${round2(p.co2_kg_per_kg)} kg CO2e/kg × ${kg} kg/${p.sales_unit}${assumed} × ${fmtQty(quantity)}`;
    flags.push("CO2_ESTIMATED");
  }

  return {
    product_id: p.id,
    product_name: p.name,
    quantity,
    sales_unit: p.sales_unit,
    unit_price_eur: price,
    line_total_eur: price == null ? null : round2(price * quantity),
    tax_rate: VAT_FOOD_PERCENT,
    available_date,
    closing_time,
    logistic_model: s.model,
    available_qty_state: s.qty,
    available_amount: s.qty === "FINITE" ? s.amount ?? null : null,
    delivery_status: status,
    on_time_probability: prob,
    co2_kg,
    co2_basis,
    flags,
  };
}

const STATUS_ORDER: DeliveryStatus[] = ["ON_TIME", "UNKNOWN", "AT_RISK", "LATE", "NOT_DELIVERABLE"];

function totals(lines: QuoteLine[]) {
  const excl = round2(lines.reduce((s, l) => s + (l.line_total_eur ?? 0), 0));
  const incl = round2(lines.reduce((s, l) => s + (l.line_total_eur ?? 0) * (1 + (l.tax_rate ?? 0) / 100), 0));
  const co2Lines = lines.filter((l) => l.co2_kg != null);
  const co2 = co2Lines.length ? round1(co2Lines.reduce((s, l) => s + (l.co2_kg ?? 0), 0)) : null;
  const overall = lines.reduce<DeliveryStatus>((w, l) => (STATUS_ORDER.indexOf(l.delivery_status) > STATUS_ORDER.indexOf(w) ? l.delivery_status : w), "ON_TIME");
  return { excl, incl, co2, overall };
}

export function resolveLines(lines: { product_id: string; quantity: number }[]): { product: Product; quantity: number }[] {
  if (!Array.isArray(lines) || lines.length < 1 || lines.length > 100) {
    throw new MockHttpError(422, "VALIDATION_ERROR", "lines must contain 1..100 items", { field: "lines" });
  }
  return lines.map((l, i) => {
    if (!l || typeof l.product_id !== "string") throw new MockHttpError(422, "VALIDATION_ERROR", `lines[${i}].product_id is required`, { field: `lines[${i}].product_id` });
    if (typeof l.quantity !== "number" || !(l.quantity > 0)) {
      throw new MockHttpError(422, "VALIDATION_ERROR", `lines[${i}].quantity must be a positive number`, { field: `lines[${i}].quantity` });
    }
    const p = productById(l.product_id);
    if (!p) throw new MockHttpError(422, "UNKNOWN_PRODUCT", `Unknown product ${l.product_id}`, { product_id: l.product_id });
    return { product: p, quantity: l.quantity };
  });
}

export function validateDate(date: unknown, field = "requested_delivery_date"): string {
  if (typeof date !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(date)) {
    throw new MockHttpError(422, "VALIDATION_ERROR", `${field} must be YYYY-MM-DD`, { field });
  }
  return date;
}

// ---------- Policy ----------

const OUTCOME_RANK: Record<PolicyOutcome, number> = { AUTO: 0, APPROVAL_REQUIRED: 1, BLOCKED: 2 };

/**
 * Parse the additive `diet` request field ("M", "L,G", "heart"). Unknown codes → 422 like the backend.
 * Returns canonical DietCode[] (empty = no requirement).
 */
export function parseDietField(raw: unknown, field = "diet"): DietCode[] {
  if (raw == null || raw === "") return [];
  if (typeof raw !== "string") throw new MockHttpError(422, "VALIDATION_ERROR", `${field} must be a comma list of diet codes`, { field });
  const out: DietCode[] = [];
  for (const tok of raw.split(",").map((t) => t.trim()).filter(Boolean)) {
    const up = tok.toUpperCase();
    const code = (up === "HEART_SYMBOL" || up === "HEART_LOW_SALT" ? "HEART" : up) as DietCode;
    if (!DIET_CODES.includes(code)) throw new MockHttpError(422, "VALIDATION_ERROR", `Unknown diet code: ${tok}`, { field, diet: tok });
    if (!out.includes(code)) out.push(code);
  }
  return DIET_CODES.filter((c) => out.includes(c));
}

export function dietString(codes: DietCode[]): string | null {
  return codes.length ? codes.join(",") : null;
}

/** Hard safety rule for a requested order-wide diet: applies to humans and agents (like the backend). */
function requestDietRule(lines: QuoteLine[], diet: DietCode[]): PolicyRule | null {
  if (diet.length === 0) return null;
  const violations: string[] = [];
  for (const l of lines) {
    const p = productById(l.product_id);
    const have = p ? productDiets(p) : [];
    const missing = diet.filter((c) => !have.includes(c));
    if (missing.length) violations.push(`${l.product_id} (${l.product_name}): not confirmed ${missing.map((c) => `${DIETS[c].en.toLowerCase()} (${DIETS[c].chip})`).join(", ")}`);
  }
  return violations.length ? { rule_id: "R-HARD-DIET", outcome: "BLOCKED", message: `Dietary safety violation: ${violations.join("; ")}` } : null;
}

/** Deterministic policy, mirroring ARCHITECTURE.md "Agent limits". Most restrictive fired rule wins. */
export function evaluatePolicy(
  principal: Principal,
  lines: QuoteLine[],
  totalExcl: number,
  now: number,
  excludeOrderId?: string,
  diet: DietCode[] = [],
): PolicyDecision {
  const dietRule = requestDietRule(lines, diet);
  if (principal.kind === "user") {
    if (dietRule) return { outcome: "BLOCKED", rules: [dietRule] };
    return { outcome: "AUTO", rules: [{ rule_id: "R-USER-DIRECT", outcome: "AUTO", message: "Ordered directly by a signed-in user" }] };
  }
  const a = db.agents.find((x) => x.id === principal.id);
  if (!a) return { outcome: "BLOCKED", rules: [{ rule_id: "R-UNKNOWN-AGENT", outcome: "BLOCKED", message: "Agent not found" }] };
  const rules: PolicyRule[] = dietRule ? [dietRule] : [];
  if (!a.can_place_orders) rules.push({ rule_id: "R-NO-ORDER-PERMISSION", outcome: "BLOCKED", message: "This agent may search and quote, but not place orders" });
  if (totalExcl > a.max_order_value_eur) {
    rules.push({ rule_id: "R-MAX-ORDER", outcome: "BLOCKED", message: `${eur(totalExcl)} > ${eur(a.max_order_value_eur)} max order value` });
  }
  for (const code of a.hard_diet) {
    if (dietRule && diet.includes(code)) continue; // already covered by the requested diet
    for (const l of lines) {
      const p = productById(l.product_id);
      if (p && !productDiets(p).includes(code)) {
        rules.push({ rule_id: "R-HARD-DIET", outcome: "BLOCKED", message: `${l.product_id} is not confirmed ${code === "L" ? "lactose-free (L)" : code}; this agent may only buy ${code} products` });
      }
    }
  }
  if (totalExcl > a.auto_approve_limit_eur) {
    rules.push({ rule_id: "R-AUTO-LIMIT", outcome: "APPROVAL_REQUIRED", message: `${eur(totalExcl)} > ${eur(a.auto_approve_limit_eur)} auto limit` });
  }
  const spent = round2(agentSpentToday(a.id, now) - (excludeOrderId ? db.orders.find((r) => r.order.id === excludeOrderId && helsinkiDate(Date.parse(r.order.created_at)) === helsinkiDate(now))?.order.total_excl_vat_eur ?? 0 : 0));
  if (spent + totalExcl > a.daily_spend_limit_eur) {
    rules.push({
      rule_id: "R-DAILY-LIMIT",
      outcome: "APPROVAL_REQUIRED",
      message: `${eur(spent)} spent today + ${eur(totalExcl)} > ${eur(a.daily_spend_limit_eur)} daily limit`,
    });
  }
  if (a.allowed_categories.length) {
    for (const l of lines) {
      const p = productById(l.product_id);
      if (p && !a.allowed_categories.some((c) => p.category.startsWith(c))) {
        rules.push({ rule_id: "R-CATEGORY", outcome: "APPROVAL_REQUIRED", message: `${l.product_id} category ${p.category} is outside the agent's allowed categories` });
      }
    }
  }
  if (a.max_quantity_per_line != null) {
    for (const l of lines) {
      if (l.quantity > a.max_quantity_per_line) {
        rules.push({ rule_id: "R-LINE-QTY", outcome: "APPROVAL_REQUIRED", message: `${l.product_id}: ${fmtQty(l.quantity)} ${l.sales_unit} > ${fmtQty(a.max_quantity_per_line)} per line` });
      }
    }
  }
  for (const l of lines) {
    if (l.delivery_status !== "ON_TIME") {
      const what = l.flags.filter((f) => f !== "CO2_ESTIMATED").join(", ") || l.delivery_status;
      const extra = l.flags.includes("PARTIAL") && l.available_amount != null ? ` (${fmtQty(l.available_amount)} of ${fmtQty(l.quantity)} available)` : "";
      rules.push({ rule_id: "R-DELIVERY-RISK", outcome: "APPROVAL_REQUIRED", message: `${l.product_id}: ${what}${extra}` });
    }
  }
  if (rules.length === 0) {
    rules.push({ rule_id: "R-WITHIN-LIMITS", outcome: "AUTO", message: `${eur(totalExcl)} ≤ ${eur(a.auto_approve_limit_eur)} auto limit; all lines on time` });
  }
  const outcome = rules.reduce<PolicyOutcome>((w, r) => (OUTCOME_RANK[r.outcome] > OUTCOME_RANK[w] ? r.outcome : w), "AUTO");
  return { outcome, rules };
}

export function createQuote(
  principal: Principal,
  req: { requested_delivery_date: string; lines: { product_id: string; quantity: number }[]; diet?: unknown },
  now = mockNowMs(),
): Quote {
  const date = validateDate(req.requested_delivery_date);
  const diet = parseDietField(req.diet);
  const resolved = resolveLines(req.lines);
  const lines = resolved.map((r) => buildLine(r.product, r.quantity, date, now));
  const t = totals(lines);
  const futureClosings = lines.map((l) => (l.closing_time ? Date.parse(l.closing_time) : NaN)).filter((ms) => !Number.isNaN(ms) && ms > now);
  const expires = Math.min(now + 30 * 60_000, ...futureClosings);
  const quote: Quote = {
    id: newId("qt"),
    status: "OPEN",
    created_at: isoAt(now),
    expires_at: isoAt(expires),
    requested_delivery_date: date,
    lines,
    total_excl_vat_eur: t.excl,
    total_incl_vat_eur: t.incl,
    total_co2_kg: t.co2,
    overall_delivery_status: t.overall,
    policy_preview: evaluatePolicy(principal, lines, t.excl, now, undefined, diet),
    created_by: principal,
    diet: dietString(diet),
  };
  db.quotes.set(quote.id, { quote, company_id: principal.company_id });
  return quote;
}

/** Refresh a quote's status (OPEN → EXPIRED after expires_at). */
export function refreshQuote(rec: QuoteRec, now = mockNowMs()): Quote {
  if (rec.quote.status === "OPEN" && Date.parse(rec.quote.expires_at) <= now) rec.quote.status = "EXPIRED";
  return rec.quote;
}

// ---------- Orders ----------

export function contentHash(lines: QuoteLine[], total: number, date: string): string {
  const canonical = JSON.stringify({
    lines: lines.map((l) => [l.product_id, l.quantity, l.sales_unit, l.unit_price_eur]),
    total: total.toFixed(2),
    date,
  });
  return sha256Hex(canonical);
}

function event(o: Order, type: string, actor: Principal | null, message: string, requestId: string, now: number) {
  const e: OrderEvent = { at: isoAt(now), type, actor, message, request_id: requestId };
  o.events.push(e);
}

export function audit(
  companyId: string,
  actor: Principal | null,
  action: string,
  entity_type: string,
  entity_id: string | null,
  requestId: string,
  details: Record<string, unknown> = {},
  outcome: "ok" | "error" = "ok",
  now = mockNowMs(),
): void {
  db.audit.unshift({ id: newId("aud"), at: isoAt(now), request_id: requestId, actor, action, entity_type, entity_id, outcome, details, company_id: companyId });
}

/** Re-price lines at `now`. Returns fresh lines and whether prices/availability changed. */
function revalidate(lines: QuoteLine[], date: string, now: number): { fresh: QuoteLine[]; changed: boolean } {
  const fresh = lines.map((l) => buildLine(productById(l.product_id)!, l.quantity, date, now));
  const changed = fresh.some((f, i) => f.unit_price_eur !== lines[i]!.unit_price_eur || f.delivery_status !== lines[i]!.delivery_status);
  return { fresh, changed };
}

export function createOrder(principal: Principal, body: { quote_id?: unknown; note?: unknown; diet?: unknown }, requestId: string, now = mockNowMs()): Order {
  if (typeof body.quote_id !== "string") throw new MockHttpError(422, "VALIDATION_ERROR", "quote_id is required", { field: "quote_id" });
  const reqDiet = parseDietField(body.diet);
  const qrec = db.quotes.get(body.quote_id);
  if (!qrec || qrec.company_id !== principal.company_id) throw new MockHttpError(404, "NOT_FOUND", `Quote ${body.quote_id} not found`);
  const quote = refreshQuote(qrec, now);
  if (quote.status === "USED") throw new MockHttpError(409, "QUOTE_ALREADY_USED", "This quote has already been used for an order. Request a new quote.", { quote_id: quote.id });
  if (quote.status === "EXPIRED") throw new MockHttpError(409, "QUOTE_EXPIRED", "The quote has expired. Request a new quote.", { quote_id: quote.id, expires_at: quote.expires_at });
  const { fresh, changed } = revalidate(quote.lines, quote.requested_delivery_date, now);
  if (changed) {
    throw new MockHttpError(409, "QUOTE_STALE", "Prices or availability changed since the quote. Review the fresh lines and re-quote.", { quote_id: quote.id, lines: fresh });
  }
  // Quote diet + order diet are combined (backend: _combined_diet_param).
  const diet = parseDietField([quote.diet, dietString(reqDiet)].filter(Boolean).join(","));

  // §10 Batch H: align placed order delivery date to the last available item so deliverable lines arrive On time.
  const todayIso = helsinkiDate(now);
  let lastAvailableDate = quote.requested_delivery_date;
  let needsLateBump = false;
  for (const ln of quote.lines) {
    if (ln.available_date && ln.available_date > lastAvailableDate) {
      lastAvailableDate = ln.available_date;
    }
    if (
      ln.delivery_status !== "NOT_DELIVERABLE" &&
      (ln.flags.includes("LATE") || ln.flags.includes("CUTOFF_PASSED")) &&
      (!ln.available_date || ln.available_date <= quote.requested_delivery_date)
    ) {
      needsLateBump = true;
    }
  }
  if (needsLateBump) {
    let d = addDays(lastAvailableDate > todayIso ? lastAvailableDate : todayIso, 1);
    while (weekdayOf(d) === 0 || weekdayOf(d) === 6) d = addDays(d, 1);
    lastAvailableDate = d;
  }

  const orderLines: QuoteLine[] = quote.lines.map((ln) => {
    if (ln.delivery_status === "NOT_DELIVERABLE") return { ...ln, flags: [...ln.flags] };
    const strippedFlags = ln.flags.filter((f) => f !== "LATE" && f !== "CUTOFF_PASSED");
    const nextAvailableDate = ln.available_date ?? lastAvailableDate;
    const isPartialOrMismatch = strippedFlags.includes("PARTIAL") || strippedFlags.includes("UNIT_MISMATCH");
    return {
      ...ln,
      flags: strippedFlags,
      available_date: nextAvailableDate,
      delivery_status: isPartialOrMismatch ? ln.delivery_status : "ON_TIME",
      on_time_probability: isPartialOrMismatch ? ln.on_time_probability : Math.max(ln.on_time_probability ?? 0, 0.95),
    };
  });

  const policy = evaluatePolicy(principal, orderLines, quote.total_excl_vat_eur, now, undefined, diet);
  if (policy.outcome === "BLOCKED") {
    audit(principal.company_id, principal, "order.blocked", "quote", quote.id, requestId, { policy }, "error", now);
    throw new MockHttpError(403, "POLICY_BLOCKED", "Blocked by company policy: " + policy.rules.filter((r) => r.outcome === "BLOCKED").map((r) => r.rule_id).join(", "), policy as unknown as Record<string, unknown>);
  }
  const pending = policy.outcome === "APPROVAL_REQUIRED";
  const firstClosing = orderLines.map((l) => (l.closing_time ? Date.parse(l.closing_time) : NaN)).filter((ms) => !Number.isNaN(ms) && ms > now);
  const order: Order = {
    id: newId("ord"),
    status: pending ? "PENDING_APPROVAL" : "SUBMITTING",
    quote_id: quote.id,
    created_at: isoAt(now),
    requested_delivery_date: lastAvailableDate,
    lines: orderLines,
    total_excl_vat_eur: quote.total_excl_vat_eur,
    total_incl_vat_eur: quote.total_incl_vat_eur,
    total_co2_kg: quote.total_co2_kg,
    policy,
    created_by: principal,
    decided_by: null,
    decided_at: null,
    decision_note: typeof body.note === "string" ? body.note : null,
    approval_expires_at: pending ? isoAt(Math.min(now + 4 * 3600_000, ...firstClosing)) : null,
    negotiated_savings_eur: quote.negotiated_savings_eur ?? null,
    content_hash: contentHash(orderLines, quote.total_excl_vat_eur, lastAvailableDate),
    gateway_reference: null,
    events: [],
    diet: dietString(diet),
  };
  quote.status = "USED";
  const who = principal.kind === "agent" ? `${principal.name} (agent, authorized by ${principal.delegated_by?.name ?? "?"})` : principal.name;
  event(order, "order.created", principal, `Order created by ${who} from quote ${quote.id} · ${eur(order.total_excl_vat_eur)} excl. VAT`, requestId, now);
  event(order, "policy.evaluated", null, `Policy ${policy.outcome}: ${policy.rules.map((r) => r.rule_id).join(", ")}`, requestId, now);
  const rec: OrderRec = { order, company_id: principal.company_id, submit_ms: null, unknown_ms: null, gateway: db.gatewayMode };
  if (pending) {
    event(order, "approval.requested", null, `Waiting for human approval (expires ${order.approval_expires_at})`, requestId, now);
  } else {
    startSubmission(rec, requestId, now);
  }
  db.orders.unshift(rec);
  if (principal.kind === "agent") {
    const a = db.agents.find((x) => x.id === principal.id);
    if (a) a.last_used_at = isoAt(now);
  }
  audit(principal.company_id, principal, "order.create", "order", order.id, requestId, { status: order.status, total_excl_vat_eur: order.total_excl_vat_eur, policy: policy.outcome }, "ok", now);
  return order;
}

function startSubmission(rec: OrderRec, requestId: string, now: number) {
  rec.order.status = "SUBMITTING";
  rec.submit_ms = now;
  rec.gateway = db.gatewayMode;
  event(rec.order, "gateway.submitting", null, "Sending order to the Valio Aimo order gateway (mock)", requestId, now);
}

/** Lazily advance time-based transitions (SUBMITTING → PLACED etc., approval expiry). Call before reading. */
export function settleOrder(rec: OrderRec, now = mockNowMs()): Order {
  const o = rec.order;
  const rid = "req_reconciler";
  if (o.status === "SUBMITTING" && rec.submit_ms != null && now - rec.submit_ms >= 2500) {
    if (rec.gateway === "timeout") {
      o.status = "SUBMISSION_UNKNOWN";
      rec.unknown_ms = rec.submit_ms + 2500;
      event(o, "gateway.timeout", null, "Gateway timed out after accepting; outcome unknown. Reconciler will look it up (no blind retry).", rid, rec.unknown_ms);
      audit(rec.company_id, null, "order.submission_unknown", "order", o.id, rid, {}, "error", rec.unknown_ms);
    } else if (rec.gateway === "error") {
      o.status = "FAILED";
      event(o, "gateway.failed", null, "Gateway rejected the order (mock error). Needs attention.", rid, rec.submit_ms + 2500);
      audit(rec.company_id, null, "order.failed", "order", o.id, rid, {}, "error", rec.submit_ms + 2500);
    } else {
      placeOrder(rec, rec.submit_ms + 2500, rid, "Gateway confirmed");
    }
  }
  if (o.status === "SUBMISSION_UNKNOWN" && rec.unknown_ms != null && now - rec.unknown_ms >= 6000) {
    placeOrder(rec, rec.unknown_ms + 6000, rid, "Reconciler found the order at the gateway (lookup by order_id)");
  }
  if (o.status === "PENDING_APPROVAL" && o.approval_expires_at && Date.parse(o.approval_expires_at) <= now) {
    o.status = "EXPIRED";
    event(o, "approval.expired", null, "Approval window expired; nothing was ordered", rid, Date.parse(o.approval_expires_at));
    audit(rec.company_id, null, "order.expire", "order", o.id, rid, {}, "ok", Date.parse(o.approval_expires_at));
    updateA2AForOrder(o, "canceled", "Approval expired; order not placed", Date.parse(o.approval_expires_at));
  }
  return o;
}

function placeOrder(rec: OrderRec, at: number, rid: string, msg: string) {
  rec.order.status = "PLACED";
  rec.order.gateway_reference = `VA-${rec.order.id.slice(4, 12)}`;
  event(rec.order, "order.placed", null, `${msg} · reference ${rec.order.gateway_reference}`, rid, at);
  audit(rec.company_id, null, "order.placed", "order", rec.order.id, rid, { gateway_reference: rec.order.gateway_reference }, "ok", at);
  updateA2AForOrder(rec.order, "completed", `Order PLACED · gateway reference ${rec.order.gateway_reference}`, at);
}

export function findOrder(principal: Principal, id: string, now = mockNowMs()): OrderRec {
  const rec = db.orders.find((r) => r.order.id === id && r.company_id === principal.company_id);
  if (!rec || (principal.kind === "agent" && rec.order.created_by.id !== principal.id)) throw new MockHttpError(404, "NOT_FOUND", `Order ${id} not found`);
  settleOrder(rec, now);
  return rec;
}

function requireUser(principal: Principal) {
  if (principal.kind !== "user") throw new MockHttpError(403, "FORBIDDEN", "Only a human user can do this");
}

export function approveOrder(principal: Principal, id: string, body: { content_hash?: unknown; note?: unknown }, requestId: string, now = mockNowMs()): Order {
  requireUser(principal);
  const rec = findOrder(principal, id, now);
  const o = rec.order;
  if (o.status !== "PENDING_APPROVAL") throw new MockHttpError(409, "INVALID_STATE", `Order is ${o.status}, not PENDING_APPROVAL`, { status: o.status });
  if (typeof body.content_hash !== "string") throw new MockHttpError(422, "VALIDATION_ERROR", "content_hash is required", { field: "content_hash" });
  if (body.content_hash !== o.content_hash) {
    audit(rec.company_id, principal, "order.approve", "order", o.id, requestId, { reason: "content_hash mismatch" }, "error", now);
    throw new MockHttpError(409, "APPROVAL_CONTENT_CHANGED", "The order changed since you opened it. Review the current content and approve again.", {
      content_hash: o.content_hash,
    });
  }
  const { fresh, changed } = revalidate(o.lines, o.requested_delivery_date, now);
  if (changed) {
    const t = totals(fresh);
    const before = o.lines;
    o.lines = fresh;
    o.total_excl_vat_eur = t.excl;
    o.total_incl_vat_eur = t.incl;
    o.total_co2_kg = t.co2;
    o.content_hash = contentHash(fresh, t.excl, o.requested_delivery_date);
    o.policy = evaluatePolicy(o.created_by, fresh, t.excl, now, o.id, parseDietField(o.diet ?? null));
    event(o, "order.revalidated", null, `Prices/availability changed since the quote; new total ${eur(t.excl)}. New approval needed.`, requestId, now);
    audit(rec.company_id, principal, "order.approve", "order", o.id, requestId, { reason: "stale", before, after: fresh }, "error", now);
    throw new MockHttpError(409, "QUOTE_STALE", "Prices or availability changed since this order was created. Review the updated lines and approve again.", {
      lines: fresh,
      content_hash: o.content_hash,
      total_excl_vat_eur: t.excl,
    });
  }
  o.decided_by = principal;
  o.decided_at = isoAt(now);
  o.decision_note = typeof body.note === "string" && body.note ? body.note : o.decision_note;
  event(o, "approval.approved", principal, `Approved by ${principal.name}${typeof body.note === "string" && body.note ? `: "${body.note}"` : ""}`, requestId, now);
  startSubmission(rec, requestId, now);
  audit(rec.company_id, principal, "order.approve", "order", o.id, requestId, { content_hash: o.content_hash }, "ok", now);
  updateA2AForOrder(o, "working", `Approved by ${principal.name}; submitting to Valio Aimo`, now);
  return o;
}

export function rejectOrder(principal: Principal, id: string, body: { note?: unknown }, requestId: string, now = mockNowMs()): Order {
  requireUser(principal);
  const rec = findOrder(principal, id, now);
  const o = rec.order;
  if (o.status !== "PENDING_APPROVAL") throw new MockHttpError(409, "INVALID_STATE", `Order is ${o.status}, not PENDING_APPROVAL`, { status: o.status });
  o.status = "REJECTED";
  o.decided_by = principal;
  o.decided_at = isoAt(now);
  o.decision_note = typeof body.note === "string" && body.note ? body.note : null;
  event(o, "approval.rejected", principal, `Rejected by ${principal.name}${o.decision_note ? `: "${o.decision_note}"` : ""}`, requestId, now);
  audit(rec.company_id, principal, "order.reject", "order", o.id, requestId, { note: o.decision_note }, "ok", now);
  updateA2AForOrder(o, "canceled", `Rejected by ${principal.name}${o.decision_note ? `: ${o.decision_note}` : ""}`, now);
  return o;
}

export function cancelOrder(principal: Principal, id: string, body: { note?: unknown }, requestId: string, now = mockNowMs()): Order {
  const rec = findOrder(principal, id, now);
  const o = rec.order;
  const canCancel = principal.kind === "user" ? o.status === "PENDING_APPROVAL" || o.status === "PLACED" : o.status === "PENDING_APPROVAL";
  if (!canCancel) {
    throw new MockHttpError(
      409,
      "INVALID_STATE",
      principal.kind === "user"
        ? `Only PENDING_APPROVAL or PLACED orders can be cancelled (order is ${o.status})`
        : `Only PENDING_APPROVAL orders can be cancelled (order is ${o.status})`,
      { status: o.status },
    );
  }
  o.status = "CANCELLED";
  o.reservation_status = null;
  o.decision_note = typeof body.note === "string" && body.note ? body.note : o.decision_note;
  event(o, "order.cancelled", principal, `Cancelled by ${principal.name}`, requestId, now);
  audit(rec.company_id, principal, "order.cancel", "order", o.id, requestId, {}, "ok", now);
  updateA2AForOrder(o, "canceled", `Cancelled by ${principal.name}`, now);
  return o;
}

// ---------- A2A ----------

function updateA2AForOrder(o: Order, state: A2ATask["state"], message: string, at: number) {
  const task = db.a2a.find((t) => t.linked_order_id === o.id);
  if (!task || task.state === "completed" || task.state === "canceled" || task.state === "failed") return;
  task.state = state;
  task.updated_at = isoAt(at);
  // Same data shape as the real gateway: `reason` says why (ORDER_PLACED, or the order status), `order_status` on
  // working turns (SUBMITTING …).
  const data = state === "completed" ? { reason: "ORDER_PLACED", order_id: o.id, gateway_reference: o.gateway_reference } : { reason: o.status, order_id: o.id, order_status: o.status };
  task.history.push({ at: isoAt(at), state, role: "gateway", message, data });
}

export function addA2ATask(principal: Principal, history: A2AHistoryEntry[], linkedOrderId: string | null): A2ATask {
  const last = history[history.length - 1]!;
  const t = {
    id: newId("task"),
    state: last.state,
    principal,
    created_at: history[0]!.at,
    updated_at: last.at,
    linked_order_id: linkedOrderId,
    history,
    company_id: principal.company_id,
  };
  db.a2a.unshift(t);
  return t;
}

// ---------- Seed ----------

const at = (iso: string) => Date.parse(iso);

export function resetMockDb(): void {
  db = emptyDb();
  const c = SEED_COMPANY;
  db.companies.set(c.id, { ...c });
  db.users.push({ ...SEED_USER });
  const anna = { id: SEED_USER.id, name: SEED_USER.name };
  db.agents.push(
    {
      id: "agt_kitchen01",
      name: "Kitchen Reorder Bot",
      description: "Weekly dairy & bakery reorders for the Hotel Aurora kitchens",
      status: "active",
      auto_approve_limit_eur: 500,
      max_order_value_eur: 5000,
      daily_spend_limit_eur: 3000,
      allowed_categories: [],
      max_quantity_per_line: null,
      can_place_orders: true,
      delegated_by: anna,
      expires_at: null,
      created_at: "2026-11-02T09:15:00+02:00",
      last_used_at: "2026-11-14T09:12:00+02:00",
      company_id: c.id,
      secret: SEED_AGENT_SECRETS.agt_kitchen01,
      hard_diet: [],
    },
    {
      id: "agt_scout01",
      name: "Read-only Scout",
      description: "Price & availability monitoring; may not place orders",
      status: "active",
      auto_approve_limit_eur: 0,
      max_order_value_eur: 1000,
      daily_spend_limit_eur: 0,
      allowed_categories: [],
      max_quantity_per_line: null,
      can_place_orders: false,
      delegated_by: anna,
      expires_at: "2026-12-31T23:59:00+02:00",
      created_at: "2026-11-05T14:00:00+02:00",
      last_used_at: "2026-11-14T06:30:00+02:00",
      company_id: c.id,
      secret: SEED_AGENT_SECRETS.agt_scout01,
      hard_diet: [],
    },
    {
      id: "agt_ward01",
      name: "Ward Diet Bot",
      description: "Lactose-free (L) dairy for the hotel's allergy-safe kitchen. Hard diet rule: L only.",
      status: "active",
      auto_approve_limit_eur: 300,
      max_order_value_eur: 2000,
      daily_spend_limit_eur: 1000,
      allowed_categories: ["104"],
      max_quantity_per_line: 500,
      can_place_orders: true,
      delegated_by: anna,
      expires_at: null,
      created_at: "2026-11-10T11:00:00+02:00",
      last_used_at: null,
      company_id: c.id,
      secret: SEED_AGENT_SECRETS.agt_ward01,
      hard_diet: ["L"],
    },
  );
  const annaP = userPrincipal(db.users[0]!);
  const bot = agentPrincipal(db.agents[0]!);
  audit(c.id, annaP, "company.register", "company", c.id, newRequestId(), { name: c.name }, "ok", at("2026-11-02T09:00:00+02:00"));
  for (const a of db.agents) audit(c.id, annaP, "agent.create", "agent", a.id, newRequestId(), { name: a.name }, "ok", Date.parse(a.created_at));
  db.promotions.push(
    {
      id: "prm_valio_sale",
      company_id: c.id,
      title: "Prefer Valio on-sale items",
      description: "Prioritize Valio foodservice items currently discounted below list price.",
      brand_filter: "Valio",
      category_prefix: null,
      product_ids: [],
      boost_weight: 2.0,
      prefer_on_sale: true,
      active: true,
      created_by_user_id: anna.id,
      created_at: "2026-11-10T08:00:00+02:00",
    },
    {
      id: "prm_dairy_picks",
      company_id: c.id,
      title: "Lactose-free dairy staples",
      description: "Boost core lactose-free milk SKUs for breakfast and lunch prep.",
      brand_filter: null,
      category_prefix: "104",
      product_ids: ["100001", "100002"],
      boost_weight: 1.5,
      prefer_on_sale: false,
      active: true,
      created_by_user_id: anna.id,
      created_at: "2026-11-12T09:00:00+02:00",
    },
  );
  db.aimoCampaigns.push(
    {
      id: "cmp_valio_dairy",
      title: "Valio Dairy Autumn Priority",
      brand_filter: "Valio",
      category_prefix: "104",
      product_ids: ["100001", "100002"],
      boost_weight: 2.0,
      active: true,
      created_at: "2026-11-10T08:00:00+02:00",
    },
    {
      id: "cmp_oddlygood",
      title: "Oddlygood Oat Barista Push",
      brand_filter: "Oddlygood",
      category_prefix: null,
      product_ids: [],
      boost_weight: 1.5,
      active: true,
      created_at: "2026-11-11T09:00:00+02:00",
    },
  );
  db.brandPreferences.push({
    id: "bpf_dairy",
    company_id: c.id,
    category_prefix: "104",
    category_label: "Dairy & Plant-Based",
    preferred_brands: ["Valio", "Oddlygood"],
    active: true,
    updated_at: "2026-11-12T10:00:00+02:00",
  });
  db.companyBranches.push(
    {
      id: "brn_strand",
      company_id: c.id,
      name: "Hilton Helsinki Strand",
      short_name: "Hilton Strand",
      address: "John Stenbergin ranta 4, Helsinki",
    },
    {
      id: "brn_kalastaja",
      company_id: c.id,
      name: "Hilton Helsinki Kalastajantorppa",
      short_name: "Hilton Kalastajantorppa",
      address: "Kalastajatorpantie 1, Helsinki",
    },
  );
  db.negotiationSettings.set(c.id, {
    company_id: c.id,
    allow_price_negotiation: false,
    max_discount_override_pct: null,
    updated_at: null,
  });
  db.assistantSettings.set(c.id, {
    company_id: c.id,
    order_assurance_threshold: 80,
    min_threshold: 50,
    max_threshold: 100,
    updated_at: null,
  });

  // Seed additional reference kitchen tenants so logging in as any of the 3 demo personas works end-to-end.
  for (const extra of SEED_EXTRA_TENANTS) {
    db.companies.set(extra.company.id, { ...extra.company });
    const extraUser: UserRec = {
      ...extra.user,
      password: SEED_USER.password,
      company_id: extra.company.id,
    };
    db.users.push(extraUser);
    const delegatedBy = { id: extraUser.id, name: extraUser.name };
    db.agents.push(
      {
        id: `agt_kitchen_${extra.company.id}`,
        name: "Kitchen Agent",
        description: `Purchasing agent for ${extra.company.name}`,
        status: "active",
        auto_approve_limit_eur: 500,
        max_order_value_eur: 5000,
        daily_spend_limit_eur: 3000,
        allowed_categories: [],
        max_quantity_per_line: null,
        can_place_orders: true,
        delegated_by: delegatedBy,
        expires_at: null,
        created_at: "2026-11-02T09:15:00+02:00",
        last_used_at: "2026-11-14T09:12:00+02:00",
        company_id: extra.company.id,
        secret: `aks_demo_${extra.company.id}_kitchen`,
        hard_diet: [],
      },
      {
        id: `agt_reorder_${extra.company.id}`,
        name: "Reorder Bot",
        description: `Automated dairy & staple reorder bot for ${extra.company.name}`,
        status: "active",
        auto_approve_limit_eur: 300,
        max_order_value_eur: 2500,
        daily_spend_limit_eur: 1500,
        allowed_categories: [],
        max_quantity_per_line: 500,
        can_place_orders: true,
        delegated_by: delegatedBy,
        expires_at: null,
        created_at: "2026-11-05T10:00:00+02:00",
        last_used_at: null,
        company_id: extra.company.id,
        secret: `aks_demo_${extra.company.id}_reorder`,
        hard_diet: [],
      },
      {
        id: `agt_scout_${extra.company.id}`,
        name: "Read-only Scout",
        description: "Price & availability monitoring; may not place orders",
        status: "active",
        auto_approve_limit_eur: 0,
        max_order_value_eur: 1000,
        daily_spend_limit_eur: 0,
        allowed_categories: [],
        max_quantity_per_line: null,
        can_place_orders: false,
        delegated_by: delegatedBy,
        expires_at: "2026-12-31T23:59:00+02:00",
        created_at: "2026-11-06T14:00:00+02:00",
        last_used_at: null,
        company_id: extra.company.id,
        secret: `aks_demo_${extra.company.id}_scout`,
        hard_diet: [],
      },
    );
    db.negotiationSettings.set(extra.company.id, {
      company_id: extra.company.id,
      allow_price_negotiation: false,
      max_discount_override_pct: null,
      updated_at: null,
    });
    db.assistantSettings.set(extra.company.id, {
      company_id: extra.company.id,
      order_assurance_threshold: 80,
      min_threshold: 50,
      max_threshold: 100,
      updated_at: null,
    });
  }

  const seedOrder = (who: Principal, lines: { product_id: string; quantity: number }[], date: string, createdIso: string) => {
    const t = at(createdIso);
    const q = createQuote(who, { requested_delivery_date: date, lines }, t);
    return createOrder(who, { quote_id: q.id }, newRequestId(), t);
  };
  const settleAt = (o: Order, iso: string) => settleOrder(db.orders.find((r) => r.order.id === o.id)!, at(iso));

  // 1) Rejected agent order, Thu 12 Nov
  const o4 = seedOrder(bot, [{ product_id: "100002", quantity: 2000 }], "2026-11-13", "2026-11-12T09:00:00+02:00");
  rejectOrder(annaP, o4.id, { note: "Too much UHT, the dry store is full" }, newRequestId(), at("2026-11-12T09:40:00+02:00"));

  // 2) Anna orders cream directly, Fri 13 Nov 08:30 → PLACED
  const o1 = seedOrder(annaP, [{ product_id: "100006", quantity: 12 }], "2026-11-16", "2026-11-13T08:30:00+02:00");
  settleAt(o1, "2026-11-13T08:31:00+02:00");

  // 3) Agent order needing approval, approved by Anna, Fri 13 Nov → PLACED, with an A2A trace
  const o2 = seedOrder(bot, [{ product_id: "100001", quantity: 600 }], "2026-11-16", "2026-11-13T14:05:00+02:00");
  addA2ATask(
    bot,
    [
      { at: "2026-11-13T14:02:10+02:00", state: "working", role: "agent", message: "Need 600 L lactose-free milk for Monday breakfast service" },
      { at: "2026-11-13T14:02:12+02:00", state: "input-required", role: "gateway", message: "Lactose-free milk: chilled (100001) or UHT ambient (100002)?", data: { reason: "CLARIFICATION_REQUIRED", missing_slots: ["storage_temperature"], options: [mockOption("100001", "chilled"), mockOption("100002", "uht")] } },
      { at: "2026-11-13T14:04:40+02:00", state: "input-required", role: "agent", message: "Select 100001 (chilled)", data: { action: "select_option", product_id: "100001" } },
      { at: "2026-11-13T14:05:00+02:00", state: "input-required", role: "gateway", message: `Order ${o2.id} requires human approval (EUR 606.00 excl. VAT; rules: R-AUTO-LIMIT)`, data: { reason: "PENDING_APPROVAL", order_id: o2.id, linked_order_id: o2.id, rule_ids: ["R-AUTO-LIMIT"], policy: { outcome: "APPROVAL_REQUIRED" } } },
    ],
    o2.id,
  );
  approveOrder(annaP, o2.id, { content_hash: o2.content_hash, note: "OK for Monday" }, newRequestId(), at("2026-11-13T14:20:00+02:00"));
  settleAt(o2, "2026-11-13T14:21:00+02:00");
  const o2Audit = db.audit.find((a) => a.entity_id === o2.id && a.action === "order.create");
  if (o2Audit) {
    o2Audit.details = {
      ...o2Audit.details,
      reasoning_steps: [
        { step: 1, stage: "user_prompt", summary: "Agent requested 600 L lactose-free milk for Monday breakfast service", actor_kind: "agent", at: "2026-11-13T14:02:10+02:00", details: { query: "lactose-free milk", qty: 600 } },
        { step: 2, stage: "promotion_recommendation", summary: "Applied management priority: Lactose-free dairy staples (-19% vs list)", actor_kind: "system", at: "2026-11-13T14:04:42+02:00", details: { promotion: "Lactose-free dairy staples", sku: "100001" } },
        { step: 3, stage: "policy_evaluation", summary: "Evaluated Layer 1 guardrails: EUR 606.00 > EUR 500.00 auto-approve limit (R-AUTO-LIMIT)", actor_kind: "system", at: "2026-11-13T14:05:00+02:00", details: { rule_id: "R-AUTO-LIMIT", outcome: "APPROVAL_REQUIRED" } },
      ],
    };
  }

  // 4) Agent auto order today 07:40 → PLACED
  const o3 = seedOrder(bot, [{ product_id: "100002", quantity: 200 }], "2026-11-16", "2026-11-14T07:40:00+02:00");
  settleAt(o3, "2026-11-14T07:41:00+02:00");

  // 5) The live demo card: 1,000 × 100001 = €1,010.00 → PENDING_APPROVAL (R-AUTO-LIMIT)
  const o5 = seedOrder(bot, [{ product_id: "100001", quantity: 1000 }], "2026-11-16", "2026-11-14T09:12:00+02:00");
  const o5Audit = db.audit.find((a) => a.entity_id === o5.id && a.action === "order.create");
  if (o5Audit) {
    o5Audit.details = {
      ...o5Audit.details,
      reasoning_steps: [
        { step: 1, stage: "user_prompt", summary: "Requested 1,000 L lactose-free milk by Monday", actor_kind: "agent", at: "2026-11-14T09:11:20+02:00", details: { qty: 1000, diet: "L" } },
        { step: 2, stage: "safety_screen", summary: "Confirmed 100001 meets L (Lactose-free) supplier specification", actor_kind: "system", at: "2026-11-14T09:11:58+02:00", details: { sku: "100001", diet_verified: "L" } },
        { step: 3, stage: "policy_evaluation", summary: "Total EUR 1,010.00 exceeds auto-approve limit EUR 500.00 (R-AUTO-LIMIT)", actor_kind: "system", at: "2026-11-14T09:12:00+02:00", details: { rule_id: "R-AUTO-LIMIT", outcome: "APPROVAL_REQUIRED" } },
      ],
    };
  }
  addA2ATask(
    bot,
    [
      { at: "2026-11-14T09:11:20+02:00", state: "working", role: "agent", message: "We need 1,000 litres of lactose-free milk by Monday for our hotels" },
      { at: "2026-11-14T09:11:22+02:00", state: "input-required", role: "gateway", message: "100003 has only 384 ST in stock for Mon 16 Nov. Split the order or switch to 100001 (chilled, unlimited)?", data: { reason: "CLARIFICATION_REQUIRED", options: ["split", "switch"] } },
      { at: "2026-11-14T09:11:58+02:00", state: "input-required", role: "agent", message: "Switch: 1,000 × 100001", data: { action: "select_option", product_id: "100001" } },
      { at: "2026-11-14T09:12:00+02:00", state: "input-required", role: "gateway", message: `Order ${o5.id} requires human approval (EUR 1010.00 excl. VAT; rules: R-AUTO-LIMIT). Waiting for a human.`, data: { reason: "PENDING_APPROVAL", order_id: o5.id, linked_order_id: o5.id, rule_ids: ["R-AUTO-LIMIT"], policy: { outcome: "APPROVAL_REQUIRED" } } },
    ],
    o5.id,
  );
}

/** A clarification option in the shape the real gateway sends (`data.options[]` of an input-required turn). */
function mockOption(productId: string, storage: "chilled" | "uht") {
  const p = PRODUCTS.find((x) => x.id === productId);
  return { option_id: `opt_${storage}_${productId}`, action: "select_option", product_id: productId, name: p?.name ?? productId, storage };
}

/** Dev helper: a new agent order arrives (default 1,000 × 100001 by Kitchen Reorder Bot). */
export function simulateAgentOrder(opts: { agentId?: string; productId?: string; quantity?: number; date?: string } = {}): Order {
  const now = mockNowMs();
  const a = db.agents.find((x) => x.id === (opts.agentId ?? "agt_kitchen01"));
  if (!a) throw new Error("unknown agent");
  const p = agentPrincipal(a);
  const q = createQuote(p, { requested_delivery_date: opts.date ?? defaultDeliveryDate(now), lines: [{ product_id: opts.productId ?? "100001", quantity: opts.quantity ?? 1000 }] }, now);
  const rid = newRequestId();
  audit(p.company_id, p, "quote.create", "quote", q.id, rid, { total_excl_vat_eur: q.total_excl_vat_eur }, "ok", now);
  const o = createOrder(p, { quote_id: q.id }, rid, now);
  addA2ATask(
    p,
    [
      { at: isoAt(now - 3000), state: "working", role: "agent", message: `Order ${fmtQty(opts.quantity ?? 1000)} × ${opts.productId ?? "100001"}` },
      o.status === "PENDING_APPROVAL"
        ? { at: isoAt(now), state: "input-required", role: "gateway", message: `Order ${o.id} requires human approval (${o.policy.rules.map((r) => r.rule_id).join(", ")})`, data: { reason: "PENDING_APPROVAL", order_id: o.id, rule_ids: o.policy.rules.map((r) => r.rule_id) } }
        : { at: isoAt(now), state: "working", role: "gateway", message: `Order ${o.id} is ${o.status}`, data: { reason: o.status, order_id: o.id, order_status: o.status } },
    ],
    o.id,
  );
  return o;
}

export function allProducts(): Product[] {
  return PRODUCTS;
}

resetMockDb();
