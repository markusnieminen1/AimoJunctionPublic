// M7: dashboard KPIs (exact API values), audit log filters (request_id sent to the API, URL deep link), A2A trace.
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { endpoints } from "../api/endpoints";
import { clearSession } from "../auth/session";
import { resetMockClock } from "../mocks/clock";
import { db, resetMockDb } from "../mocks/db";
import { setupMockServer } from "../mocks/server";
import { loginDemo, renderAt } from "../test/renderApp";

const server = setupMockServer();
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());
beforeEach(async () => {
  resetMockDb();
  resetMockClock();
  clearSession();
  window.localStorage.clear();
  await loginDemo();
});

function recordAuditQueries(): URLSearchParams[] {
  const seen: URLSearchParams[] = [];
  server.use(
    http.get("*/api/audit", ({ request }) => {
      seen.push(new URL(request.url).searchParams);
      return undefined;
    }),
  );
  return seen;
}

describe("DashboardPage", () => {
  it("renders the six KPI tiles exactly as returned by the API", async () => {
    server.use(
      http.get("*/api/dashboard", () =>
        HttpResponse.json({
          pending_approvals: 2,
          orders_today: 7,
          spend_today_eur: 1234.5,
          spend_7d_eur: 9876.54,
          co2_7d_kg: 321.4,
          active_agents: 3,
          recent_orders: [],
        }),
      ),
    );
    renderAt("/");
    expect(await screen.findByTestId("kpi-pending")).toHaveTextContent("2");
    expect(screen.getByTestId("kpi-orders-today")).toHaveTextContent("7");
    expect(screen.getByTestId("kpi-spend-today")).toHaveTextContent("1 234,50 €");
    expect(screen.getByTestId("kpi-spend-7d")).toHaveTextContent("9 876,54 €");
    expect(screen.getByTestId("kpi-co2-7d")).toHaveTextContent("~321");
    expect(screen.getByTestId("kpi-agents")).toHaveTextContent("3");
    expect(screen.getByTestId("kpi-pending").closest("a")).toHaveAttribute("href", "/approvals");
    expect(screen.getByText("No orders yet.")).toBeInTheDocument();
  });

  it("shows recent orders, the pending approval preview and active agents from the mock", async () => {
    renderAt("/");
    const recent = await screen.findByTestId("recent-orders");
    expect(within(recent).getAllByTestId("order-block")).toHaveLength(5);
    const preview = await screen.findByTestId("pending-preview");
    expect(within(preview).getByText("1 010,00 €")).toBeInTheDocument();
    expect(within(preview).getByText("R-AUTO-LIMIT")).toBeInTheDocument();
    expect(await screen.findByRole("link", { name: /Ward Diet Bot/ })).toBeInTheDocument();
  });
});

