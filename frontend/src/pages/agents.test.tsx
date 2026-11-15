// M3: agents table from MSW seeds, create flow with the one-time secret, detail kill switch + rotate.
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http } from "msw";
import type { Agent } from "../api/types";
import { clearSession } from "../auth/session";
import { agentPolicyHeadline } from "../components/agents/AgentBits";
import { DEFAULT_AGENT_FORM, validateAgentForm } from "../components/agents/AgentForm";
import { db, resetMockDb } from "../mocks/db";
import { setupMockServer } from "../mocks/server";
import { loginDemo, renderAt } from "../test/renderApp";

const server = setupMockServer();
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());
beforeEach(async () => {
  resetMockDb();
  clearSession();
  window.localStorage.clear();
  await loginDemo();
});

describe("agent helpers", () => {
  it("states the limits in plain English", () => {
    const a = { status: "active", can_place_orders: true, auto_approve_limit_eur: 500, max_order_value_eur: 5000, daily_spend_limit_eur: 3000, allowed_categories: [], max_quantity_per_line: null, expires_at: null } as unknown as Agent;
    expect(agentPolicyHeadline(a).replace(/\u00a0/g, " ")).toBe("Orders up to 500 € are placed automatically. 500 €–5 000 € need your approval. Over 5 000 € are blocked.");
  });

  it("validates input (auto ≤ max, positive numbers, future expiry)", () => {
    expect(validateAgentForm({ ...DEFAULT_AGENT_FORM, name: "X" }, "2026-11-14")).toEqual({});
    const e = validateAgentForm({ ...DEFAULT_AGENT_FORM, name: "", auto: "600", max: "500", daily: "-1", maxQty: "1.5", expires: "2026-11-14" }, "2026-11-14");
    expect(Object.keys(e).sort()).toEqual(["auto", "daily", "expires", "maxQty", "name"]);
    expect(validateAgentForm({ ...DEFAULT_AGENT_FORM, name: "X", max: "0" }, "2026-11-14").max).toBeTruthy();
  });
});

