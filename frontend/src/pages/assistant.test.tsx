// M8: /assistant chat: suggested chips, every action type, clarification answers, inline 503 with retry, sessionStorage.
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { endpoints } from "../api/endpoints";
import type { AssistantChatRequest } from "../api/types";
import { ASSISTANT_STORAGE_KEY, resetChatStoreForTests } from "../assistant/chatStore";
import { clearSession } from "../auth/session";
import { resetMockClock } from "../mocks/clock";
import { resetMockDb } from "../mocks/db";
import { buildMenuPlanResponse } from "../mocks/m9";
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
  resetChatStoreForTests();
  await loginDemo();
});

const SERVER_TIME = { "X-Server-Time": "2026-11-14T10:00:00+02:00" };

/** Record chat request bodies and fall through to the scripted mock assistant. */
function recordChat() {
  const bodies: AssistantChatRequest[] = [];
  server.use(
    http.post("*/api/assistant/chat", async ({ request }) => {
      bodies.push((await request.clone().json()) as AssistantChatRequest);
      return undefined;
    }),
  );
  return bodies;
}

function replyWith(actions: unknown[], reply = "Here you go.") {
  server.use(http.post("*/api/assistant/chat", () => HttpResponse.json({ conversation_id: "conv_TEST", reply, actions }, { headers: SERVER_TIME })));
}

async function ask(user: ReturnType<typeof userEvent.setup>, text: string) {
  const box = await screen.findByLabelText(/Ask for products/);
  await user.type(box, text);
  await user.keyboard("{Enter}");
}

