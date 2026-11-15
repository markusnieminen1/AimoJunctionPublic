// MSW handlers for every endpoint in docs/API_CONTRACT.md + additive /api/products/substitutes and /api/a2a/tasks.
// Every response carries X-Request-ID and X-Server-Time; every error uses the contract envelope.
import { http, HttpResponse, type HttpResponseResolver } from "msw";
import type {
  A2ASupportedMatter,
  AgentCreateRequest,
  AgentUpdateRequest,
  CompanyBranchOut,
  DietCode,
  OrderStatus,
  Principal,
  Product,
  RoutingInspectResponse,
  SubstituteItem,
  TelegramLinkTokenOut,
  TelegramOptInOut,
} from "../api/types";
import { productDiets } from "../domain/diets";
import { temperatureInfo } from "../domain/temperature";
import { helsinkiDate } from "../lib/dates";
import { assistantChat } from "./assistant";
import { isoAt, mockNowMs } from "./clock";
import { PRODUCTS, SEED_DEMO_PERSONAS, productById } from "./data";
import { buildDealsResponse, buildGovernanceSummaryResponse, buildMenuPlanResponse, buildOrderTraceResponse } from "./m9";
import { buildQuoteOptimizeResponse } from "./optimizer";
import { runMockSimulatorScenario, SIMULATOR_SCENARIOS } from "./simulator";
import {
  MockHttpError,
  agentPrincipal,
  approveOrder,
  audit,
  buildLine,
  cancelOrder,
  createOrder,
  parseDietField,
  createQuote,
  db,
  findOrder,
  issueToken,
  newId,
  newRequestId,
  principalFor,
  refreshQuote,
  rejectOrder,
  settleOrder,
  toAgent,
  userPrincipal,
  validateDate,
} from "./db";

function buildSupportedMatters(negotiationEnabled: boolean, companyName?: string): A2ASupportedMatter[] {
  const scopeLabel = companyName ? ` for ${companyName}` : "";
  return [
    {
      id: "menu_planning",
      title: "Menu Planning",
      summary: "Plan allergen-checked weekly kitchen menus from Valio Aimo wholesale inventory.",
      enabled: true,
      tools_or_skills: ["plan_menu", "search_products", "check_product_suitability"],
    },
    {
      id: "order_execution",
      title: "Order Execution",
      summary: "Create deterministic quotes, verify stock/diet compliance, and place orders with idempotency.",
      enabled: true,
      tools_or_skills: ["create_quote", "place_order", "get_order_status"],
    },
    {
      id: "preliminary_tendering",
      title: "Preliminary Tendering",
      summary: negotiationEnabled
        ? `Enabled${scopeLabel}: can negotiate prices for larger quantities and campaign discounts (managed in /admin).`
        : `Disabled${scopeLabel}: price negotiation for larger quantities is turned off (managed in /admin).`,
      enabled: negotiationEnabled,
      tools_or_skills: negotiationEnabled ? ["negotiate_quote", "list_deals"] : ["list_deals"],
    },
    {
      id: "exception_handling",
      title: "Exception Handling",
      summary: "Resolve out-of-stock substitutes, 5h holds, recipe sanity checks, and human approvals.",
      enabled: true,
      tools_or_skills: ["suggest_substitutes", "submit_for_approval"],
    },
  ];
}

const API = "*/api";
const USER_TOKEN_TTL = 12 * 3600_000;
const AGENT_TOKEN_TTL = 60 * 60_000;

type Auth = "none" | "any" | "user";
type Ctx = {
  request: Request;
  url: URL;
  params: Record<string, string>;
  body: Record<string, unknown>;
  principal: Principal;
  requestId: string;
  now: number;
};
type Result = { status?: number; body: unknown; headers?: Record<string, string> };

function headers(requestId: string, extra: Record<string, string> = {}) {
  return { "X-Request-ID": requestId, "X-Server-Time": isoAt(mockNowMs()), ...extra };
}

function errorResponse(e: MockHttpError, requestId: string) {
  return HttpResponse.json(
    { error: { code: e.code, message: e.message, details: e.details }, request_id: requestId },
    { status: e.status, headers: headers(requestId) },
  );
}

function authenticate(request: Request, now: number): Principal | null {
  const h = request.headers.get("Authorization");
  if (!h || !h.startsWith("Bearer ")) return null;
  const rec = db.tokens.get(h.slice(7));
  if (!rec || rec.expires_ms < now) throw new MockHttpError(401, "UNAUTHENTICATED", "Session expired or invalid. Log in again.");
  if (rec.kind === "agent") {
    const a = db.agents.find((x) => x.id === rec.id);
    if (!a) throw new MockHttpError(401, "UNAUTHENTICATED", "Unknown agent");
    if (a.status !== "active") throw new MockHttpError(403, "AGENT_DISABLED", `Agent ${a.name} is disabled`);
    a.last_used_at = isoAt(now);
  }
  return principalFor(rec.kind, rec.id);
}

/** Wraps a resolver: request id, auth, JSON body, idempotency (when `idempotent`), error envelope. */
function route(auth: Auth, fn: (ctx: Ctx) => Result | Promise<Result>, opts: { idempotent?: "required" | "optional" } = {}): HttpResponseResolver {
  return async ({ request, params }) => {
    const requestId = newRequestId();
    try {
      const now = mockNowMs();
      let principal: Principal | null = null;
      try {
        principal = authenticate(request, now);
      } catch (e) {
        if (auth !== "none") throw e;
      }
      if (auth !== "none" && !principal) throw new MockHttpError(401, "UNAUTHENTICATED", "Missing bearer token");
      if (auth === "user" && principal!.kind !== "user") throw new MockHttpError(403, "FORBIDDEN", "This endpoint is for human users only");
      let body: Record<string, unknown> = {};
      if (request.method !== "GET" && request.method !== "DELETE") {
        const text = await request.text();
        if (text) {
          try {
            body = JSON.parse(text) as Record<string, unknown>;
          } catch {
            throw new MockHttpError(422, "VALIDATION_ERROR", "Request body is not valid JSON");
          }
        }
      }
      const url = new URL(request.url);
      const idemKey = request.headers.get("Idempotency-Key");
      if (opts.idempotent === "required" && !idemKey) throw new MockHttpError(422, "VALIDATION_ERROR", "Idempotency-Key header is required", { header: "Idempotency-Key" });
      const storeKey = idemKey && opts.idempotent ? `${principal?.id}|${request.method} ${url.pathname}|${idemKey}` : null;
      const bodySig = JSON.stringify(body);
      if (storeKey) {
        const prev = db.idem.get(storeKey);
        if (prev) {
          if (prev.body !== bodySig) throw new MockHttpError(409, "CONFLICT", "Idempotency-Key reused with a different request body");
          return HttpResponse.json(prev.response as never, { status: prev.status, headers: headers(requestId, { "Idempotent-Replayed": "true" }) });
        }
      }
      let result: Result;
      try {
        result = await fn({ request, url, params: params as Record<string, string>, body, principal: principal!, requestId, now });
      } catch (e) {
        if (storeKey && e instanceof MockHttpError && e.status < 500 && e.code !== "QUOTE_STALE" && e.code !== "APPROVAL_CONTENT_CHANGED") {
          db.idem.set(storeKey, { body: bodySig, status: e.status, response: { error: { code: e.code, message: e.message, details: e.details }, request_id: requestId } });
        }
        throw e;
      }
      if (storeKey) db.idem.set(storeKey, { body: bodySig, status: result.status ?? 200, response: result.body });
      return HttpResponse.json(result.body as never, { status: result.status ?? 200, headers: headers(requestId, result.headers) });
    } catch (e) {
      if (e instanceof MockHttpError) return errorResponse(e, requestId);
      console.error("[mock] internal error", e);
      return errorResponse(new MockHttpError(500, "INTERNAL", e instanceof Error ? e.message : "Internal error"), requestId);
    }
  };
}

function page<T>(items: T[], url: URL) {
  const limit = Math.min(100, Math.max(1, Number(url.searchParams.get("limit") ?? 20) || 20));
  const offset = Math.max(0, Number(url.searchParams.get("offset") ?? 0) || 0);
  return { items: items.slice(offset, offset + limit), total: items.length };
}

function agentRec(ctx: Ctx) {
  const a = db.agents.find((x) => x.id === ctx.params.id && x.company_id === ctx.principal.company_id);
  if (!a) throw new MockHttpError(404, "NOT_FOUND", `Agent ${ctx.params.id} not found`);
  return a;
}

