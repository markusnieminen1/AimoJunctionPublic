// Batch C + Batch G + Batch I: /admin = Valio Aimo Internal Console (Organization Bot Negotiation + Invisible Platform Campaigns),
// /account = Client Company Profile & Settings (Agent settings & permissions + Category Brand Preferences + Promotions),
// agent 403 RBAC enforcement, /api/quotes/negotiate endpoint, and NegotiatedSavingsBadge display.
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { endpoints } from "../api/endpoints";
import { api } from "../api/client";
import { clearSession, setSession } from "../auth/session";
import { NegotiatedSavingsBadge } from "../components/NegotiatedSavingsBadge";
import { I18nProvider } from "../i18n";
import { resetMockClock } from "../mocks/clock";
import { resetMockDb } from "../mocks/db";
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
  window.sessionStorage.clear();
  await loginDemo();
});

describe("Valio Aimo Admin Console (/admin) & Client Company Settings (/account)", () => {
  it("renders /admin as standalone Valio Aimo Internal Console without BrandPreferencesPanel and supports campaign CRUD", async () => {
    const u = userEvent.setup();
    renderAt("/admin");

    const consoleRoot = await screen.findByTestId("aimo-internal-console");
    expect(within(consoleRoot).getByTestId("aimo-internal-eyebrow")).toHaveTextContent(/VALIO AIMO INTERNAL · PLATFORM CONSOLE/i);
    expect(within(consoleRoot).getByRole("heading", { level: 1, name: "Valio Aimo Admin Console" })).toBeInTheDocument();
    expect(within(consoleRoot).getByTestId("aimo-console-back-link")).toHaveAttribute("href", "/account");

    // Unwrapped from client SettingsLayout and contains NO client-company BrandPreferencesPanel
    expect(screen.queryByTestId("account-tabs")).not.toBeInTheDocument();
    expect(screen.queryByTestId("brand-preferences-card")).not.toBeInTheDocument();
    expect(await screen.findByTestId("negotiation-settings-card")).toBeInTheDocument();

    const notice = await screen.findByTestId("invisible-campaigns-notice");
    expect(notice).toHaveTextContent(/Invisible ranking signals/i);
    expect(notice).toHaveTextContent(/never billed/i);

    await u.click(await screen.findByTestId("new-campaign-btn"));
    const boostInput = screen.getByTestId("campaign-boost-input");
    expect(boostInput).toHaveAttribute("min", "0.1");
    expect(boostInput).toHaveAttribute("max", "10");
    await u.clear(boostInput);
    await u.type(boostInput, "0.5");
    await u.type(screen.getByTestId("campaign-title-input"), "Autumn Oat Promo");
    await u.type(screen.getByTestId("campaign-brand-input"), "Oddlygood");
    await u.click(screen.getByTestId("campaign-save-btn"));

    expect(await screen.findByText("Autumn Oat Promo")).toBeInTheDocument();

    const list = await endpoints.listAimoCampaigns();
    const created = list.items.find((c) => c.title === "Autumn Oat Promo");
    expect(created).toBeTruthy();
    expect(created?.boost_weight).toBe(0.5);

    await u.click(screen.getByTestId(`campaign-toggle-${created!.id}`));
    await waitFor(async () => {
      const updated = (await endpoints.listAimoCampaigns()).items.find((c) => c.id === created!.id);
      expect(updated?.active).toBe(false);
    });

    await u.click(screen.getByTestId(`campaign-delete-${created!.id}`));
    await waitFor(() => {
      expect(screen.queryByText("Autumn Oat Promo")).not.toBeInTheDocument();
    });
  });

  it("renders Client Company Profile & Settings on /account with direct Agent settings links and Category Brand Preferences", async () => {
    const u = userEvent.setup();
    renderAt("/account");

    expect(await screen.findByRole("heading", { level: 1, name: "Company profile & settings" })).toBeInTheDocument();
    expect(screen.queryByText(/cannot be edited here/i)).not.toBeInTheDocument();

    // Client SettingsLayout has ONLY the 3 client-company tabs (/account, /agents, /audit — NOT /admin)
    const clientTabs = screen.getByTestId("account-tabs");
    const tabHrefs = Array.from(clientTabs.querySelectorAll("a")).map((a) => a.getAttribute("href"));
    expect(tabHrefs).toEqual(["/account", "/agents", "/audit"]);

    // Direct Agent settings & permissions card on /account
    const agentCard = await screen.findByTestId("account-agent-settings-card");
    expect(agentCard).toHaveTextContent(/AI order assurance: 80%/i);
    expect(agentCard).toHaveTextContent(/Disallowed by Valio Aimo/i);
    expect(within(agentCard).getByTestId("account-negotiation-console-link")).toHaveAttribute("href", "/admin");
    expect(within(agentCard).getByTestId("account-manage-agents-link")).toHaveAttribute("href", "/agents");
    expect(within(agentCard).getByTestId("account-create-agent-link")).toHaveAttribute("href", "/agents/new");

    const editKitchenBtn = await within(agentCard).findByTestId("account-edit-agent-agt_kitchen01");
    expect(editKitchenBtn).toHaveAttribute("href", "/agents/agt_kitchen01");

    // Category Brand Preferences moved from /admin into /account
    expect(await screen.findByTestId("brand-preferences-card")).toBeInTheDocument();
    const catSelect = await screen.findByTestId("pref-category-select");
    await waitFor(() => expect(catSelect.querySelectorAll("option").length).toBeGreaterThan(1));
    const firstCode = (catSelect.querySelectorAll("option")[1] as HTMLOptionElement).value;
    await u.selectOptions(catSelect, firstCode);
    await u.type(screen.getByTestId("pref-brands-input"), "Valio, Nordic Dairy");
    await u.click(screen.getByTestId("pref-save-btn"));

    await waitFor(() => {
      expect(screen.getAllByText(/Valio, Nordic Dairy/i).length).toBeGreaterThan(0);
    });
    const list = await endpoints.listBrandPreferences();
    const created = list.items.find((p) => p.preferred_brands.includes("Nordic Dairy"));
    expect(created).toBeTruthy();

    await u.click(screen.getByTestId(`pref-delete-${created!.id}`));
    await waitFor(async () => {
      const after = await endpoints.listBrandPreferences();
      expect(after.items.some((p) => p.id === created!.id)).toBe(false);
    });
  });

  it("manages per-company allow_price_negotiation on /admin (with none on /account) and updates agent capabilities", async () => {
    const u = userEvent.setup();
    const accountView = renderAt("/account");
    await screen.findByRole("heading", { level: 1, name: "Company profile & settings" });
    expect(screen.queryByTestId("negotiation-settings-card")).not.toBeInTheDocument();
    expect(screen.queryByTestId("assurance-settings-card")).not.toBeInTheDocument();
    accountView.unmount();

    renderAt("/admin");

    const card = await screen.findByTestId("negotiation-settings-card");
    expect(card).toHaveTextContent(/Organization Bot Preliminary Negotiation/i);
    const auroraToggle = await within(card).findByTestId("company-negotiation-toggle-cmp_aurora");
    const bistroToggle = await within(card).findByTestId("company-negotiation-toggle-cmp_bistro");
    expect(auroraToggle).toHaveAttribute("aria-checked", "false");
    expect(bistroToggle).toHaveAttribute("aria-checked", "false");
    expect(within(card).getByText("Nordic Campus Catering Oy")).toBeInTheDocument();

    // Toggling Bistro Satama does NOT affect Hotel Aurora
    await u.click(bistroToggle);
    await waitFor(() => expect(bistroToggle).toHaveAttribute("aria-checked", "true"));
    expect((await endpoints.getNegotiationSettings()).allow_price_negotiation).toBe(false);

    // Toggling Hotel Aurora updates both /api/admin/company-negotiations and /api/account/negotiation-settings
    await u.click(auroraToggle);
    await waitFor(() => expect(auroraToggle).toHaveAttribute("aria-checked", "true"));
    const afterEnable = await endpoints.getNegotiationSettings();
    expect(afterEnable.allow_price_negotiation).toBe(true);
    expect(within(card).getByTestId("negotiation-status-pill")).toHaveTextContent(/Enabled/i);

    const a2aEnabled = await endpoints.a2aSessionConnect();
    expect(a2aEnabled.result?.session_greeting).toMatch(/can negotiate prices for larger quantities/i);
    const mcpInitEnabled = await api.post<{ result?: { instructions?: string } }>("/api/mcp", { jsonrpc: "2.0", id: "1", method: "initialize" });
    expect(mcpInitEnabled.result?.instructions).toMatch(/can negotiate prices for larger quantities/i);
    const mcpToolsEnabled = await api.post<{ result?: { tools?: { name: string; description?: string }[] } }>("/api/mcp", { jsonrpc: "2.0", id: "2", method: "tools/list" });
    expect(mcpToolsEnabled.result?.tools?.find((x) => x.name === "negotiate_quote")?.description).toMatch(/can negotiate prices for larger quantities/i);
    const chatEnabled = await endpoints.assistantChat({ message: "What are your capabilities?" });
    expect(chatEnabled.reply).toMatch(/can negotiate prices for larger quantities/i);

    await u.click(auroraToggle);
    await waitFor(() => expect(auroraToggle).toHaveAttribute("aria-checked", "false"));
    const afterDisable = await endpoints.getNegotiationSettings();
    expect(afterDisable.allow_price_negotiation).toBe(false);
    expect(within(card).getByTestId("negotiation-status-pill")).toHaveTextContent(/Disabled/i);

    const mcpToolsDisabled = await api.post<{ result?: { tools?: { name: string; description?: string }[] } }>("/api/mcp", { jsonrpc: "2.0", id: "3", method: "tools/list" });
    expect(mcpToolsDisabled.result?.tools?.find((x) => x.name === "negotiate_quote")?.description).toMatch(/DISABLED/i);
    const chatDisabled = await endpoints.assistantChat({ message: "What are your capabilities?" });
    expect(chatDisabled.reply).toMatch(/disabled/i);
    expect(chatDisabled.reply).toMatch(/\/admin/);
  });

  it("enforces agent RBAC (read-only UI + 403 on admin/settings writes) and gates /api/quotes/negotiate", async () => {
    const prodPage = await endpoints.searchProducts({ limit: 1 });
    const prod = prodPage.items[0]!;
    await expect(
      endpoints.negotiateQuote({
        requested_delivery_date: "2026-11-16",
        lines: [{ product_id: prod.id, quantity: 20 }],
        target_discount_pct: 5,
      }),
    ).rejects.toMatchObject({ status: 403, code: "NEGOTIATION_DISABLED" });

    await endpoints.updateNegotiationSettings({ allow_price_negotiation: true });

    const negRes = await endpoints.negotiateQuote({
      requested_delivery_date: "2026-11-16",
      lines: [{ product_id: prod.id, quantity: 20 }],
      target_discount_pct: 5,
    });
    expect(["ACCEPTED", "COUNTER_OFFER", "FLOOR_REACHED"]).toContain(negRes.status);
    expect((negRes.quote.negotiated_savings_eur ?? 0)).toBeGreaterThan(0);
    expect(negRes.rounds.length).toBeGreaterThanOrEqual(2);

    const placed = await endpoints.createOrder(
      { quote_id: negRes.quote.id },
      "idem-negotiated-order-1",
    );
    expect(placed.negotiated_savings_eur).toBe(negRes.quote.negotiated_savings_eur);

    await endpoints.updateNegotiationSettings({ allow_price_negotiation: false });
    await expect(
      endpoints.negotiateQuote({
        requested_delivery_date: "2026-11-16",
        lines: [{ product_id: prod.id, quantity: 20 }],
        target_discount_pct: 5,
      }),
    ).rejects.toMatchObject({ status: 403, code: "NEGOTIATION_DISABLED" });

    renderAt(`/orders/${placed.id}`);
    expect(await screen.findByTestId("negotiated-savings-badge")).toBeInTheDocument();

    const { agent, agent_secret } = await endpoints.createAgent({
      name: "RBAC Bot",
      auto_approve_limit_eur: 100,
      max_order_value_eur: 500,
      daily_spend_limit_eur: 1000,
      can_place_orders: true,
    });
    const tok = await endpoints.agentToken({ agent_id: agent.id, agent_secret });
    setSession({
      token: tok.token,
      principal: { kind: "agent", id: agent.id, name: agent.name, company_id: placed.created_by.company_id },
      company: { id: placed.created_by.company_id, name: "Hotel Aurora Oy" },
    });
    const me = await endpoints.me();
    setSession({
      token: tok.token,
      principal: me.principal,
      company: me.company,
    });

    await expect(
      endpoints.createAimoCampaign({
        title: "Unauthorized Bot Campaign",
        boost_weight: 2,
        active: true,
      }),
    ).rejects.toMatchObject({ status: 403 });

    await expect(
      endpoints.createBrandPreference({
        category_prefix: "100",
        category_label: "Dairy",
        preferred_brands: ["Valio"],
        active: true,
      }),
    ).rejects.toMatchObject({ status: 403 });

    await expect(
      endpoints.updateNegotiationSettings({ allow_price_negotiation: true }),
    ).rejects.toMatchObject({ status: 403 });

    await expect(
      endpoints.listCompanyNegotiations(),
    ).rejects.toMatchObject({ status: 403 });

    await expect(
      endpoints.updateCompanyNegotiation(placed.created_by.company_id, { allow_price_negotiation: true }),
    ).rejects.toMatchObject({ status: 403 });

    renderAt("/admin");
    expect(await screen.findByTestId("admin-readonly-notice")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.queryByTestId("new-campaign-btn")).not.toBeInTheDocument();
    const toggle = await screen.findByTestId(`company-negotiation-toggle-${placed.created_by.company_id}`);
    expect(toggle).toBeDisabled();
  });

  it("renders NegotiatedSavingsBadge sharply only when savings > 0", () => {
    const { rerender } = render(
      <I18nProvider>
        <NegotiatedSavingsBadge savingsEur={0} />
      </I18nProvider>,
    );
    expect(screen.queryByTestId("negotiated-savings-badge")).not.toBeInTheDocument();

    rerender(
      <I18nProvider>
        <NegotiatedSavingsBadge savingsEur={null} />
      </I18nProvider>,
    );
    expect(screen.queryByTestId("negotiated-savings-badge")).not.toBeInTheDocument();

    rerender(
      <I18nProvider>
        <NegotiatedSavingsBadge savingsEur={18.5} />
      </I18nProvider>,
    );
    const badge = screen.getByTestId("negotiated-savings-badge");
    expect(badge).toBeInTheDocument();
    expect(badge.className).toContain("rounded-none");
    expect(badge.textContent).toMatch(/18[.,]50/);
  });

  it("collapses QA and Demo Kitchen sandbox organizations behind an optional toggle on /admin", async () => {
    const u = userEvent.setup();
    server.use(
      http.get("*/api/admin/company-negotiations", () =>
        HttpResponse.json({
          items: [
            { company_id: "cmp_aurora", company_name: "Hotel Aurora Oy", allow_price_negotiation: false, max_auto_discount_pct: 8, updated_at: null },
            { company_id: "cmp_bistro", company_name: "Bistro Satama Oy", allow_price_negotiation: true, max_auto_discount_pct: 8, updated_at: null },
            { company_id: "cmp_nordic", company_name: "Nordic Campus Catering Oy", allow_price_negotiation: false, max_auto_discount_pct: 8, updated_at: null },
            { company_id: "cmp_qa1", company_name: "QA Smoke Test Org", allow_price_negotiation: false, max_auto_discount_pct: 8, updated_at: null },
            { company_id: "cmp_demo99", company_name: "Demo Kitchen 99", allow_price_negotiation: false, max_auto_discount_pct: 8, updated_at: null },
          ],
        }),
      ),
    );

    renderAt("/admin");
    const card = await screen.findByTestId("negotiation-settings-card");
    expect(await within(card).findByText("Hotel Aurora Oy")).toBeInTheDocument();
    expect(within(card).getByText("Bistro Satama Oy")).toBeInTheDocument();
    expect(within(card).getByText("Nordic Campus Catering Oy")).toBeInTheDocument();
    expect(within(card).queryByText("QA Smoke Test Org")).not.toBeInTheDocument();
    expect(within(card).queryByText("Demo Kitchen 99")).not.toBeInTheDocument();

    const toggleBtn = within(card).getByTestId("toggle-sandbox-orgs");
    expect(toggleBtn).toHaveTextContent(/Show sandbox\/QA organizations \(2\)/i);
    await u.click(toggleBtn);

    expect(await within(card).findByText("QA Smoke Test Org")).toBeInTheDocument();
    expect(within(card).getByText("Demo Kitchen 99")).toBeInTheDocument();
    expect(toggleBtn).toHaveTextContent(/Hide sandbox\/QA organizations/i);
  });
});