describe("AssistantPage", () => {
  it("a suggested chip sends the message and renders products + clarification from the reply", async () => {
    const user = userEvent.setup();
    const bodies = recordChat();
    renderAt("/assistant");
    await user.click(await screen.findByRole("button", { name: "1000 litres of lactose-free milk by Monday" }));
    expect(await screen.findByTestId("user-message")).toHaveTextContent("1000 litres of lactose-free milk by Monday");
    const results = await screen.findByTestId("action-search_results");
    expect(within(results).getAllByTestId("assistant-product").length).toBe(4);
    expect(within(results).getAllByRole("link", { name: /Organic Lactose-Free/ })[0]).toHaveAttribute("href", expect.stringMatching(/^\/products\//));
    expect(screen.getByTestId("action-clarification")).toBeInTheDocument();
    expect(screen.getAllByText("AI assistant · decisions by policy engine").length).toBe(1);
    expect(bodies).toHaveLength(1);
    expect(bodies[0]!.conversation_id).toBeUndefined();
    expect(bodies[0]!.message).toBe("1000 litres of lactose-free milk by Monday");
  });

  it("clicking a clarification option sends that option with the conversation_id and disables the others", async () => {
    const user = userEvent.setup();
    const bodies = recordChat();
    renderAt("/assistant");
    await ask(user, "1000 litres of lactose-free milk by Monday");
    const clar = await screen.findByTestId("action-clarification");
    const chilled = within(clar).getByRole("button", { name: /Chilled · 100001/ });
    await user.click(chilled);
    await screen.findByTestId("action-quote");
    expect(bodies[1]!.message).toBe("Chilled · 100001 (+4..+8 °C)");
    expect(bodies[1]!.conversation_id).toMatch(/^conv_/);
    expect(within(clar).getByRole("button", { name: /Chilled · 100001/ })).toHaveAttribute("aria-pressed", "true");
    expect(within(clar).getByRole("button", { name: /UHT ambient/ })).toBeDisabled();
    // The quote card is the shared QuotePanel (user principal → AUTO, Place order).
    expect(within(screen.getByTestId("action-quote")).getByRole("button", { name: /Place order/ })).toBeEnabled();
  });

  it("an order created by chat ('place the order') turns its quote card into the order result", async () => {
    const user = userEvent.setup();
    renderAt("/assistant");
    await ask(user, "1000 litres of lactose-free milk by Monday");
    await user.click(within(await screen.findByTestId("action-clarification")).getByRole("button", { name: /Chilled · 100001/ }));
    const quoteCard = await screen.findByTestId("action-quote");
    expect(within(quoteCard).getByRole("button", { name: /Place order/ })).toBeInTheDocument();
    await ask(user, "Place the order");
    await screen.findByTestId("action-order");
    await waitFor(() => expect(within(screen.getByTestId("action-quote")).queryByRole("button", { name: /Place order/ })).toBeNull());
    expect(within(screen.getByTestId("action-quote")).getByText("An order was created from this quote.")).toBeInTheDocument();
  });

  it("Shift+Enter adds a newline; Enter sends", async () => {
    const user = userEvent.setup();
    const bodies = recordChat();
    renderAt("/assistant");
    const box = await screen.findByLabelText(/Ask for products/);
    await user.type(box, "milk{Shift>}{Enter}{/Shift}please");
    expect(box).toHaveValue("milk\nplease");
    expect(bodies).toHaveLength(0);
    await user.keyboard("{Enter}");
    await screen.findByTestId("assistant-message");
    expect(bodies[0]!.message).toBe("milk\nplease");
    expect(box).toHaveValue("");
  });

  it("renders a quote action with QuotePanel and places the order from the chat", async () => {
    const user = userEvent.setup();
    const q = await endpoints.createQuote({ requested_delivery_date: "2026-11-16", lines: [{ product_id: "100001", quantity: 100 }] });
    replyWith([{ type: "quote", quote: q }], "Quote ready.");
    renderAt("/assistant");
    await ask(user, "quote please");
    const card = await screen.findByTestId("action-quote");
    expect(within(card).getByTestId("quote-panel")).toBeInTheDocument();
    expect(within(card).getAllByText("101,00 €").length).toBeGreaterThan(0);
    await user.click(within(card).getByRole("button", { name: /Place order/ }));
    expect(await within(screen.getByTestId("action-quote")).findByTestId("order-result")).toBeInTheDocument();
    expect(within(screen.getByTestId("action-quote")).queryByRole("button", { name: /Place order/ })).toBeNull();
  });

  it("a chat quote that was ordered elsewhere (e.g. on /cart) shows as used instead of offering Place order", async () => {
    const user = userEvent.setup();
    const q = await endpoints.createQuote({ requested_delivery_date: "2026-11-16", lines: [{ product_id: "100001", quantity: 100 }] });
    await endpoints.createOrder({ quote_id: q.id }, "idem-elsewhere-1");
    replyWith([{ type: "quote", quote: q }], "Quote ready."); // snapshot still says OPEN
    renderAt("/assistant");
    await ask(user, "quote please");
    const card = await screen.findByTestId("action-quote");
    expect(await within(card).findByTestId("quote-used")).toHaveTextContent("This quote was already used");
    expect(within(card).getByRole("button", { name: /Place order/ })).toBeDisabled();
  });

  it("renders an order action; PENDING_APPROVAL says it waits for a human and links to /approvals", async () => {
    const user = userEvent.setup();
    const { items } = await endpoints.listOrders({ status: "PENDING_APPROVAL" });
    replyWith([{ type: "order", order: items[0] }], "Waiting for approval.");
    renderAt("/assistant");
    await ask(user, "status?");
    const card = await screen.findByTestId("action-order");
    expect(within(card).getByTestId("waiting-approval")).toHaveTextContent("Waiting for human approval");
    expect(within(card).getByRole("link", { name: "Open approvals" })).toHaveAttribute("href", "/approvals");
    expect(within(card).getByTestId("order-result")).toBeInTheDocument();
  });

  it("renders search_results with + Add and an unknown action type as a neutral card without crashing", async () => {
    const user = userEvent.setup();
    const p = await endpoints.getProduct("100002");
    replyWith([{ type: "search_results", products: [p] }, { type: "recipe_plan", steps: [] }, null]);
    renderAt("/assistant");
    await ask(user, "anything");
    const results = await screen.findByTestId("action-search_results");
    expect(within(results).getByText("100002")).toBeInTheDocument();
    expect(within(results).getByRole("button", { name: new RegExp(`Add ${p.name}`) })).toBeInTheDocument();
    const unsupported = screen.getAllByTestId("action-unsupported");
    expect(unsupported).toHaveLength(2);
    expect(unsupported[0]).toHaveTextContent("recipe_plan");
  });

  it("503 LLM_UNAVAILABLE shows inline with code + request_id, and Retry resends the same message", async () => {
    const user = userEvent.setup();
    let calls = 0;
    server.use(
      http.post("*/api/assistant/chat", () => {
        calls += 1;
        if (calls > 1) return undefined;
        return HttpResponse.json(
          { error: { code: "LLM_UNAVAILABLE", message: "Assistant is over its hourly budget", details: {} }, request_id: "req_LLM503" },
          { status: 503, headers: SERVER_TIME },
        );
      }),
    );
    renderAt("/assistant");
    await ask(user, "1000 litres of lactose-free milk by Monday");
    const err = await screen.findByTestId("chat-error");
    expect(err).toHaveTextContent("The assistant is unavailable right now");
    expect(err).toHaveTextContent("LLM_UNAVAILABLE");
    expect(err).toHaveTextContent("req_LLM503");
    await user.click(within(err).getByRole("button", { name: "Try again" }));
    await screen.findByTestId("action-clarification");
    expect(screen.queryByTestId("chat-error")).toBeNull();
    expect(screen.getAllByTestId("user-message")).toHaveLength(1);
    expect(calls).toBe(2);
  });

  it("keeps the conversation in sessionStorage across remounts; New conversation clears it", async () => {
    const user = userEvent.setup();
    const first = renderAt("/assistant");
    await ask(user, "1000 litres of lactose-free milk by Monday");
    await screen.findByTestId("action-clarification");
    expect(window.sessionStorage.getItem(ASSISTANT_STORAGE_KEY)).toContain("conv_");
    first.unmount();
    resetChatStoreForTests(); // simulate a fresh page: state must come back from sessionStorage
    renderAt("/assistant");
    expect(await screen.findByTestId("action-clarification")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "New conversation" }));
    await waitFor(() => expect(screen.queryByTestId("assistant-message")).toBeNull());
    expect(window.sessionStorage.getItem(ASSISTANT_STORAGE_KEY)).toBeNull();
    expect(screen.getByTestId("suggestions")).toBeInTheDocument();
  });

  it("sends a prompt handed over from the dashboard Ask band (?prompt=) exactly once", async () => {
    const sent: string[] = [];
    server.use(
      http.post("*/api/assistant/chat", async ({ request }) => {
        sent.push(((await request.clone().json()) as AssistantChatRequest).message);
        return undefined;
      }),
    );
    renderAt(`/assistant?prompt=${encodeURIComponent("1000 litres of lactose-free milk by Monday")}`);
    expect(await screen.findByTestId("user-message")).toHaveTextContent("1000 litres of lactose-free milk by Monday");
    await screen.findByTestId("action-clarification");
    expect(sent).toEqual(["1000 litres of lactose-free milk by Monday"]);
  });

  it("renders deals action card with discount badge, strikethrough list price, Management Pick, and Add to cart", async () => {
    const user = userEvent.setup();
    renderAt("/assistant");
    await ask(user, "What dairy products are on sale right now?");
    const card = await screen.findByTestId("action-deals");
    expect(card).toHaveTextContent(/on-sale & promoted/i);
    expect(within(card).getAllByText(/-\d+%/).length).toBeGreaterThan(0);
    expect(within(card).getAllByText(/Management Pick|Promoted/i).length).toBeGreaterThan(0);
    const addBtns = within(card).getAllByRole("button", { name: /^Add / });
    await user.click(addBtns[0]!);
    await waitFor(() => expect(screen.getAllByRole("link", { name: /^Cart: 1 line/ }).length).toBeGreaterThan(0));
  });

  it("renders menu_plan action card with Valio recipes, savings, policy preview, reasoning steps, and Add menu ingredients to cart", async () => {
    const user = userEvent.setup();
    renderAt("/assistant");
    await ask(user, "Build a lactose-free lunch menu from on-sale Valio items");
    const card = await screen.findByTestId("action-menu_plan");
    expect(card).toHaveTextContent(/Planned .* menu/);
    expect(card.querySelector('[data-outcome="AUTO"]')).toBeInTheDocument();
    expect(within(card).getByText(/Save .* vs list price/)).toBeInTheDocument();

    // Expand reasoning steps ("Why this menu was chosen")
    await user.click(within(card).getByText(/Why this menu was chosen/));
    expect(within(card).getByTestId("menu-plan-reasoning")).toBeInTheDocument();

    // Add all menu ingredients to cart
    await user.click(within(card).getByRole("button", { name: "Add menu ingredients to cart" }));
    expect(await within(card).findByRole("link", { name: "Open cart & quote" })).toHaveAttribute("href", "/cart");
    await waitFor(() => expect(screen.getAllByRole("link", { name: /^Cart: [1-9]\d* line/ }).length).toBeGreaterThan(0));
  });

  it("shows per-line CO2 basis and partial coverage notice when a menu ingredient lacks CO2 data", async () => {
    const basePlan = buildMenuPlanResponse(
      { id: "usr_demo", name: "Anna Korhonen", kind: "user", company_id: "cmp_aurora" },
      { meal_type: "lunch", pax: 20, diet: "L", prefer_on_sale: true, include_valio_recipes: true },
      "req_co2_partial",
      Date.now(),
    );
    const baseItems = basePlan.items ?? [];
    const items = baseItems.map((item, idx) =>
      idx === baseItems.length - 1
        ? { ...item, quote_line: { ...item.quote_line, co2_kg: null, co2_basis: null } }
        : { ...item, quote_line: { ...item.quote_line, co2_kg: 1.5, co2_basis: "Category dairy avg" } },
    );
    server.use(
      http.post("/api/assistant/chat", () =>
        HttpResponse.json({
          reply: "Planned menu with partial CO2 data.",
          conversation_id: "cnv_co2",
          actions: [{ type: "menu_plan", menu_plan: { ...basePlan, items, estimated_co2_kg: 3.0 } }],
        }),
      ),
    );

    const user = userEvent.setup();
    renderAt("/assistant");
    await ask(user, "Check partial CO2 basis");

    const card = await screen.findByTestId("action-menu_plan");
    expect(within(card).getByTestId("menu-plan-co2-partial")).toHaveTextContent(/CO₂e estimate covers 2 of 3/i);
    expect(within(card).getByText("CO₂e unavailable")).toBeInTheDocument();
    const lineCo2 = within(card).getAllByText(/~1,5 kg CO₂e/)[0]!;
    await user.hover(lineCo2);
    expect(await screen.findByRole("tooltip")).toHaveTextContent(/Category dairy avg/);
  });

  it("runs the judge phone over-order starter showing A2A negotiation, R-RECIPE-SANITY warning, and PENDING_APPROVAL order", async () => {
    const user = userEvent.setup();
    renderAt("/assistant");
    const starters = await screen.findByTestId("judge-demo-starters");
    await user.click(within(starters).getByRole("button", { name: "Check over-sized milk order" }));

    const a2a = await screen.findByTestId("action-a2a_negotiation");
    expect(a2a).toHaveTextContent(/Pax: 60/i);
    expect(a2a).toHaveTextContent("policy-engine");

    const sanity = await screen.findByTestId("recipe-sanity-warning");
    expect(sanity).toHaveTextContent("R-RECIPE-SANITY");
    expect(sanity).toHaveTextContent(/120 L/);
    expect(sanity).toHaveTextContent(/18 L/);

    expect(await screen.findByTestId("waiting-approval")).toBeInTheDocument();
  });

  it("renders **bold**, `code`, and bullet lists cleanly in assistant chat replies", async () => {
    const user = userEvent.setup();
    server.use(
      http.post("*/api/assistant/chat", () =>
        HttpResponse.json({
          reply: "Here is your **Valio Aimo** summary:\n- Checked `100001` milk\n- **All lines** on time",
          actions: [],
        }),
      ),
    );
    renderAt("/assistant");
    await ask(user, "Check formatting");

    const msg = await screen.findByTestId("assistant-message");
    expect(msg.querySelectorAll("strong").length).toBe(2);
    expect(msg.querySelector("strong")).toHaveTextContent("Valio Aimo");
    expect(msg.querySelector("code")).toHaveTextContent("100001");
    expect(msg.querySelectorAll("li").length).toBe(2);
    expect(msg).not.toHaveTextContent("**");
  });

  it("moves single-node composer below chat stream via CSS flex order without remounting", async () => {
    const user = userEvent.setup();
    renderAt("/assistant");
    const composerRow = await screen.findByTestId("composer-row");
    const textarea = await screen.findByLabelText(/Ask for products/);
    const stream = await screen.findByTestId("chat-stream");
    expect(composerRow.className).toContain("order-2");
    expect(composerRow.className).not.toContain("order-4");
    expect(stream.parentElement?.className).toContain("order-4");

    await ask(user, "Check our top dairy campaigns");
    expect(await screen.findByTestId("user-message")).toHaveTextContent("Check our top dairy campaigns");
    expect(screen.getByTestId("composer-row")).toBe(composerRow);
    expect(screen.getByLabelText(/Ask for products/)).toBe(textarea);
    expect(composerRow.className).toContain("order-4");
    expect(composerRow.className).toContain("sticky");
    expect(stream.parentElement?.className).toContain("order-3");
  });

  it("auto-executes at 50% easy mode assurance threshold, shows assurance badge + 1-tap undo, and shows processing warning", async () => {
    const user = userEvent.setup();
    await endpoints.updateAssistantSettings({ order_assurance_threshold: 50 });
    renderAt("/assistant");

    const thresholdPill = await screen.findByTestId("assistant-threshold-pill");
    expect(thresholdPill).toHaveTextContent(/50%/);
    expect(thresholdPill).toHaveAttribute("href", "/agents");
    await ask(user, "20 liters of milk");

    const badge = await screen.findByTestId("assurance-badge");
    expect(badge).toHaveTextContent(/Assurance 62%/i);
    expect(badge).toHaveTextContent(/threshold 50%/i);
    expect(badge).toHaveTextContent(/Auto-ordered/i);

    const orderCard = await screen.findByTestId("action-order");
    expect(await within(orderCard).findByText("Placed", {}, { timeout: 6000 })).toBeInTheDocument();
    const undoBtn = await within(orderCard).findByTestId("undo-order-btn");
    await user.click(undoBtn);
    await waitFor(() => {
      expect(within(orderCard).getByText("Cancelled")).toBeInTheDocument();
    });

    server.use(
      http.post("*/api/assistant/chat", () =>
        HttpResponse.json({
          reply: "Fallback assistant summary.",
          actions: [],
          fallback_used: true,
          processing_warnings: ["LLM gateway slow; template fallback used"],
        }),
      ),
    );
    await ask(user, "Status check");
    expect(await screen.findByTestId("assistant-processing-warning")).toHaveTextContent(
      /LLM gateway slow; template fallback used/,
    );
  });
});