describe("AgentsPage", () => {
  it("lists the seeded agents with the policy card", async () => {
    renderAt("/agents");
    expect(await screen.findAllByText("Kitchen Reorder Bot")).not.toHaveLength(0);
    expect(screen.getAllByText("Read-only Scout").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Ward Diet Bot").length).toBeGreaterThan(0);
    const table = screen.getByRole("table");
    expect(within(table).getAllByRole("row")).toHaveLength(4); // header + 3 agents
    expect(screen.getByText(/Orders up to 500 € are placed automatically\. 500 €–5 000 € need your approval\. Over 5 000 € are blocked\./)).toBeInTheDocument();
    expect(screen.getAllByText("Search & quote only").length).toBeGreaterThan(0);
  });

  it("creates an agent and shows the secret exactly once", async () => {
    const user = userEvent.setup();
    renderAt("/agents?create=1");
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("Anna Korhonen")).toBeInTheDocument(); // Authorized by <you>

    // input validation: auto above max
    await user.clear(within(dialog).getByLabelText("Auto-approve limit"));
    await user.type(within(dialog).getByLabelText("Auto-approve limit"), "9000");
    await user.click(within(dialog).getByRole("button", { name: "Create agent" }));
    expect(within(dialog).getByText("Agent name is required")).toBeInTheDocument();
    expect(within(dialog).getByText("Must be at most the max order value")).toBeInTheDocument();

    await user.type(within(dialog).getByLabelText("Agent name"), "Bakery Bot");
    await user.clear(within(dialog).getByLabelText("Auto-approve limit"));
    await user.type(within(dialog).getByLabelText("Auto-approve limit"), "250");
    await user.click(within(dialog).getByRole("button", { name: "Create agent" }));

    const secretBox = await screen.findByTestId("secret-agent_secret");
    const secret = secretBox.textContent ?? "";
    expect(secret).toMatch(/^aks_/);
    expect(screen.getByText("You will not see this secret again.")).toBeInTheDocument();
    expect(screen.getByText(/\/api\/agent-auth\/token/, { selector: "pre" })).toHaveTextContent(secret);
    expect(screen.getByTestId("secret-mcp-json")).toHaveTextContent(secret);
    expect(screen.getByTestId("secret-mcp-warning")).toHaveTextContent("Never commit .mcp.json");

    // In-memory download buttons trigger createObjectURL without storing secret in web storage.
    const createObjectURL = vi.fn(() => "blob:mock-download");
    const revokeObjectURL = vi.fn();
    const origCreate = URL.createObjectURL;
    const origRevoke = URL.revokeObjectURL;
    URL.createObjectURL = createObjectURL;
    URL.revokeObjectURL = revokeObjectURL;
    try {
      await user.click(screen.getByTestId("download-claude-zip"));
      await user.click(screen.getByTestId("download-mcp-json"));
      expect(createObjectURL).toHaveBeenCalledTimes(2);
    } finally {
      URL.createObjectURL = origCreate;
      URL.revokeObjectURL = origRevoke;
    }
    expect(JSON.stringify(localStorage)).not.toContain(secret);
    expect(JSON.stringify(sessionStorage)).not.toContain(secret);

    await user.click(screen.getByRole("button", { name: "I have stored the secret" }));
    await waitFor(() => expect(screen.queryByText(secret, { exact: false })).not.toBeInTheDocument());
    expect((await screen.findAllByText("Bakery Bot")).length).toBeGreaterThan(0);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});

describe("AgentDetailPage", () => {
  it("disables the agent via the kill switch and logs it in activity", async () => {
    const user = userEvent.setup();
    renderAt("/agents/agt_kitchen01");
    expect(await screen.findByRole("heading", { level: 1, name: /Kitchen Reorder Bot/ })).toBeInTheDocument();

    // Switch to Orders tab to verify seeded agent orders (incl. the pending €1,010.00 card)
    await user.click(screen.getByRole("tab", { name: "Orders" }));
    expect(await screen.findByText("1 010,00 €")).toBeInTheDocument();

    // Switch back to Activity tab before disabling so audit event is checked
    await user.click(screen.getByRole("tab", { name: "Activity" }));

    await user.click(screen.getByRole("button", { name: "Disable agent" }));
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByText("Disable this agent?")).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Disable agent" }));

    expect(await screen.findByText("Kill switch is on.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Enable agent" })).toBeInTheDocument();
    expect(await screen.findByText("agent.disable")).toBeInTheDocument();
  });

  it("asks the server for this agent's orders only (agent_id)", async () => {
    const seen: URLSearchParams[] = [];
    server.use(
      http.get("*/api/orders", ({ request }) => {
        seen.push(new URL(request.url).searchParams);
        return undefined; // fall through to the mock API
      }),
    );
    const user = userEvent.setup();
    renderAt("/agents/agt_kitchen01");
    await user.click(await screen.findByRole("tab", { name: "Orders" }));
    expect(await screen.findByText("1 010,00 €")).toBeInTheDocument();
    expect(seen.some((p) => p.get("agent_id") === "agt_kitchen01")).toBe(true);
  });

  it("rotates the secret behind a confirm and shows the new one once", async () => {
    const user = userEvent.setup();
    renderAt("/agents/agt_scout01");
    await screen.findByRole("heading", { level: 1, name: /Read-only Scout/ });
    await user.click(screen.getByRole("button", { name: "Rotate secret" }));
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Rotate secret" }));
    const box = await screen.findByTestId("secret-agent_secret");
    expect(box.textContent).toMatch(/^aks_/);
    expect(box.textContent).not.toBe("aks_demo_readonly_scout");
  });

  it("saves edited limits with PATCH", async () => {
    const user = userEvent.setup();
    renderAt("/agents/agt_ward01");
    await screen.findByRole("heading", { level: 1, name: /Ward Diet Bot/ });
    const save = screen.getByRole("button", { name: "Save changes" });
    expect(save).toBeDisabled();
    await user.clear(screen.getByLabelText("Auto-approve limit"));
    await user.type(screen.getByLabelText("Auto-approve limit"), "400");
    await user.click(save);
    await waitFor(() => expect(screen.getAllByText(/Orders up to 400 € are placed automatically\./).length).toBeGreaterThan(0));
  });
});