function num(v: unknown, field: string, opts: { nullable?: boolean } = {}): number | null {
  if (v === null && opts.nullable) return null;
  if (typeof v !== "number" || Number.isNaN(v) || v < 0) throw new MockHttpError(422, "VALIDATION_ERROR", `${field} must be a number ≥ 0`, { field });
  return v;
}

function newSecret(): string {
  let s = "aks_";
  const abc = "abcdefghijklmnopqrstuvwxyz0123456789";
  for (let i = 0; i < 32; i++) s += abc[Math.floor(Math.random() * abc.length)];
  return s;
}

// ---------- product search helpers ----------

const ALLERGEN_FREE_TERMS: Record<string, { codes: string[]; labels: string[] }> = {
  lactose: { codes: ["ML", "AM"], labels: ["FREE_FROM_LACTOSE"] },
  milk: { codes: ["AM"], labels: ["FREE_FROM_MILK"] },
  gluten: { codes: ["AW"], labels: ["FREE_FROM_GLUTEN"] },
  wheat: { codes: ["UW", "AW"], labels: ["FREE_FROM_WHEAT", "FREE_FROM_GLUTEN"] },
  egg: { codes: ["AE"], labels: ["FREE_FROM_EGGS"] },
  eggs: { codes: ["AE"], labels: ["FREE_FROM_EGGS"] },
  soy: { codes: ["AY"], labels: ["FREE_FROM_SOY"] },
  fish: { codes: ["AF"], labels: ["FREE_FROM_FISH"] },
  nuts: { codes: ["AN"], labels: ["FREE_FROM_NUTS"] },
  peanut: { codes: ["AP"], labels: ["FREE_FROM_PEANUTS"] },
};

function confirmedFree(p: Product, term: string): boolean {
  const t = ALLERGEN_FREE_TERMS[term.trim().toLowerCase()];
  if (!t) return true;
  const contains = p.allergens.some((a) => t.codes.includes(a.split(":")[0]!));
  if (term === "lactose" && p.labels.includes("FREE_FROM_LACTOSE")) return true;
  if (contains) return false;
  return t.labels.some((l) => p.labels.includes(l)) || t.codes.some((c) => p.non_allergens.includes(c));
}

function searchProducts(url: URL): Product[] {
  const q = (url.searchParams.get("q") ?? "").trim().toLowerCase();
  const category = url.searchParams.get("category");
  const free = (url.searchParams.get("allergen_free") ?? "").split(",").filter(Boolean);
  const label = url.searchParams.get("label");
  const diet = (url.searchParams.get("diet") ?? "").split(",").filter(Boolean) as DietCode[];
  return PRODUCTS.filter((p) => {
    if (q) {
      const hay = [p.id, p.name, p.names.fi, p.names.en, p.names.sv, p.brand, p.vendor, p.category_name].filter(Boolean).join(" ").toLowerCase();
      if (!q.split(/\s+/).every((w) => hay.includes(w))) return false;
    }
    if (category && !p.category.startsWith(category)) return false;
    if (label && !p.labels.includes(label)) return false;
    if (!free.every((f) => confirmedFree(p, f))) return false;
    if (diet.length && !diet.every((d) => productDiets(p).includes(d))) return false;
    return true;
  });
}

// ---------- handlers ----------

