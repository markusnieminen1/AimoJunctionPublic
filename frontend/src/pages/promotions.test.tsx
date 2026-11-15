// M11: Management Promotions panel on /account (human-only CRUD, AI agent read-only).
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { endpoints } from "../api/endpoints";
import { clearSession, setSession } from "../auth/session";
import { resetMockClock } from "../mocks/clock";
import { SEED_AGENT_SECRETS, SEED_COMPANY } from "../mocks/data";
import { db, resetMockDb } from "../mocks/db";
import { buildDealsResponse } from "../mocks/m9";
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

describe("Management Promotions panel (/account)", () => {
  it("lists seeded promotions with human-only badge, toggles active state, creates and deletes a rule", async () => {
    const user = userEvent.setup();
    renderAt("/account");

    const panel = await screen.findByTestId("promotions-panel");
    expect(within(panel).getByText("Management promotions")).toBeInTheDocument();
    expect(within(panel).getByText("Human management only · AI agents read-only")).toBeInTheDocument();

    const firstRow = await within(panel).findByTestId("promotion-row-prm_valio_sale");
    expect(firstRow).toHaveTextContent("Prefer Valio on-sale items");
    expect(firstRow).toHaveTextContent("Active");

    // Pause rule
    await user.click(within(firstRow).getByRole("button", { name: "Pause" }));
    await waitFor(() => expect(within(panel).getByTestId("promotion-row-prm_valio_sale")).toHaveTextContent("Paused"));

    // Create a new rule
    await user.click(within(panel).getByRole("button", { name: "New promotion rule" }));
    const form = await within(panel).findByTestId("promotion-create-form");
    await user.type(within(form).getByLabelText("Rule title"), "Autumn berry desserts");
    await user.clear(within(form).getByLabelText("Boost weight"));
    await user.type(within(form).getByLabelText("Boost weight"), "2.0");
    await user.click(within(form).getByRole("button", { name: "Create promotion" }));

    await waitFor(() => expect(within(panel).getByText("Autumn berry desserts")).toBeInTheDocument());

    // Delete the newly created rule
    const delBtn = within(panel).getByRole("button", { name: "Delete Autumn berry desserts" });
    await user.click(delBtn);
    await waitFor(() => expect(within(panel).queryByText("Autumn berry desserts")).not.toBeInTheDocument());
  });

  it("renders read-only view when signed in as an AI agent", async () => {
    const agent = db.agents.find((a) => a.id === "agt_kitchen01")!;
    const tok = await endpoints.agentToken({ agent_id: agent.id, agent_secret: SEED_AGENT_SECRETS.agt_kitchen01 });
    setSession({
      token: tok.token,
      principal: { id: agent.id, name: agent.name, kind: "agent", company_id: agent.company_id },
      company: SEED_COMPANY,
    });
    renderAt("/account");
    const panel = await screen.findByTestId("promotions-panel");
    expect(await within(panel).findByText(/You are signed in as an agent/)).toBeInTheDocument();
    expect(within(panel).queryByRole("button", { name: "New promotion rule" })).not.toBeInTheDocument();
    expect(within(panel).queryByRole("button", { name: "Pause" })).not.toBeInTheDocument();
  });

  it("blocks creating a blank-criterion promotion and ignores criteria-less rules in mock deals", async () => {
    const user = userEvent.setup();
    renderAt("/account");

    const panel = await screen.findByTestId("promotions-panel");
    await user.click(within(panel).getByRole("button", { name: "New promotion rule" }));
    const form = await within(panel).findByTestId("promotion-create-form");
    await user.type(within(form).getByLabelText("Rule title"), "Empty filter rule");
    await user.clear(within(form).getByLabelText(/^Brand filter/));
    await user.click(within(form).getByLabelText("Only boost items currently on sale (listPrice > catalogPrice)"));
    await user.click(within(form).getByRole("button", { name: "Create promotion" }));

    expect(await within(form).findByTestId("promotion-criterion-error")).toBeInTheDocument();
    expect(within(panel).queryByText("Empty filter rule")).not.toBeInTheDocument();

    // Backend parity: rules without any criterion do not match every product in deals evaluation.
    db.promotions = [
      {
        id: "prm_blank",
        company_id: SEED_COMPANY.id,
        title: "Blank rule",
        description: null,
        brand_filter: null,
        category_prefix: null,
        boost_weight: 2,
        prefer_on_sale: false,
        active: true,
        created_at: new Date().toISOString(),
        created_by_user_id: "usr_demo",
      },
    ];
    const deals = buildDealsResponse({ id: "usr_demo", name: "Anna Korhonen", kind: "user", company_id: SEED_COMPANY.id }, {}, Date.now());
    expect(deals.items.every((i) => !i.promotion_reasons?.includes("Blank rule"))).toBe(true);
  });
});