describe("Batch 2: New Env provisioning, Universal Agent Docs, and Quarantine Anomaly Review", () => {
  it("provisions an isolated New Env + agent secret from TopBar and exposes Add to Claude + Copy this to agent", async () => {
    const user = userEvent.setup();
    renderAt("/agents");
    await screen.findByRole("heading", { level: 1, name: "Agents & permissions" });

    const newEnvBtn = await screen.findByTestId("new-env-btn");
    await user.click(newEnvBtn);

    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(/New Env Ready/)).toBeInTheDocument();
    expect(within(dialog).getByTestId("secret-modal-subtitle")).toHaveTextContent(/zero seeded Telegram recipients/i);
    expect(within(dialog).getByTestId("download-claude-zip")).toBeInTheDocument();
    expect(within(dialog).getByTestId("copy-claude-cli-btn")).toBeInTheDocument();
    expect(within(dialog).getByTestId("copy-universal-agent-prompt")).toBeInTheDocument();

    const secret = within(dialog).getByTestId("secret-agent_secret").textContent!;
    expect(secret).toMatch(/^aks_/);

    await user.click(within(dialog).getByRole("button", { name: "I have stored the secret" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("expands on-page universal agent docs across Claude/MCP, LangGraph, and OpenAPI tabs", async () => {
    const user = userEvent.setup();
    renderAt("/agents");

    const docs = await screen.findByTestId("universal-agent-docs");
    expect(within(docs).getByTestId("copy-agent-docs-prompt")).toBeInTheDocument();
    expect(within(docs).getByTestId("negotiation-admin-link")).toHaveAttribute("href", "/admin");
    expect(await screen.findByTestId("assurance-settings-card")).toBeInTheDocument();

    await user.click(within(docs).getByTestId("toggle-agent-docs"));
    const body = await within(docs).findByTestId("universal-agent-docs-body");
    expect(body).toHaveTextContent("./mcp/aimo_mcp_bridge.py");

    await user.click(within(docs).getByTestId("agent-docs-tab-langgraph"));
    expect(body).toHaveTextContent("langchain_core.tools");

    await user.click(within(docs).getByTestId("agent-docs-tab-rest"));
    expect(body).toHaveTextContent("/api/agent-auth/token");

    const matters = await within(docs).findByTestId("a2a-matters-strip");
    expect(matters).toHaveTextContent("Menu Planning");
    expect(matters).toHaveTextContent("Order Execution");
    expect(matters).toHaveTextContent("Preliminary Tendering");
    expect(matters).toHaveTextContent("Exception Handling");

    await user.click(within(docs).getByTestId("agent-docs-tab-a2a"));
    expect(body).toHaveTextContent("session/connect");
    expect(body).toHaveTextContent("/api/.well-known/agent-card.json");
  });

  it("renders quarantined agent anomaly banner on /agents and restores access cleanly", async () => {
    const user = userEvent.setup();
    db.agents[0]!.status = "quarantined";
    db.anomalies.push({
      company_id: "cmp_aurora",
      agent_id: "agt_kitchen01",
      agent_name: "Kitchen Reorder Bot",
      status: "quarantined",
      trigger_reason: "Duplicate order fingerprint repeated 3x within 10 minutes",
      rule_code: "R-ANOMALY-SPAM",
      quarantined_at: "2026-11-14T14:30:00Z",
      purchasing_characteristics: { identical_hash_repeats: 3, window_minutes: 10 },
      recent_request_ids: ["req_spam01", "req_spam02"],
    });

    renderAt("/agents");
    const banner = await screen.findByTestId("agent-anomalies-banner");
    expect(within(banner).getByText("Kitchen Reorder Bot")).toBeInTheDocument();
    expect(within(banner).getByText("R-ANOMALY-SPAM")).toBeInTheDocument();
    expect(within(banner).getByText("identical_hash_repeats=3")).toBeInTheDocument();
    expect(screen.getAllByText("Quarantined").length).toBeGreaterThanOrEqual(1);

    await user.click(within(banner).getByTestId("anomaly-restore-agt_kitchen01"));
    await waitFor(() => expect(screen.queryByTestId("agent-anomalies-banner")).not.toBeInTheDocument());
    expect(db.agents[0]!.status).toBe("active");
  });
});