export const handlers = [
  http.get(`${API}/health`, route("none", ({ now }) => ({ body: { status: "ok", db: "ok", aimo: "ok", aimo_mode: "fixture", server_time: isoAt(now), version: "mock-0.1.0" } }))),

  // Auth
  http.post(
    `${API}/auth/register`,
    route("none", ({ body, requestId, now }) => {
      const { company_name, business_id, user_name, email, password } = body as Record<string, string | undefined>;
      for (const [k, v] of Object.entries({ company_name, user_name, email, password })) {
        if (typeof v !== "string" || !v.trim()) throw new MockHttpError(422, "VALIDATION_ERROR", `${k} is required`, { field: k });
      }
      if (!/^\S+@\S+\.\S+$/.test(email!)) throw new MockHttpError(422, "VALIDATION_ERROR", "email is not valid", { field: "email" });
      if (password!.length < 8) throw new MockHttpError(422, "VALIDATION_ERROR", "password must be at least 8 characters", { field: "password" });
      if (db.users.some((u) => u.email.toLowerCase() === email!.toLowerCase())) throw new MockHttpError(409, "EMAIL_TAKEN", "An account with this email already exists");
      const company = { id: newId("cmp"), name: company_name!.trim(), business_id };
      db.companies.set(company.id, company);
      const user = { id: newId("usr"), name: user_name!.trim(), email: email!.trim(), password: password!, company_id: company.id };
      db.users.push(user);
      const p = userPrincipal(user);
      audit(company.id, p, "company.register", "company", company.id, requestId, { name: company.name }, "ok", now);
      const { token } = issueToken("user", user.id, USER_TOKEN_TTL, now);
      return { status: 201, body: { token, user: p, company: { id: company.id, name: company.name } } };
    }),
  ),
  http.post(
    `${API}/auth/login`,
    route("none", ({ body, requestId, now }) => {
      const legacyAliases: Record<string, string> = {
        "demo@aimoannos.test": "anna.korhonen@hotel.fi",
        "mikko@bistrosatama.test": "mikko.laine@restaurant.fi",
        "sari@nordiccampus.test": "sari.virtanen@festival.fi",
      };
      const rawEmail = String(body.email ?? "").trim().toLowerCase();
      const targetEmail = legacyAliases[rawEmail] ?? rawEmail;
      const u = db.users.find((x) => x.email.toLowerCase() === targetEmail && x.password === body.password);
      if (!u) throw new MockHttpError(401, "UNAUTHENTICATED", "Wrong email or password");
      const { token } = issueToken("user", u.id, USER_TOKEN_TTL, now);
      audit(u.company_id, userPrincipal(u), "auth.login", "user", u.id, requestId, {}, "ok", now);
      return { body: { token, user: userPrincipal(u) } };
    }),
  ),
  http.get(
    `${API}/me`,
    route("any", ({ principal, now }) => {
      const c = db.companies.get(principal.company_id)!;
      const a = principal.kind === "agent" ? db.agents.find((x) => x.id === principal.id) : undefined;
      return { body: { principal, company: { id: c.id, name: c.name }, ...(a ? { agent: toAgent(a, now) } : {}) } };
    }),
  ),
  http.post(
    `${API}/agent-auth/token`,
    route("none", ({ body, requestId, now }) => {
      const a = db.agents.find((x) => x.id === body.agent_id);
      if (!a || a.secret !== body.agent_secret) throw new MockHttpError(401, "UNAUTHENTICATED", "Invalid agent_id or agent_secret");
      if (a.status !== "active") throw new MockHttpError(403, "AGENT_DISABLED", `Agent ${a.name} is disabled`);
      if (a.expires_at && Date.parse(a.expires_at) < now) throw new MockHttpError(401, "UNAUTHENTICATED", "Agent credentials have expired");
      const { token, expires_at } = issueToken("agent", a.id, AGENT_TOKEN_TTL, now);
      a.last_used_at = isoAt(now);
      audit(a.company_id, agentPrincipal(a), "agent.token", "agent", a.id, requestId, {}, "ok", now);
      return { body: { token, expires_at, agent: toAgent(a, now) } };
    }),
  ),

  // Agents (user only)
  http.get(
    `${API}/auth/demo-personas`,
    route("none", () => ({
      body: {
        items: SEED_DEMO_PERSONAS.map((p) => ({ ...p })),
      },
    })),
  ),
  http.get(
    `${API}/agents/anomalies`,
    route("user", ({ principal }) => {
      const items = db.anomalies.filter((a) => a.company_id === principal.company_id);
      return { body: { items } };
    }),
  ),
  http.post(
    `${API}/agents/:id/review`,
    route("user", (ctx) => {
      const a = agentRec(ctx);
      const b = ctx.body as { action?: unknown; note?: unknown };
      if (b.action !== "restore" && b.action !== "revoke") {
        throw new MockHttpError(422, "VALIDATION_ERROR", "action must be 'restore' or 'revoke'", { field: "action" });
      }
      a.status = b.action === "restore" ? "active" : "disabled";
      db.anomalies = db.anomalies.filter((item) => !(item.agent_id === a.id && item.company_id === ctx.principal.company_id));
      audit(
        ctx.principal.company_id,
        ctx.principal,
        "agent.quarantine_reviewed",
        "agent",
        a.id,
        ctx.requestId,
        { action: b.action, note: typeof b.note === "string" ? b.note : null, new_status: a.status },
        "ok",
        ctx.now,
      );
      return { body: { agent: toAgent(a, ctx.now), action: b.action, reviewed_at: isoAt(ctx.now) } };
    }),
  ),
  http.get(
    `${API}/agents`,
    route("user", ({ principal, now }) => {
      const items = db.agents.filter((a) => a.company_id === principal.company_id).map((a) => toAgent(a, now));
      return { body: { items, total: items.length } };
    }),
  ),
  http.post(
    `${API}/agents`,
    route("user", ({ principal, body, requestId, now }) => {
      const b = body as Partial<AgentCreateRequest>;
      if (typeof b.name !== "string" || !b.name.trim()) throw new MockHttpError(422, "VALIDATION_ERROR", "name is required", { field: "name" });
      const auto = num(b.auto_approve_limit_eur, "auto_approve_limit_eur")!;
      const max = num(b.max_order_value_eur, "max_order_value_eur")!;
      const daily = num(b.daily_spend_limit_eur, "daily_spend_limit_eur")!;
      if (auto > max) throw new MockHttpError(422, "VALIDATION_ERROR", "auto_approve_limit_eur must be ≤ max_order_value_eur", { field: "auto_approve_limit_eur" });
      const secret = newSecret();
      const rec = {
        id: newId("agt"),
        name: b.name.trim(),
        description: b.description ?? null,
        status: "active" as const,
        auto_approve_limit_eur: auto,
        max_order_value_eur: max,
        daily_spend_limit_eur: daily,
        allowed_categories: Array.isArray(b.allowed_categories) ? b.allowed_categories : [],
        max_quantity_per_line: b.max_quantity_per_line == null ? null : num(b.max_quantity_per_line, "max_quantity_per_line"),
        can_place_orders: b.can_place_orders ?? true,
        delegated_by: { id: principal.id, name: principal.name },
        expires_at: b.expires_at ?? null,
        created_at: isoAt(now),
        last_used_at: null,
        company_id: principal.company_id,
        secret,
        hard_diet: [] as DietCode[],
      };
      db.agents.push(rec);
      audit(principal.company_id, principal, "agent.create", "agent", rec.id, requestId, { name: rec.name, auto_approve_limit_eur: auto, max_order_value_eur: max }, "ok", now);
      return { status: 201, body: { agent: toAgent(rec, now), agent_secret: secret } };
    }),
  ),
  http.get(`${API}/agents/:id`, route("user", (ctx) => ({ body: toAgent(agentRec(ctx), ctx.now) }))),
  http.patch(
    `${API}/agents/:id`,
    route("user", (ctx) => {
      const a = agentRec(ctx);
      const b = ctx.body as AgentUpdateRequest;
      const before = toAgent(a, ctx.now);
      if (b.name !== undefined) {
        if (typeof b.name !== "string" || !b.name.trim()) throw new MockHttpError(422, "VALIDATION_ERROR", "name must not be empty", { field: "name" });
        a.name = b.name.trim();
      }
      if (b.description !== undefined) a.description = b.description;
      if (b.auto_approve_limit_eur !== undefined) a.auto_approve_limit_eur = num(b.auto_approve_limit_eur, "auto_approve_limit_eur")!;
      if (b.max_order_value_eur !== undefined) a.max_order_value_eur = num(b.max_order_value_eur, "max_order_value_eur")!;
      if (b.daily_spend_limit_eur !== undefined) a.daily_spend_limit_eur = num(b.daily_spend_limit_eur, "daily_spend_limit_eur")!;
      if (b.allowed_categories !== undefined) a.allowed_categories = b.allowed_categories ?? [];
      if (b.max_quantity_per_line !== undefined) a.max_quantity_per_line = num(b.max_quantity_per_line, "max_quantity_per_line", { nullable: true });
      if (b.can_place_orders !== undefined) a.can_place_orders = !!b.can_place_orders;
      if (b.expires_at !== undefined) a.expires_at = b.expires_at;
      if (b.status !== undefined) {
        if (b.status !== "active" && b.status !== "disabled") throw new MockHttpError(422, "VALIDATION_ERROR", "status must be active or disabled", { field: "status" });
        a.status = b.status;
      }
      if (a.auto_approve_limit_eur > a.max_order_value_eur) throw new MockHttpError(422, "VALIDATION_ERROR", "auto_approve_limit_eur must be ≤ max_order_value_eur", { field: "auto_approve_limit_eur" });
      const after = toAgent(a, ctx.now);
      const action = b.status === "disabled" ? "agent.disable" : b.status === "active" && before.status === "disabled" ? "agent.enable" : "agent.update";
      audit(a.company_id, ctx.principal, action, "agent", a.id, ctx.requestId, { before, after }, "ok", ctx.now);
      return { body: after };
    }),
  ),
  http.post(
    `${API}/agents/:id/rotate-secret`,
    route("user", (ctx) => {
      const a = agentRec(ctx);
      a.secret = newSecret();
      for (const [tok, rec] of db.tokens) if (rec.kind === "agent" && rec.id === a.id) db.tokens.delete(tok);
      audit(a.company_id, ctx.principal, "agent.rotate_secret", "agent", a.id, ctx.requestId, {}, "ok", ctx.now);
      return { body: { agent: toAgent(a, ctx.now), agent_secret: a.secret } };
    }),
  ),
  http.get(
    `${API}/agents/:id/activity`,
    route("user", (ctx) => {
      const a = agentRec(ctx);
      const items = db.audit.filter((e) => e.company_id === a.company_id && (e.actor?.id === a.id || e.entity_id === a.id)).map(({ company_id: _c, ...e }) => e);
      return { body: page(items, ctx.url) };
    }),
  ),

  // Products
  http.get(`${API}/products`, route("any", ({ url }) => ({ body: page(searchProducts(url), url) }))),
  http.get(
    `${API}/products/deals`,
    route("any", ({ principal, url, now }) => {
      const sp = url.searchParams;
      const limit = sp.get("limit") ? Number(sp.get("limit")) : undefined;
      return {
        body: buildDealsResponse(
          principal,
          {
            q: sp.get("q") ?? undefined,
            category: sp.get("category") ?? undefined,
            diet: sp.get("diet") ?? undefined,
            valio_only: sp.get("valio_only") === "true",
            on_sale_only: sp.get("on_sale_only") === "true",
            requested_delivery_date: sp.get("requested_delivery_date") ?? undefined,
            limit: limit && !Number.isNaN(limit) ? limit : undefined,
          },
          now,
        ),
      };
    }),
  ),
  http.get(
    `${API}/categories`,
    route("any", () => {
      const map = new Map<string, { code: string; name: string; product_count: number }>();
      for (const p of PRODUCTS) {
        const c = map.get(p.category) ?? { code: p.category, name: p.category_name ?? p.category, product_count: 0 };
        c.product_count++;
        map.set(p.category, c);
      }
      return { body: { items: [...map.values()].sort((a, b) => a.code.localeCompare(b.code)) } };
    }),
  ),
  http.get(
    `${API}/products/:id`,
    route("any", ({ params }) => {
      const p = productById(params.id!);
      if (!p) throw new MockHttpError(404, "NOT_FOUND", `Product ${params.id} not found`);
      return { body: p };
    }),
  ),
  http.post(
    `${API}/products/check`,
    route("any", ({ body, now }) => {
      const ids = body.product_ids;
      if (!Array.isArray(ids) || ids.length < 1 || ids.length > 50) throw new MockHttpError(422, "VALIDATION_ERROR", "product_ids must contain 1..50 ids", { field: "product_ids" });
      const date = validateDate(body.requested_delivery_date);
      const qty = (body.quantities ?? {}) as Record<string, number>;
      const items = (ids as string[]).map((id) => {
        const p = productById(id);
        if (!p) throw new MockHttpError(422, "UNKNOWN_PRODUCT", `Unknown product ${id}`, { product_id: id });
        return buildLine(p, typeof qty[id] === "number" && qty[id]! > 0 ? qty[id]! : 1, date, now);
      });
      return { body: { items } };
    }),
  ),
  http.post(
    `${API}/products/substitutes`,
    route("any", ({ body, now }) => {
      const orig = productById(String(body.product_id ?? ""));
      if (!orig) throw new MockHttpError(422, "UNKNOWN_PRODUCT", `Unknown product ${String(body.product_id)}`);
      const qty = typeof body.quantity === "number" && body.quantity > 0 ? body.quantity : 1;
      const date = validateDate(body.requested_delivery_date);
      // Like the backend: explicit diet + the diets the original already meets (L/M/G/VEG) are all required.
      const inferred = productDiets(orig).filter((d) => d === "L" || d === "M" || d === "G" || d === "VEG");
      const required = [...new Set<DietCode>([...parseDietField(body.diet), ...inferred])];
      const origLine = buildLine(orig, qty, date, now);
      const perSU = (p: Product) => p.units.find((u) => u.unit_id === p.sales_unit)?.size_in_base_units ?? 1;
      const items: SubstituteItem[] = [];
      for (const p of PRODUCTS) {
        if (p.id === orig.id || p.category !== orig.category) continue;
        if (!required.every((d) => productDiets(p).includes(d))) continue;
        const subQty = Math.ceil((qty * perSU(orig)) / perSU(p));
        const line = buildLine(p, subQty, date, now);
        if (line.delivery_status !== "ON_TIME" || line.unit_price_eur == null) continue;
        const trade: string[] = [];
        const tOrig = temperatureInfo(orig.temperature_condition);
        const tSub = temperatureInfo(p.temperature_condition);
        trade.push(
          tOrig.zone === tSub.zone
            ? `${tSub.label}${tSub.range ? ` ${tSub.range}` : ""}, same as original`
            : `${tSub.label}${tSub.range ? ` ${tSub.range}` : ""} instead of ${tOrig.label.toLowerCase()}${tOrig.range ? ` ${tOrig.range}` : ""}`,
        );
        if (p.sales_unit !== orig.sales_unit || perSU(p) !== perSU(orig)) {
          trade.push(`${subQty.toLocaleString("en-US")} ${p.sales_unit} × ${perSU(p)} ${p.base_unit} replaces ${qty.toLocaleString("en-US")} ${orig.sales_unit}`);
        }
        if (origLine.line_total_eur != null && line.line_total_eur != null) {
          const delta = Math.round((line.line_total_eur - origLine.line_total_eur) * 100) / 100;
          trade.push(delta === 0 ? "Same line total" : `${delta > 0 ? "+" : "−"}€${Math.abs(delta).toFixed(2)} line total vs original`);
        }
        if (p.brand !== orig.brand && p.brand) trade.push(`Brand: ${p.brand}`);
        const dietTxt = required.length ? required.join(", ") + " kept · " : "";
        items.push({ product: p, line, trade_offs: trade, reason: `${dietTxt}full quantity on time for ${date}` });
      }
      items.sort((a, b) => (a.line.line_total_eur ?? 0) - (b.line.line_total_eur ?? 0));
      return { body: { items: items.slice(0, 5) } };
    }),
  ),

  // Quotes
  http.post(
    `${API}/quotes`,
    route("any", ({ principal, body, requestId, now }) => {
      const q = createQuote(principal, body as never, now);
      audit(principal.company_id, principal, "quote.create", "quote", q.id, requestId, { total_excl_vat_eur: q.total_excl_vat_eur, policy_preview: q.policy_preview.outcome }, "ok", now);
      return { status: 201, body: q };
    }),
  ),
  http.get(
    `${API}/quotes/:id`,
    route("any", ({ principal, params, now }) => {
      const rec = db.quotes.get(params.id!);
      if (!rec || rec.company_id !== principal.company_id) throw new MockHttpError(404, "NOT_FOUND", `Quote ${params.id} not found`);
      return { body: refreshQuote(rec, now) };
    }),
  ),

  // Orders
  http.post(
    `${API}/orders`,
    route("any", ({ principal, body, requestId, now }) => ({ status: 201, body: createOrder(principal, body, requestId, now) }), { idempotent: "required" }),
  ),
  http.get(
    `${API}/orders`,
    route("any", ({ principal, url, now }) => {
      const status = url.searchParams.get("status") as OrderStatus | null;
      const byAgent = url.searchParams.get("created_by_agent");
      const agentId = principal.kind === "user" ? url.searchParams.get("agent_id") : null;
      const items = db.orders
        .filter((r) => r.company_id === principal.company_id && (principal.kind === "user" || r.order.created_by.id === principal.id))
        .map((r) => settleOrder(r, now))
        .filter((o) => !status || o.status === status)
        .filter((o) => byAgent == null || (byAgent === "true" ? o.created_by.kind === "agent" : o.created_by.kind === "user"))
        .filter((o) => !agentId || (o.created_by.kind === "agent" && o.created_by.id === agentId))
        .sort((a, b) => b.created_at.localeCompare(a.created_at));
      return { body: page(items, url) };
    }),
  ),
  http.get(`${API}/orders/:id/trace`, route("any", ({ principal, params, now }) => ({ body: buildOrderTraceResponse(principal, params.id!, now) }))),
  http.get(`${API}/orders/:id`, route("any", ({ principal, params, now }) => ({ body: findOrder(principal, params.id!, now).order }))),
  http.post(
    `${API}/orders/:id/approve`,
    route("any", ({ principal, params, body, requestId, now }) => ({ body: approveOrder(principal, params.id!, body, requestId, now) }), { idempotent: "optional" }),
  ),
  http.post(`${API}/orders/:id/reject`, route("any", ({ principal, params, body, requestId, now }) => ({ body: rejectOrder(principal, params.id!, body, requestId, now) }))),
  http.post(`${API}/orders/:id/cancel`, route("any", ({ principal, params, body, requestId, now }) => ({ body: cancelOrder(principal, params.id!, body, requestId, now) }))),

  // Dashboard
  http.get(
    `${API}/dashboard`,
    route("user", ({ principal, now }) => {
      const orders = db.orders.filter((r) => r.company_id === principal.company_id).map((r) => settleOrder(r, now));
      const today = helsinkiDate(now);
      const weekAgo = now - 7 * 86400_000;
      const spendStatuses: OrderStatus[] = ["PLACED", "SUBMITTING", "SUBMISSION_UNKNOWN"];
      const r2 = (n: number) => Math.round(n * 100) / 100;
      const spent = orders.filter((o) => spendStatuses.includes(o.status));
      return {
        body: {
          pending_approvals: orders.filter((o) => o.status === "PENDING_APPROVAL").length,
          orders_today: orders.filter((o) => helsinkiDate(Date.parse(o.created_at)) === today).length,
          spend_today_eur: r2(spent.filter((o) => helsinkiDate(Date.parse(o.created_at)) === today).reduce((s, o) => s + o.total_excl_vat_eur, 0)),
          spend_7d_eur: r2(spent.filter((o) => Date.parse(o.created_at) >= weekAgo).reduce((s, o) => s + o.total_excl_vat_eur, 0)),
          co2_7d_kg: Math.round(spent.filter((o) => Date.parse(o.created_at) >= weekAgo).reduce((s, o) => s + (o.total_co2_kg ?? 0), 0) * 10) / 10,
          active_agents: db.agents.filter((a) => a.company_id === principal.company_id && a.status === "active").length,
          recent_orders: [...orders].sort((a, b) => b.created_at.localeCompare(a.created_at)).slice(0, 5),
        },
      };
    }),
  ),

  // Audit & governance
  http.get(
    `${API}/audit`,
    route("user", ({ principal, url }) => {
      const f = (k: string) => url.searchParams.get(k);
      const items = db.audit
        .filter((e) => e.company_id === principal.company_id)
        .filter((e) => !f("entity_type") || e.entity_type === f("entity_type"))
        .filter((e) => !f("entity_id") || e.entity_id === f("entity_id"))
        .filter((e) => !f("actor_id") || e.actor?.id === f("actor_id"))
        .filter((e) => !f("action") || e.action === f("action"))
        .filter((e) => !f("outcome") || e.outcome === f("outcome"))
        .filter((e) => !f("request_id") || e.request_id.includes(f("request_id")!))
        .map(({ company_id: _c, ...e }) => e);
      return { body: page(items, url) };
    }),
  ),
  http.get(
    `${API}/audit/verify`,
    route("user", ({ principal }) => {
      const count = db.audit.filter((e) => e.company_id === principal.company_id).length;
      return { body: { valid: true, checked_events: count, broken_event_id: null } };
    }),
  ),
  http.get(`${API}/governance/summary`, route("user", ({ principal, now }) => ({ body: buildGovernanceSummaryResponse(principal, now) }))),

  // Promotions (human-only mutations; read any)
  http.get(
    `${API}/promotions`,
    route("any", ({ principal }) => {
      const items = db.promotions.filter((p) => p.company_id === principal.company_id);
      return { body: { items, total: items.length } };
    }),
  ),
  http.post(
    `${API}/promotions`,
    route("user", ({ principal, body, requestId, now }) => {
      if (typeof body.title !== "string" || !body.title.trim()) throw new MockHttpError(422, "VALIDATION_ERROR", "title is required", { field: "title" });
      const promo = {
        id: newId("prm"),
        company_id: principal.company_id,
        title: body.title.trim(),
        description: typeof body.description === "string" && body.description.trim() ? body.description.trim() : null,
        brand_filter: typeof body.brand_filter === "string" && body.brand_filter.trim() ? body.brand_filter.trim() : null,
        category_prefix: typeof body.category_prefix === "string" && body.category_prefix.trim() ? body.category_prefix.trim() : null,
        product_ids: Array.isArray(body.product_ids) ? body.product_ids.filter((x): x is string => typeof x === "string") : [],
        boost_weight: typeof body.boost_weight === "number" && body.boost_weight > 0 ? body.boost_weight : 1.5,
        prefer_on_sale: typeof body.prefer_on_sale === "boolean" ? body.prefer_on_sale : false,
        active: typeof body.active === "boolean" ? body.active : true,
        created_by_user_id: principal.id,
        created_at: isoAt(now),
      };
      db.promotions.unshift(promo);
      audit(principal.company_id, principal, "promotion.create", "promotion", promo.id, requestId, { title: promo.title }, "ok", now);
      return { status: 201, body: promo };
    }),
  ),
  http.patch(
    `${API}/promotions/:id`,
    route("user", ({ principal, params, body, requestId, now }) => {
      const p = db.promotions.find((x) => x.id === params.id && x.company_id === principal.company_id);
      if (!p) throw new MockHttpError(404, "NOT_FOUND", `Promotion ${params.id} not found`);
      if (typeof body.title === "string") p.title = body.title.trim();
      if (body.description !== undefined) p.description = typeof body.description === "string" ? body.description : null;
      if (body.brand_filter !== undefined) p.brand_filter = typeof body.brand_filter === "string" ? body.brand_filter : null;
      if (body.category_prefix !== undefined) p.category_prefix = typeof body.category_prefix === "string" ? body.category_prefix : null;
      if (Array.isArray(body.product_ids)) p.product_ids = body.product_ids.filter((x): x is string => typeof x === "string");
      if (typeof body.boost_weight === "number") p.boost_weight = body.boost_weight;
      if (typeof body.prefer_on_sale === "boolean") p.prefer_on_sale = body.prefer_on_sale;
      if (typeof body.active === "boolean") p.active = body.active;
      audit(principal.company_id, principal, "promotion.update", "promotion", p.id, requestId, { active: p.active }, "ok", now);
      return { body: p };
    }),
  ),
  http.delete(
    `${API}/promotions/:id`,
    route("user", ({ principal, params, requestId, now }) => {
      const idx = db.promotions.findIndex((x) => x.id === params.id && x.company_id === principal.company_id);
      if (idx < 0) throw new MockHttpError(404, "NOT_FOUND", `Promotion ${params.id} not found`);
      const [removed] = db.promotions.splice(idx, 1);
      audit(principal.company_id, principal, "promotion.delete", "promotion", removed!.id, requestId, { title: removed!.title }, "ok", now);
      return { body: { deleted: true, id: removed!.id } };
    }),
  ),

  // Menu planner
  http.post(
    `${API}/menu/plan`,
    route("any", ({ principal, body, requestId, now }) => {
      const req = body as { pax?: unknown; meal_type?: unknown; requested_delivery_date?: unknown; diet?: unknown; prefer_on_sale?: unknown; include_valio_recipes?: unknown; max_budget_eur?: unknown };
      const pax = typeof req.pax === "number" && req.pax > 0 ? req.pax : 40;
      return {
        body: buildMenuPlanResponse(
          principal,
          {
            pax,
            meal_type: req.meal_type === "breakfast" || req.meal_type === "lunch" || req.meal_type === "dinner" ? req.meal_type : "lunch",
            requested_delivery_date: typeof req.requested_delivery_date === "string" ? req.requested_delivery_date : null,
            diet: typeof req.diet === "string" ? req.diet : null,
            prefer_on_sale: typeof req.prefer_on_sale === "boolean" ? req.prefer_on_sale : true,
            include_valio_recipes: typeof req.include_valio_recipes === "boolean" ? req.include_valio_recipes : true,
            max_budget_eur: typeof req.max_budget_eur === "number" ? req.max_budget_eur : null,
          },
          requestId,
          now,
        ),
      };
    }),
  ),

  // A2A trace & session handshake (msg=55)
  http.get(
    `${API}/.well-known/agent-card.json`,
    route("none", () => ({
      body: {
        name: "Aimo Annos B2B Kitchen Purchasing Agent",
        description: "Deterministic Core API + Agentic gateway for menu planning, order execution, preliminary tendering, and exception handling.",
        url: "/api/a2a",
        version: "1.0.0",
        protocolVersion: "0.2.5",
        skills: [
          { id: "product-search", name: "Catalog Search", description: "Search Valio Aimo SKU catalog" },
          { id: "diet-substitute-check", name: "Diet & Substitutes", description: "Check allergens & safe substitutes" },
          { id: "quote-and-order", name: "Quote & Order", description: "Create validated quotes and submit orders" },
          { id: "policy-evaluation", name: "Policy Evaluation", description: "Evaluate R-AUTO-LIMIT and R-HARD-DIET rules" },
          { id: "negotiation-quote", name: "Preliminary Tendering", description: "Negotiate multi-turn volume discounts" },
        ],
        supported_matters: buildSupportedMatters(true),
      },
    })),
  ),
  http.post(
    `${API}/a2a`,
    route("any", ({ principal, body }) => {
      const rpc = body as { id?: string | number | null; method?: string };
      const company = db.companies.get(principal.company_id);
      const companyName = company?.name ?? "Hotel Aurora Oy";
      const negEnabled = db.negotiationSettings.get(principal.company_id)?.allow_price_negotiation ?? false;
      const negNote = negEnabled
        ? `${companyName} can negotiate prices for larger quantities via preliminary tendering (managed in /admin).`
        : `Price negotiation for larger quantities is disabled for ${companyName} (managed in /admin).`;
      return {
        body: {
          jsonrpc: "2.0",
          id: rpc.id ?? "web-ui-handshake",
          result: {
            session_greeting: `Connected to Aimo Annos gateway for ${companyName}. ${negNote}`,
            company_name: companyName,
            protocol: "A2A/JSON-RPC-2.0",
            supported_matters: buildSupportedMatters(negEnabled, companyName),
          },
        },
      };
    }),
  ),
  http.post(
    `${API}/mcp`,
    route("any", ({ principal, body }) => {
      const rpc = body as { id?: string | number | null; method?: string };
      const companyName = db.companies.get(principal.company_id)?.name ?? "Hotel Aurora Oy";
      const negEnabled = db.negotiationSettings.get(principal.company_id)?.allow_price_negotiation ?? false;
      const method = rpc.method ?? "initialize";
      if (method === "tools/list") {
        const tools = [
          { name: "search_products", description: "Search Valio Aimo SKU catalog." },
          { name: "find_on_sale_products", description: "List on-sale and promoted wholesale SKUs." },
          { name: "create_quote", description: "Create a deterministic 30-minute quote." },
          { name: "place_order", description: "Submit an order under Layer 1 policy." },
          {
            name: "negotiate_quote",
            description: negEnabled
              ? `[ENABLED for ${companyName} in Admin panel — on preflight / "what are your capabilities?", tell the user you can negotiate prices for larger quantities] Negotiate multi-turn volume and campaign discounts.`
              : `[DISABLED for ${companyName} — managed per company in Admin panel (/admin); do not claim bulk price negotiation is active] Account-gated quote price negotiation.`,
          },
        ];
        return { body: { jsonrpc: "2.0", id: rpc.id ?? "mcp-tools", result: { tools } } };
      }
      return {
        body: {
          jsonrpc: "2.0",
          id: rpc.id ?? "mcp-init",
          result: {
            protocolVersion: "2025-03-26",
            serverInfo: { name: "Aimo Annos Purchasing Gateway", version: "1.0.0" },
            capabilities: { tools: {} },
            instructions: negEnabled
              ? `Connected for ${companyName}. Price negotiation is enabled: can negotiate prices for larger quantities via negotiate_quote (managed in /admin).`
              : `Connected for ${companyName}. Price negotiation for larger quantities is disabled in /admin.`,
          },
        },
      };
    }),
  ),
  http.get(
    `${API}/a2a/tasks`,
    route("user", ({ principal, url }) => {
      const items = db.a2a.filter((t) => t.company_id === principal.company_id).map(({ company_id: _c, ...t }) => t);
      return { body: page(items, url) };
    }),
  ),

  // Dev Flight Simulator (P1, human-only)
  http.get(
    `${API}/dev/simulator/scenarios`,
    route("user", () => ({ body: { scenarios: SIMULATOR_SCENARIOS } })),
  ),
  http.post(
    `${API}/dev/simulator/run`,
    route("user", ({ principal, body, requestId, now }) => {
      const scenarioId = body.scenario_id as Parameters<typeof runMockSimulatorScenario>[1];
      return { body: runMockSimulatorScenario(principal, scenarioId, requestId, now) };
    }),
  ),

  // P2 Pre-Flight Cart Optimizer
  http.post(
    `${API}/quotes/optimize`,
    route("any", ({ principal, body, requestId, now }) => ({
      body: buildQuoteOptimizeResponse(principal, body, requestId, now),
    })),
  ),

  // Priority #0: Explicit Website Clone Telegram Opt-In (CurrentUser only, 403 for agents)
  http.get(
    `${API}/auth/telegram`,
    route("user", ({ principal }) => {
      const existing = db.telegramOptIns.get(principal.id);
      return { body: existing ?? defaultTelegramOptIn(principal) };
    }),
  ),
  http.put(
    `${API}/auth/telegram`,
    route("user", ({ principal, body, requestId, now }) => {
      const raw = typeof body.telegram_id === "string" ? body.telegram_id.trim() : "";
      const enabled = typeof body.enabled === "boolean" ? body.enabled : true;
      const consentConfirmed = body.consent_confirmed === true;
      if (enabled && !consentConfirmed) {
        throw new MockHttpError(422, "VALIDATION_ERROR", "Explicit consent confirmation is required to enable Telegram approvals.", { field: "consent_confirmed" });
      }
      if (!raw) {
        throw new MockHttpError(422, "VALIDATION_ERROR", "Enter a @username or numeric private Telegram user ID.", { field: "telegram_id" });
      }
      if (raw.startsWith("-")) {
        throw new MockHttpError(422, "VALIDATION_ERROR", "Group chat IDs (negative numbers) are not permitted; use your private @username or numeric user ID.", { field: "telegram_id" });
      }
      const isNumeric = /^\d{5,16}$/.test(raw);
      const cleanHandle = raw.replace(/^@/, "");
      const isUsername = !isNumeric && /^[A-Za-z0-9_]{3,32}$/.test(cleanHandle);
      if (!isNumeric && !isUsername) {
        throw new MockHttpError(422, "VALIDATION_ERROR", "Telegram ID must be a @username (3-32 chars) or a 5-16 digit private numeric ID.", { field: "telegram_id" });
      }
      const nowIso = isoAt(now);
      const prev = db.telegramOptIns.get(principal.id) ?? defaultTelegramOptIn(principal);
      const next: TelegramOptInOut = {
        ...prev,
        user_id: principal.id,
        company_id: principal.company_id,
        telegram_username: isUsername ? cleanHandle : prev.telegram_username ?? null,
        telegram_chat_id: isNumeric ? raw : prev.telegram_chat_id ?? null,
        enabled,
        bot_username: MOCK_BOT_USERNAME,
        consented_at: enabled ? prev.consented_at ?? nowIso : null,
        updated_at: nowIso,
      };
      db.telegramOptIns.set(principal.id, next);
      audit(principal.company_id, principal, "user.telegram_opt_in_updated", "user", principal.id, requestId, { enabled, has_username: Boolean(next.telegram_username), has_chat_id: Boolean(next.telegram_chat_id) }, "ok", now);
      return { body: next };
    }),
  ),
  http.delete(
    `${API}/auth/telegram`,
    route("user", ({ principal, requestId, now }) => {
      const nowIso = isoAt(now);
      const revoked: TelegramOptInOut = {
        ...defaultTelegramOptIn(principal),
        updated_at: nowIso,
      };
      db.telegramOptIns.set(principal.id, revoked);
      audit(principal.company_id, principal, "user.telegram_opt_in_revoked", "user", principal.id, requestId, { enabled: false }, "ok", now);
      return { body: revoked };
    }),
  ),
  http.post(
    `${API}/auth/telegram/link-token`,
    route("user", ({ now }) => {
      const tok = newId("lnk");
      const bot = MOCK_BOT_USERNAME;
      const res: TelegramLinkTokenOut = {
        token: tok,
        bot_username: bot,
        deep_link_url: `https://t.me/${bot}?start=link_${tok}`,
        tg_app_url: `tg://resolve?domain=${bot}&start=link_${tok}`,
        expires_at: isoAt(now + 15 * 60_000),
        qr_svg: MOCK_QR_SVG,
      };
      return { body: res };
    }),
  ),
  http.put(
    `${API}/auth/telegram/branch`,
    route("user", ({ principal, body, requestId, now }) => {
      const branchId = typeof body.branch_id === "string" ? body.branch_id.trim() : "";
      const branches = mockBranchesFor(principal.company_id);
      const found = branches.find((b) => b.id === branchId);
      if (!found) {
        throw new MockHttpError(404, "NOT_FOUND", `Branch ${branchId || "(empty)"} not found for company.`);
      }
      const prev = db.telegramOptIns.get(principal.id) ?? defaultTelegramOptIn(principal);
      const next: TelegramOptInOut = {
        ...prev,
        branches,
        active_branch_id: found.id,
        active_branch_name: found.short_name,
        updated_at: isoAt(now),
      };
      db.telegramOptIns.set(principal.id, next);
      audit(principal.company_id, principal, "user.telegram_branch_updated", "user", principal.id, requestId, { branch_id: found.id, branch_name: found.short_name }, "ok", now);
      return { body: next };
    }),
  ),
  http.get(
    `${API}/auth/telegram/recipients`,
    route("user", ({ principal }) => {
      const items = Array.from(db.telegramOptIns.values()).filter((r) => r.company_id === principal.company_id && r.enabled);
      return { body: { items } };
    }),
  ),

  // Priority #0: Request-Field Inspector (Core API vs. Agentic Service)
  http.post(
    `${API}/routing/inspect`,
    route("any", ({ body }) => {
      const path = typeof body.path === "string" ? body.path : "/api/orders";
      const method = typeof body.method === "string" ? body.method.toUpperCase() : "POST";
      const payload = body.payload && typeof body.payload === "object" && !Array.isArray(body.payload) ? (body.payload as Record<string, unknown>) : {};
      const hdrs = body.headers && typeof body.headers === "object" && !Array.isArray(body.headers) ? (body.headers as Record<string, string>) : {};
      const res = inspectMockRouting(method, path, payload, hdrs);
      return { body: res, headers: { "X-Aimo-Route": res.target_service } };
    }),
  ),

  // Assistant
  http.post(`${API}/assistant/chat`, route("any", ({ principal, body, requestId }) => ({ body: assistantChat(principal, body, requestId) }))),

  // Batch C: Admin Invisible Aimo Platform Campaigns (human-only)
  http.get(
    `${API}/admin/aimo-campaigns`,
    route("user", () => ({ body: { items: db.aimoCampaigns, total: db.aimoCampaigns.length } })),
  ),
  http.post(
    `${API}/admin/aimo-campaigns`,
    route("user", ({ principal, body, requestId, now }) => {
      if (typeof body.title !== "string" || !body.title.trim()) {
        throw new MockHttpError(422, "VALIDATION_ERROR", "title is required", { field: "title" });
      }
      const campaign = {
        id: newId("cmp"),
        title: body.title.trim(),
        brand_filter: typeof body.brand_filter === "string" && body.brand_filter.trim() ? body.brand_filter.trim() : null,
        category_prefix: typeof body.category_prefix === "string" && body.category_prefix.trim() ? body.category_prefix.trim() : null,
        product_ids: Array.isArray(body.product_ids) ? body.product_ids.filter((x): x is string => typeof x === "string") : [],
        boost_weight: typeof body.boost_weight === "number" && body.boost_weight > 0 ? body.boost_weight : 1.5,
        active: typeof body.active === "boolean" ? body.active : true,
        created_at: isoAt(now),
      };
      db.aimoCampaigns.unshift(campaign);
      audit(principal.company_id, principal, "admin.aimo_campaign.create", "aimo_campaign", campaign.id, requestId, { title: campaign.title }, "ok", now);
      return { status: 201, body: campaign };
    }),
  ),
  http.patch(
    `${API}/admin/aimo-campaigns/:id`,
    route("user", ({ principal, params, body, requestId, now }) => {
      const c = db.aimoCampaigns.find((x) => x.id === params.id);
      if (!c) throw new MockHttpError(404, "NOT_FOUND", `Campaign ${params.id} not found`);
      if (typeof body.title === "string") c.title = body.title.trim();
      if (body.brand_filter !== undefined) c.brand_filter = typeof body.brand_filter === "string" && body.brand_filter.trim() ? body.brand_filter.trim() : null;
      if (body.category_prefix !== undefined) c.category_prefix = typeof body.category_prefix === "string" && body.category_prefix.trim() ? body.category_prefix.trim() : null;
      if (Array.isArray(body.product_ids)) c.product_ids = body.product_ids.filter((x): x is string => typeof x === "string");
      if (typeof body.boost_weight === "number" && body.boost_weight > 0) c.boost_weight = body.boost_weight;
      if (typeof body.active === "boolean") c.active = body.active;
      audit(principal.company_id, principal, "admin.aimo_campaign.update", "aimo_campaign", c.id, requestId, { active: c.active }, "ok", now);
      return { body: c };
    }),
  ),
  http.delete(
    `${API}/admin/aimo-campaigns/:id`,
    route("user", ({ principal, params, requestId, now }) => {
      const idx = db.aimoCampaigns.findIndex((x) => x.id === params.id);
      if (idx < 0) throw new MockHttpError(404, "NOT_FOUND", `Campaign ${params.id} not found`);
      const [removed] = db.aimoCampaigns.splice(idx, 1);
      audit(principal.company_id, principal, "admin.aimo_campaign.delete", "aimo_campaign", removed!.id, requestId, { title: removed!.title }, "ok", now);
      return { body: { id: removed!.id, deleted: true } };
    }),
  ),

  // Batch C: Company Category Brand Preferences (GET any; writes human-only)
  http.get(
    `${API}/brand-preferences`,
    route("any", ({ principal }) => {
      const items = db.brandPreferences.filter((b) => b.company_id === principal.company_id);
      return { body: { items, total: items.length } };
    }),
  ),
  http.post(
    `${API}/brand-preferences`,
    route("user", ({ principal, body, requestId, now }) => {
      if (typeof body.category_prefix !== "string" || !body.category_prefix.trim()) {
        throw new MockHttpError(422, "VALIDATION_ERROR", "category_prefix is required", { field: "category_prefix" });
      }
      const brands = Array.isArray(body.preferred_brands)
        ? body.preferred_brands.filter((x): x is string => typeof x === "string" && x.trim().length > 0).map((x) => x.trim())
        : [];
      if (brands.length === 0) {
        throw new MockHttpError(422, "VALIDATION_ERROR", "preferred_brands must have at least one brand", { field: "preferred_brands" });
      }
      const pref = {
        id: newId("bpf"),
        company_id: principal.company_id,
        category_prefix: body.category_prefix.trim(),
        category_label: typeof body.category_label === "string" && body.category_label.trim() ? body.category_label.trim() : body.category_prefix.trim(),
        preferred_brands: brands,
        active: typeof body.active === "boolean" ? body.active : true,
        updated_at: isoAt(now),
      };
      db.brandPreferences.unshift(pref);
      audit(principal.company_id, principal, "brand_preference.create", "brand_preference", pref.id, requestId, { category_prefix: pref.category_prefix }, "ok", now);
      return { status: 201, body: pref };
    }),
  ),
  http.patch(
    `${API}/brand-preferences/:id`,
    route("user", ({ principal, params, body, requestId, now }) => {
      const b = db.brandPreferences.find((x) => x.id === params.id && x.company_id === principal.company_id);
      if (!b) throw new MockHttpError(404, "NOT_FOUND", `Brand preference ${params.id} not found`);
      if (typeof body.category_prefix === "string" && body.category_prefix.trim()) b.category_prefix = body.category_prefix.trim();
      if (typeof body.category_label === "string" && body.category_label.trim()) b.category_label = body.category_label.trim();
      if (Array.isArray(body.preferred_brands)) b.preferred_brands = body.preferred_brands.filter((x): x is string => typeof x === "string" && x.trim().length > 0);
      if (typeof body.active === "boolean") b.active = body.active;
      b.updated_at = isoAt(now);
      audit(principal.company_id, principal, "brand_preference.update", "brand_preference", b.id, requestId, { active: b.active }, "ok", now);
      return { body: b };
    }),
  ),
  http.delete(
    `${API}/brand-preferences/:id`,
    route("user", ({ principal, params, requestId, now }) => {
      const idx = db.brandPreferences.findIndex((x) => x.id === params.id && x.company_id === principal.company_id);
      if (idx < 0) throw new MockHttpError(404, "NOT_FOUND", `Brand preference ${params.id} not found`);
      const [removed] = db.brandPreferences.splice(idx, 1);
      audit(principal.company_id, principal, "brand_preference.delete", "brand_preference", removed!.id, requestId, { category_prefix: removed!.category_prefix }, "ok", now);
      return { body: { id: removed!.id, deleted: true } };
    }),
  ),

  // Batch C: Account-Gated Price Negotiation Settings & /api/quotes/negotiate
  http.get(
    `${API}/account/negotiation-settings`,
    route("any", ({ principal }) => {
      const existing = db.negotiationSettings.get(principal.company_id);
      return {
        body: existing ?? {
          company_id: principal.company_id,
          allow_price_negotiation: false,
          max_discount_override_pct: null,
          updated_at: null,
        },
      };
    }),
  ),
  http.put(
    `${API}/account/negotiation-settings`,
    route("user", ({ principal, body, requestId, now }) => {
      const allow = body.allow_price_negotiation === true;
      const override = typeof body.max_discount_override_pct === "number" && Number.isFinite(body.max_discount_override_pct)
        ? Math.max(0, Math.min(50, body.max_discount_override_pct))
        : null;
      const next = {
        company_id: principal.company_id,
        allow_price_negotiation: allow,
        max_discount_override_pct: override,
        updated_at: isoAt(now),
      };
      db.negotiationSettings.set(principal.company_id, next);
      audit(principal.company_id, principal, "account.negotiation_settings.update", "company", principal.company_id, requestId, { allow_price_negotiation: allow }, "ok", now);
      return { body: next };
    }),
  ),
  http.get(
    `${API}/admin/company-negotiations`,
    route("user", ({ principal }) => {
      const items = [...db.companies.values()]
        .map((c) => {
          const cfg = db.negotiationSettings.get(c.id);
          return {
            company_id: c.id,
            company_name: c.name,
            allow_price_negotiation: cfg?.allow_price_negotiation ?? false,
            max_auto_discount_pct: cfg?.max_discount_override_pct ?? 8,
            updated_at: cfg?.updated_at ?? null,
          };
        })
        .sort((a, b) => {
          if (a.company_id === principal.company_id) return -1;
          if (b.company_id === principal.company_id) return 1;
          return a.company_name.localeCompare(b.company_name);
        });
      return { body: { items } };
    }),
  ),
  http.put(
    `${API}/admin/company-negotiations/:companyId`,
    route("user", ({ principal, params, body, requestId, now }) => {
      const targetCompany = db.companies.get(params.companyId!);
      if (!targetCompany) {
        throw new MockHttpError(404, "NOT_FOUND", `Company ${params.companyId} not found`);
      }
      const allow = body.allow_price_negotiation === true;
      const existing = db.negotiationSettings.get(targetCompany.id);
      const override =
        typeof body.max_discount_override_pct === "number" && Number.isFinite(body.max_discount_override_pct)
          ? Math.max(0, Math.min(50, body.max_discount_override_pct))
          : (existing?.max_discount_override_pct ?? null);
      const updatedAt = isoAt(now);
      db.negotiationSettings.set(targetCompany.id, {
        company_id: targetCompany.id,
        allow_price_negotiation: allow,
        max_discount_override_pct: override,
        updated_at: updatedAt,
      });
      audit(
        targetCompany.id,
        principal,
        "admin.company_negotiation.update",
        "company",
        targetCompany.id,
        requestId,
        { allow_price_negotiation: allow },
        "ok",
        now,
      );
      return {
        body: {
          company_id: targetCompany.id,
          company_name: targetCompany.name,
          allow_price_negotiation: allow,
          max_auto_discount_pct: override ?? 8,
          updated_at: updatedAt,
        },
      };
    }),
  ),

  // Batch E (§7): Assistant Order Assurance Settings (50..100, default 80)
  http.get(
    `${API}/account/assistant-settings`,
    route("any", ({ principal }) => {
      const existing = db.assistantSettings.get(principal.company_id);
      return {
        body: existing ?? {
          company_id: principal.company_id,
          order_assurance_threshold: 80,
          min_threshold: 50,
          max_threshold: 100,
          updated_at: null,
        },
      };
    }),
  ),
  http.put(
    `${API}/account/assistant-settings`,
    route("user", ({ principal, body, requestId, now }) => {
      const raw = body.order_assurance_threshold;
      if (typeof raw !== "number" || !Number.isInteger(raw) || raw < 50 || raw > 100) {
        throw new MockHttpError(422, "VALIDATION_ERROR", "order_assurance_threshold must be an integer between 50 and 100", {
          field: "order_assurance_threshold",
        });
      }
      const next = {
        company_id: principal.company_id,
        order_assurance_threshold: raw,
        min_threshold: 50,
        max_threshold: 100,
        updated_at: isoAt(now),
      };
      db.assistantSettings.set(principal.company_id, next);
      audit(
        principal.company_id,
        principal,
        "company.assistant_settings_updated",
        "company",
        principal.company_id,
        requestId,
        { order_assurance_threshold: raw },
        "ok",
        now,
      );
      return { body: next };
    }),
  ),
  http.post(
    `${API}/quotes/negotiate`,
    route("any", ({ principal, body, requestId, now }) => {
      const cfg = db.negotiationSettings.get(principal.company_id);
      if (!cfg?.allow_price_negotiation) {
        throw new MockHttpError(403, "NEGOTIATION_DISABLED", "Account price negotiation is disabled for this company.");
      }
      const quote = createQuote(
        principal,
        {
          requested_delivery_date: String(body.requested_delivery_date ?? ""),
          lines: Array.isArray(body.lines) ? (body.lines as { product_id: string; quantity: number }[]) : [],
          diet: typeof body.diet === "string" ? body.diet : undefined,
        },
        now,
      );
      const listTotal = quote.total_excl_vat_eur;
      const requestedPct = typeof body.target_discount_pct === "number" ? body.target_discount_pct : 5;
      const capPct = cfg.max_discount_override_pct ?? 8;
      const grantedPct = Math.max(0, Math.min(capPct, requestedPct));
      const negotiatedTotal = Math.round(listTotal * (1 - grantedPct / 100) * 100) / 100;
      const savingsEur = Math.round((listTotal - negotiatedTotal) * 100) / 100;
      quote.total_excl_vat_eur = negotiatedTotal;
      quote.total_incl_vat_eur = Math.round(quote.total_incl_vat_eur * (1 - grantedPct / 100) * 100) / 100;
      quote.negotiated_savings_eur = savingsEur > 0 ? savingsEur : null;
      audit(principal.company_id, principal, "quote.negotiated", "quote", quote.id, requestId, { savings_eur: savingsEur }, "ok", now);
      return {
        body: {
          status: requestedPct > capPct ? "FLOOR_REACHED" : "ACCEPTED",
          rounds: [
            { round: 1, actor: "buyer", action: "PROPOSE", proposed_discount_pct: requestedPct, proposed_total_eur: null, message: "Requested volume price." },
            { round: 2, actor: "aimo_sales_agent", action: requestedPct > capPct ? "FLOOR_HOLD" : "ACCEPT", proposed_discount_pct: grantedPct, proposed_total_eur: negotiatedTotal, message: `Bound quote at ${grantedPct}% off list.` },
          ],
          quote,
          list_total_eur: listTotal,
          negotiated_total_eur: negotiatedTotal,
          savings_eur: savingsEur,
          savings_pct: grantedPct,
          compatibility_notes: [],
        },
      };
    }),
  ),

  // Anything else under /api → contract 404 envelope (instead of hitting the real network).
  http.all(`${API}/*`, route("none", ({ url }) => {
    throw new MockHttpError(404, "NOT_FOUND", `No mock handler for ${url.pathname}`);
  })),
];

