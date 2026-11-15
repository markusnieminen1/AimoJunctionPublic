// MSW-backed end-to-end test of the demo story through the real API client:
// login → agent token → quote 1,000 × 100001 → order (PENDING_APPROVAL) → approve → PLACED.
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it } from "vitest";
import { ApiError, policyFromError } from "../api/client";
import { endpoints } from "../api/endpoints";
import { clearSession, setSession } from "../auth/session";
import { advanceMockClock, resetMockClock } from "./clock";
import { SEED_AGENT_SECRETS } from "./data";
import { db, resetMockDb, settleOrder } from "./db";
import { setupMockServer } from "./server";
import { sha256Hex } from "./sha256";

const server = setupMockServer();
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());
beforeEach(() => {
  resetMockDb();
  resetMockClock();
  clearSession();
});

async function loginUser() {
  const r = await endpoints.login({ email: "anna.korhonen@hotel.fi", password: "demo1234" });
  setSession({ token: r.token, principal: r.user, company: null });
  return r;
}

async function agentToken(id: keyof typeof SEED_AGENT_SECRETS = "agt_kitchen01") {
  return endpoints.agentToken({ agent_id: id, agent_secret: SEED_AGENT_SECRETS[id] });
}

describe("mock API (MSW node)", () => {
  it("sha256 helper matches the reference vector", () => {
    expect(sha256Hex("abc")).toBe("ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad");
  });

  it("login works and wrong password gives the error envelope", async () => {
    const r = await loginUser();
    expect(r.user).toMatchObject({ kind: "user", name: "Anna Korhonen", company_id: "cmp_aurora" });
    const me = await endpoints.me();
    expect(me.company.name).toBe("Hotel Aurora Oy");
    const aliasLogin = await endpoints.login({ email: "demo@aimoannos.test", password: "demo1234" });
    expect(aliasLogin.user.id).toBe(r.user.id);
    const err = (await endpoints.login({ email: "anna.korhonen@hotel.fi", password: "nope" }).catch((e) => e)) as ApiError;
    expect(err).toBeInstanceOf(ApiError);
    expect(err.code).toBe("UNAUTHENTICATED");
    expect(err.request_id).toMatch(/^req_/);
  });

  it("seeds one pending approval (1,000 × 100001 = €1,010.00, R-AUTO-LIMIT)", async () => {
    await loginUser();
    const inbox = await endpoints.listOrders({ status: "PENDING_APPROVAL" });
    expect(inbox.total).toBe(1);
    expect(inbox.items[0]!.total_excl_vat_eur).toBe(1010);
    expect(inbox.items[0]!.policy.rules.map((r) => r.rule_id)).toContain("R-AUTO-LIMIT");
    expect(inbox.items[0]!.created_by).toMatchObject({ kind: "agent", delegated_by: { name: "Anna Korhonen" } });
  });

  it("agent: quote 1,000 × 100001 → order → PENDING_APPROVAL → user approves → PLACED", async () => {
    const { token } = await agentToken();
    const quote = await endpoints.createQuote({ requested_delivery_date: "2026-11-16", lines: [{ product_id: "100001", quantity: 1000 }] }, { token });
    expect(quote.total_excl_vat_eur).toBe(1010);
    expect(quote.lines[0]).toMatchObject({ unit_price_eur: 1.01, delivery_status: "ON_TIME", closing_time: "2026-11-15T17:00:00+02:00" });
    expect(quote.lines[0]!.co2_basis).toBeTruthy();
    expect(quote.policy_preview.outcome).toBe("APPROVAL_REQUIRED");
    expect(quote.created_by.kind).toBe("agent");

    const order = await endpoints.createOrder({ quote_id: quote.id }, "idem-agent-1", { token });
    expect(order.status).toBe("PENDING_APPROVAL");
    expect(order.content_hash).toMatch(/^[0-9a-f]{64}$/);

    // Same key + same body → same order (no duplicate).
    const replay = await endpoints.createOrder({ quote_id: quote.id }, "idem-agent-1", { token });
    expect(replay.id).toBe(order.id);
    // Same key + different body → CONFLICT.
    const conflict = (await endpoints.createOrder({ quote_id: quote.id, note: "x" }, "idem-agent-1", { token }).catch((e) => e)) as ApiError;
    expect(conflict.code).toBe("CONFLICT");
    // New key on a used quote → QUOTE_ALREADY_USED.
    const used = (await endpoints.createOrder({ quote_id: quote.id }, "idem-agent-2", { token }).catch((e) => e)) as ApiError;
    expect(used.code).toBe("QUOTE_ALREADY_USED");

    // Agents cannot approve.
    const forbidden = (await endpoints.approveOrder(order.id, { content_hash: order.content_hash }, "k0", { token }).catch((e) => e)) as ApiError;
    expect(forbidden.status).toBe(403);

    await loginUser();
    const wrong = (await endpoints.approveOrder(order.id, { content_hash: "deadbeef" }, "k1").catch((e) => e)) as ApiError;
    expect(wrong.code).toBe("APPROVAL_CONTENT_CHANGED");

    const approved = await endpoints.approveOrder(order.id, { content_hash: order.content_hash, note: "ok" }, "k2");
    expect(approved.status).toBe("SUBMITTING");
    expect(approved.decided_by?.name).toBe("Anna Korhonen");

    advanceMockClock(3000);
    const placed = await endpoints.getOrder(order.id);
    expect(placed.status).toBe("PLACED");
    expect(placed.gateway_reference).toBeTruthy();
    expect(placed.events.map((e) => e.type)).toEqual(
      expect.arrayContaining(["order.created", "policy.evaluated", "approval.requested", "approval.approved", "gateway.submitting", "order.placed"]),
    );

    const again = (await endpoints.approveOrder(order.id, { content_hash: order.content_hash }, "k3").catch((e) => e)) as ApiError;
    expect(again.code).toBe("INVALID_STATE");

    const auditPage = await endpoints.audit({ entity_id: order.id });
    expect(auditPage.items.map((e) => e.action)).toEqual(expect.arrayContaining(["order.create", "order.approve", "order.placed"]));
  });

  it("over the hard limit → 403 POLICY_BLOCKED with rules; partial stock and cut-off flags", async () => {
    const { token } = await agentToken();
    const big = await endpoints.createQuote({ requested_delivery_date: "2026-11-16", lines: [{ product_id: "100001", quantity: 6000 }] }, { token });
    expect(big.policy_preview.outcome).toBe("BLOCKED");
    const err = (await endpoints.createOrder({ quote_id: big.id }, "idem-big", { token }).catch((e) => e)) as ApiError;
    expect(err.status).toBe(403);
    expect(err.code).toBe("POLICY_BLOCKED");
    expect(policyFromError(err)?.rules.map((r) => r.rule_id)).toContain("R-MAX-ORDER");

    const risky = await endpoints.createQuote(
      { requested_delivery_date: "2026-11-16", lines: [{ product_id: "100003", quantity: 1000 }, { product_id: "100008", quantity: 10 }, { product_id: "100009", quantity: 5 }] },
      { token },
    );
    const byId = Object.fromEntries(risky.lines.map((l) => [l.product_id, l]));
    expect(byId["100003"]!.flags).toContain("PARTIAL");
    expect(byId["100003"]!.available_amount).toBe(384);
    expect(byId["100008"]!.flags).toEqual(expect.arrayContaining(["LATE", "CUTOFF_PASSED", "SUPPLIER_QTY_UNKNOWN"]));
    expect(byId["100009"]!.delivery_status).toBe("NOT_DELIVERABLE");
    expect(risky.overall_delivery_status).toBe("NOT_DELIVERABLE");
  });

  it("user orders directly (AUTO) and SUBMITTING settles to PLACED", async () => {
    await loginUser();
    const q = await endpoints.createQuote({ requested_delivery_date: "2026-11-16", lines: [{ product_id: "100002", quantity: 10 }] });
    expect(q.policy_preview.outcome).toBe("AUTO");
    const o = await endpoints.createOrder({ quote_id: q.id }, "idem-user-1");
    expect(o.status).toBe("SUBMITTING");
    advanceMockClock(3000);
    expect((await endpoints.getOrder(o.id)).status).toBe("PLACED");
  });

  it("diet requirement: 100001 with diet M → R-HARD-DIET (quote preview BLOCKED, order 403); diet L passes", async () => {
    await loginUser();
    const q = await endpoints.createQuote({ requested_delivery_date: "2026-11-16", lines: [{ product_id: "100001", quantity: 10 }], diet: "M" });
    expect(q.diet).toBe("M");
    expect(q.policy_preview.outcome).toBe("BLOCKED");
    expect(q.policy_preview.rules.map((r) => r.rule_id)).toEqual(["R-HARD-DIET"]);
    const err = (await endpoints.createOrder({ quote_id: q.id }, "diet-1").catch((e) => e)) as ApiError;
    expect(err.status).toBe(403);
    expect(err.code).toBe("POLICY_BLOCKED");
    expect(policyFromError(err)?.rules[0]?.rule_id).toBe("R-HARD-DIET");

    const ok = await endpoints.createQuote({ requested_delivery_date: "2026-11-16", lines: [{ product_id: "100001", quantity: 10 }], diet: "L" });
    expect(ok.policy_preview.outcome).toBe("AUTO");
    // a diet on the order request is combined with the quote's
    const err2 = (await endpoints.createOrder({ quote_id: ok.id, diet: "M" }, "diet-2").catch((e) => e)) as ApiError;
    expect(err2.code).toBe("POLICY_BLOCKED");
    const o = await endpoints.createOrder({ quote_id: ok.id }, "diet-3");
    expect(o.diet).toBe("L");

    const bad = (await endpoints.createQuote({ requested_delivery_date: "2026-11-16", lines: [{ product_id: "100001", quantity: 1 }], diet: "XX" }).catch((e) => e)) as ApiError;
    expect(bad.code).toBe("VALIDATION_ERROR");
  });

  it("QUOTE_EXPIRED after 30 min and QUOTE_STALE after a price change", async () => {
    await loginUser();
    const q1 = await endpoints.createQuote({ requested_delivery_date: "2026-11-16", lines: [{ product_id: "100001", quantity: 10 }] });
    advanceMockClock(31 * 60_000);
    expect(((await endpoints.createOrder({ quote_id: q1.id }, "e1").catch((e) => e)) as ApiError).code).toBe("QUOTE_EXPIRED");

    const q2 = await endpoints.createQuote({ requested_delivery_date: "2026-11-16", lines: [{ product_id: "100001", quantity: 10 }] });
    db.priceOverrides["100001"] = 1.05;
    const stale = (await endpoints.createOrder({ quote_id: q2.id }, "e2").catch((e) => e)) as ApiError;
    expect(stale.code).toBe("QUOTE_STALE");
    expect(Array.isArray(stale.details.lines)).toBe(true);
  });

  it("disabled agent gets 403 AGENT_DISABLED immediately (kill switch)", async () => {
    const { token } = await agentToken();
    await loginUser();
    await endpoints.updateAgent("agt_kitchen01", { status: "disabled" });
    const err = (await endpoints.searchProducts({ q: "milk" }, { token }).catch((e) => e)) as ApiError;
    expect(err.code).toBe("AGENT_DISABLED");
  });

  it("products search supports diet + allergen_free; substitutes offer feasible lactose-free milk", async () => {
    await loginUser();
    const l = await endpoints.searchProducts({ diet: "L", category: "10400" });
    expect(l.items.map((p) => p.id)).toEqual(expect.arrayContaining(["100001", "100002", "100003"]));
    const g = await endpoints.searchProducts({ allergen_free: "gluten" });
    expect(g.items.every((p) => p.labels.includes("FREE_FROM_GLUTEN") || p.non_allergens.includes("AW"))).toBe(true);
    const subs = await endpoints.substitutes({ product_id: "100003", quantity: 1000, requested_delivery_date: "2026-11-16" });
    const ids = subs.items.map((s) => s.product.id);
    expect(ids).toEqual(expect.arrayContaining(["100001", "100002"]));
    expect(subs.items.every((s) => s.line.delivery_status === "ON_TIME" && s.trade_offs.length > 0)).toBe(true);
  });

  it("agents CRUD shows the secret once and the new agent can get a token", async () => {
    await loginUser();
    const created = await endpoints.createAgent({ name: "Test Bot", auto_approve_limit_eur: 100, max_order_value_eur: 1000, daily_spend_limit_eur: 500 });
    expect(created.agent_secret).toMatch(/^aks_/);
    expect(created.agent.delegated_by.name).toBe("Anna Korhonen");
    expect(JSON.stringify(await endpoints.getAgent(created.agent.id))).not.toContain(created.agent_secret);
    const tok = await endpoints.agentToken({ agent_id: created.agent.id, agent_secret: created.agent_secret });
    expect(tok.agent.id).toBe(created.agent.id);
  });

  it("assistant: milk → clarification → chilled quote → place order", async () => {
    const { token } = await agentToken();
    const r1 = await endpoints.assistantChat({ message: "1000 litres of lactose-free milk by Monday" }, { token });
    expect(r1.actions.map((a) => a.type)).toEqual(["search_results", "clarification"]);
    const clar = r1.actions[1] as { type: "clarification"; options?: string[] };
    const r2 = await endpoints.assistantChat({ conversation_id: r1.conversation_id, message: clar.options![0]! }, { token });
    const q = r2.actions.find((a) => a.type === "quote");
    expect(q && q.type === "quote" && q.quote.total_excl_vat_eur).toBe(1010);
    const r3 = await endpoints.assistantChat({ conversation_id: r1.conversation_id, message: "Place the order" }, { token });
    const o = r3.actions.find((a) => a.type === "order");
    expect(o && o.type === "order" && o.order.status).toBe("PENDING_APPROVAL");
  });

  it("dashboard, A2A trace and approval expiry", async () => {
    await loginUser();
    const d = await endpoints.dashboard();
    expect(d.pending_approvals).toBe(1);
    expect(d.active_agents).toBe(3);
    const a2a = await endpoints.a2aTasks();
    expect(a2a.items.some((t) => t.state === "completed" && t.history.some((h) => h.state === "input-required"))).toBe(true);
    advanceMockClock(5 * 3600_000);
    db.orders.forEach((r) => settleOrder(r));
    expect((await endpoints.listOrders({ status: "PENDING_APPROVAL" })).total).toBe(0);
  });
});