describe("AuditPage", () => {
  it("sends the request_id filter to the API and narrows the log", async () => {
    const seen = recordAuditQueries();
    const user = userEvent.setup();
    const target = db.audit.find((e) => e.action === "agent.create")!;
    renderAt("/audit");
    const table = await screen.findByTestId("audit-table");
    expect(within(table).getAllByRole("row").length).toBeGreaterThan(3);

    await user.type(screen.getByLabelText("Request ID"), `${target.request_id}{Enter}`);
    await waitFor(() => expect(seen.some((p) => p.get("request_id") === target.request_id)).toBe(true));
    await waitFor(() => expect(within(screen.getByTestId("audit-table")).getAllByRole("row")).toHaveLength(2));
    expect(within(screen.getByTestId("audit-table")).getByText("agent.create")).toBeInTheDocument();

    // expand → pretty-printed JSON details
    await user.click(within(screen.getByTestId("audit-table")).getByRole("button", { name: "Show details" }));
    expect(screen.getAllByTestId("audit-json")[0]).toHaveTextContent(`"name": "${(target.details as { name: string }).name}"`);
  });

  it("deep-links /audit?request_id=… and sends entity_type / actor_id filters", async () => {
    const seen = recordAuditQueries();
    const user = userEvent.setup();
    const target = db.audit[0]!;
    renderAt(`/audit?request_id=${target.request_id}`);
    expect(await screen.findByLabelText("Request ID")).toHaveValue(target.request_id);
    await waitFor(() => expect(seen.some((p) => p.get("request_id") === target.request_id)).toBe(true));

    await user.click(screen.getByRole("button", { name: "Clear filters" }));
    await user.selectOptions(screen.getByLabelText("Entity"), "agent");
    await waitFor(() => expect(seen.some((p) => p.get("entity_type") === "agent" && !p.get("request_id"))).toBe(true));
    await user.selectOptions(await screen.findByLabelText("Actor"), "agt_kitchen01");
    await waitFor(() => expect(seen.some((p) => p.get("actor_id") === "agt_kitchen01" && p.get("entity_type") === "agent")).toBe(true));
  });

  it("shows the A2A trace with a state stepper and linked order", async () => {
    const user = userEvent.setup();
    renderAt("/audit");
    await user.click(await screen.findByRole("tab", { name: /A2A protocol trace/ }));
    const caps = await screen.findByTestId("a2a-session-capabilities");
    expect(caps).toHaveTextContent("Menu Planning");
    expect(caps).toHaveTextContent("Preliminary Tendering");
    expect(caps).toHaveTextContent("Exception Handling");
    expect(within(caps).getByTestId("audit-negotiation-admin-link")).toHaveAttribute("href", "/admin");
    const tasks = await screen.findAllByTestId("a2a-task");
    expect(tasks.length).toBeGreaterThanOrEqual(2);
    const live = tasks.find((t) => within(t).queryByText(/Split the order or switch/))!;
    const steps = within(live).getByTestId("a2a-stepper");
    // Real gateway shape: the approval wait is `input-required` + data.reason PENDING_APPROVAL → its own step.
    expect(steps).toHaveTextContent(/working.*input-required.*PENDING_APPROVAL.*completed/);
    const pending = db.orders.map((r) => r.order).find((o) => o.status === "PENDING_APPROVAL")!;
    expect(within(live).getByRole("link", { name: new RegExp(pending.id) })).toHaveAttribute("href", `/orders/${pending.id}`);
    // Long A2A histories (>4 turns) collapse to the last 4 turns until expanded.
    const toggles = screen.queryAllByTestId(/a2a-toggle-history-/);
    for (const toggle of toggles) {
      expect(toggle).toHaveTextContent(/earlier/);
      await user.click(toggle);
      expect(toggle).toHaveTextContent("Collapse earlier turns");
    }

    // Structured option objects are named by product + storage, never "[object Object]", and link to /products/:id.
    const earlier = tasks.find((t) => within(t).queryByText(/chilled \(100001\) or UHT/))!;
    expect(within(earlier).getByRole("link", { name: "100001 (chilled)" })).toHaveAttribute("href", "/products/100001");
    expect(within(earlier).getByRole("link", { name: "100002 (uht)" })).toHaveAttribute("href", "/products/100002");
    expect(within(earlier).getAllByText("R-AUTO-LIMIT").length).toBeGreaterThan(0);
    expect(screen.queryByText(/\[object Object\]/)).not.toBeInTheDocument();
  });

  it("shows a clean empty state when the server has no A2A endpoint (404)", async () => {
    server.use(
      http.get("*/api/a2a/tasks", () =>
        HttpResponse.json({ error: { code: "NOT_FOUND", message: "Not Found", details: {} }, request_id: "req_test_404" }, { status: 404 }),
      ),
    );
    renderAt("/audit?view=a2a");
    expect(await screen.findByText("A2A trace not available on this server")).toBeInTheDocument();
  });

  it("shows the Governance & Integrity summary strip and expands tools + Postman note", async () => {
    const user = userEvent.setup();
    renderAt("/audit");
    const strip = await screen.findByTestId("governance-strip");
    expect(within(strip).getByTestId("audit-chain-status")).toHaveTextContent(/Verified · \d+ events/);
    const funnel = within(strip).getByTestId("governance-funnel");
    expect(within(funnel).getByTestId("governance-gate-SAFETY_LOCK")).toHaveTextContent("R-HARD-DIET");
    expect(within(funnel).getByTestId("governance-gate-OPERATIONAL_SANITY")).toBeInTheDocument();
    expect(within(funnel).getByTestId("governance-gate-AUTONOMY_ENVELOPE")).toBeInTheDocument();
    expect(within(strip).getByRole("link", { name: /Manage promotions/ })).toHaveAttribute("href", "/account#promotions");

    await user.click(within(strip).getByRole("button", { name: "Show invariants & tools" }));
    const details = await within(strip).findByTestId("governance-details");
    expect(details).toHaveTextContent(/MCP tools/);
    expect(details).toHaveTextContent("AimoAnnos_Governance_Suite.postman_collection.json");
  });

  it("renders reasoning_steps above collapsed raw JSON when an audit event contains reasoning steps", async () => {
    const user = userEvent.setup();
    const withReasoning = db.audit.find((e) => Array.isArray((e.details as { reasoning_steps?: unknown }).reasoning_steps))!;
    renderAt(`/audit?request_id=${withReasoning.request_id}`);
    const table = await screen.findByTestId("audit-table");
    await user.click(within(table).getByRole("button", { name: "Show details" }));
    expect(await within(table).findByTestId("audit-reasoning-steps")).toBeInTheDocument();
    expect(within(table).getByText("Raw JSON payload")).toBeInTheDocument();
  });

  it("hides the Flight simulator tab when /api/dev/simulator/scenarios returns 404", async () => {
    server.use(
      http.get("*/api/dev/simulator/scenarios", () =>
        HttpResponse.json({ detail: "Not Found" }, { status: 404 }),
      ),
    );
    const first = renderAt("/audit");
    await screen.findByTestId("audit-table");
    expect(screen.queryByRole("tab", { name: "Flight simulator" })).not.toBeInTheDocument();
    first.unmount();

    renderAt("/audit?view=simulator");
    expect(await screen.findByText("Flight simulator disabled")).toBeInTheDocument();
  });

  it("runs all 5 Flight Simulator chaos scenarios and displays rules, transitions, substitutes, and audit chain proof", async () => {
    const user = userEvent.setup();
    renderAt("/audit");
    await user.click(await screen.findByRole("tab", { name: "Flight simulator" }));
    expect(await screen.findByTestId("flight-simulator")).toBeInTheDocument();

    await user.click(screen.getByTestId("simulator-run-unit_trap"));
    const unitOut = await screen.findByTestId("simulator-output-unit_trap");
    expect(within(unitOut).getByText("R-LINE-QTY")).toBeInTheDocument();
    expect(within(unitOut).getByTestId("simulator-transition-unit_trap")).toHaveTextContent("PENDING_APPROVAL");
    expect(within(unitOut).getByTestId("simulator-order-link-unit_trap")).toHaveAttribute("href", expect.stringMatching(/^\/orders\//));

    await user.click(screen.getByTestId("simulator-run-diet_breach"));
    const dietOut = await screen.findByTestId("simulator-output-diet_breach");
    expect(within(dietOut).getByText("R-HARD-DIET")).toBeInTheDocument();
    expect(within(dietOut).getByText(/403/)).toBeInTheDocument();

    await user.click(screen.getByTestId("simulator-run-gateway_timeout_recovery"));
    const gwOut = await screen.findByTestId("simulator-output-gateway_timeout_recovery");
    expect(within(gwOut).getByTestId("simulator-transition-gateway_timeout_recovery")).toHaveTextContent(/SUBMISSION_UNKNOWN.*PLACED/);

    await user.click(screen.getByTestId("simulator-run-partial_and_cutoff"));
    const subOut = await screen.findByTestId("simulator-output-partial_and_cutoff");
    expect(within(subOut).getByText("R-DELIVERY-RISK")).toBeInTheDocument();
    expect(within(subOut).getByTestId("simulator-subs-partial_and_cutoff")).toBeInTheDocument();

    await user.click(screen.getByTestId("simulator-run-tampered_approval_hash"));
    const tamperOut = await screen.findByTestId("simulator-output-tampered_approval_hash");
    expect(within(tamperOut).getByText(/409.*CONTENT_HASH_MISMATCH/)).toBeInTheDocument();
    expect(screen.getByTestId("simulator-result-badge-tampered_approval_hash")).toHaveTextContent("PASSED");
  });

  it("inspects Core API vs Agentic Service routing presets on /audit and handles 404 gracefully", async () => {
    const user = userEvent.setup();
    renderAt("/audit");

    const inspector = await screen.findByTestId("request-router-inspector");
    await user.click(within(inspector).getByTestId("router-preset-quote_order"));

    expect(await within(inspector).findByTestId("router-target-service")).toHaveTextContent("core-api");
    expect(within(inspector).getByTestId("router-matched-fields")).toHaveTextContent("none");
    expect(within(inspector).getByTestId("router-structured-fields")).toHaveTextContent("quote_id");
    expect(within(inspector).getByTestId("router-downstream-flow")).toHaveTextContent(/policy\.py.*content_hash/);

    await user.click(within(inspector).getByTestId("router-preset-nl_order"));
    expect(await within(inspector).findByTestId("router-target-service")).toHaveTextContent("agentic-service");
    expect(within(inspector).getByTestId("router-matched-fields")).toHaveTextContent("prompt");
    expect(within(inspector).getByTestId("router-downstream-flow")).toHaveTextContent(/agentic-service.*core-api.*policy\.py.*content_hash/);

    server.use(
      http.post("*/api/routing/inspect", () => HttpResponse.json({ detail: "Not Found" }, { status: 404 })),
    );
    await user.click(within(inspector).getByTestId("router-preset-quote_lines"));
    expect(await within(inspector).findByTestId("router-unavailable")).toBeInTheDocument();
  });

  it("filters by action and outcome and renders rich assistant_chat trace cards", async () => {
    const user = userEvent.setup();
    await endpoints.assistantChat({ message: "20 liters of milk" });
    db.assistantDown = true;
    try {
      await endpoints.assistantChat({ message: "Order oat milk" });
    } catch {
      // expected 503 emits assistant.processing_error
    }
    db.assistantDown = false;

    const seen = recordAuditQueries();
    renderAt("/audit");

    await user.selectOptions(await screen.findByLabelText("Entity"), "assistant_chat");
    await waitFor(() => {
      expect(seen.some((p) => p.get("entity_type") === "assistant_chat")).toBe(true);
    });
    const table = await screen.findByTestId("audit-table");
    expect(await within(table).findByText("assistant.chat_turn")).toBeInTheDocument();
    const expandBtns = await within(table).findAllByRole("button", { name: "Show details" });
    expect(expandBtns.length).toBeGreaterThanOrEqual(2);
    await user.click(expandBtns[0]!);
    await user.click(expandBtns[1]!);
    const cards = await within(table).findAllByTestId("audit-chat-trace-card");
    expect(cards.length).toBeGreaterThanOrEqual(2);
    expect(cards.some((c) => /20 liters of milk/.test(c.textContent ?? ""))).toBe(true);

    await user.selectOptions(screen.getByTestId("audit-action-filter"), "assistant.processing_error");
    await user.selectOptions(screen.getByTestId("audit-outcome-filter"), "error");
    await waitFor(() => {
      expect(seen.some((p) => p.get("action") === "assistant.processing_error" && p.get("outcome") === "error")).toBe(true);
    });
    await waitFor(() => {
      expect(within(screen.getByTestId("audit-table")).getAllByTestId("audit-chat-trace-card")).toHaveLength(1);
    });
    expect(within(screen.getByTestId("audit-table")).getByTestId("audit-chat-trace-card")).toHaveTextContent("Order oat milk");
  });
});