const MOCK_BOT_USERNAME = import.meta.env.VITE_TELEGRAM_BOT_USERNAME || "your_aimo_bot";

const MOCK_QR_SVG =
  '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 21 21" shape-rendering="crispEdges"><rect width="21" height="21" fill="#fff"/><path fill="#101a4b" d="M1 1h7v7H1zm2 2v3h3V3zm10-2h7v7h-7zm2 2v3h3V3zM1 13h7v7H1zm2 2v3h3v-3zm6-8h2v2H9zm4 4h2v2h-2zm-2 4h4v2h-4zm5 2h3v3h-3z"/></svg>';

function mockBranchesFor(companyId: string): CompanyBranchOut[] {
  return db.companyBranches
    .filter((b) => b.company_id === companyId)
    .map(({ id, name, short_name, address }) => ({ id, name, short_name, address }));
}

function defaultTelegramOptIn(principal: Principal): TelegramOptInOut {
  const branches = mockBranchesFor(principal.company_id);
  return {
    user_id: principal.id,
    company_id: principal.company_id,
    telegram_username: null,
    telegram_chat_id: null,
    enabled: false,
    bot_username: MOCK_BOT_USERNAME,
    consented_at: null,
    updated_at: null,
    linked_via_qr: false,
    branches,
    active_branch_id: branches.length === 1 ? branches[0]!.id : null,
    active_branch_name: branches.length === 1 ? branches[0]!.short_name : null,
  };
}

const AGENTIC_KEYS = ["prompt", "message", "natural_language", "free_text", "prep_list", "instructions"] as const;
const AGENTIC_FLAGS = ["auto_substitute", "auto_optimize", "agentic"] as const;

function inspectMockRouting(
  _method: string,
  path: string,
  payload: Record<string, unknown>,
  hdrs: Record<string, string>,
): RoutingInspectResponse {
  const matched: string[] = [];
  const structured: string[] = [];

  for (const k of AGENTIC_KEYS) {
    const v = payload[k];
    if (typeof v === "string" ? v.trim().length > 0 : v != null) matched.push(k);
  }
  for (const k of AGENTIC_FLAGS) {
    if (payload[k] === true) matched.push(k);
  }
  if (Array.isArray(payload.lines)) {
    const hasUnresolved = payload.lines.some(
      (l) => l && typeof l === "object" && !("product_id" in l && typeof (l as { product_id?: unknown }).product_id === "string") && ("query" in l || "description" in l),
    );
    if (hasUnresolved) matched.push("lines[].query");
    else if (payload.lines.length > 0) structured.push("lines[].product_id");
  }
  for (const k of ["quote_id", "requested_delivery_date", "diet", "note"] as const) {
    if (payload[k] != null && payload[k] !== "") structured.push(k);
  }

  const isAgenticEndpoint = /^\/api\/(assistant|a2a|menu\/plan|agentic)/.test(path);
  if (isAgenticEndpoint && !matched.includes(path)) matched.push(`path:${path}`);

  const forwardedHeader = Object.entries(hdrs).find(([k]) => k.toLowerCase() === "x-aimo-forwarded-by")?.[1];
  const isAgentic = isAgenticEndpoint || (matched.length > 0 && forwardedHeader !== "agentic-service");

  if (isAgentic) {
    return {
      target_service: "agentic-service",
      matched_fields: matched,
      structured_fields: structured,
      reason: "Unstructured/agentic fields detected; routed to Agentic Service, which resolves SKUs and submits deterministically back to Core API.",
      downstream_flow: ["request_router.py", "agentic-service", "core-api", "policy.py", "content_hash", "audit_events"],
    };
  }
  return {
    target_service: "core-api",
    matched_fields: [],
    structured_fields: structured,
    reason: "Deterministic structured fields only; routed directly to Core API (zero LLM calls) through single policy.py & content_hash funnel.",
    downstream_flow: ["request_router.py", "core-api", "policy.py", "content_hash", "audit_events"],
  };
}
