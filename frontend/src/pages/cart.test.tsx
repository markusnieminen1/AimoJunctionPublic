// M5: cart → quote (PARTIAL + substitutes + swap), place order with a stable Idempotency-Key, inline quote/order errors.
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { delay, http, HttpResponse } from "msw";
import { endpoints } from "../api/endpoints";
import type { Product } from "../api/types";
import { clearSession } from "../auth/session";
import { resetMockClock } from "../mocks/clock";
import { SYNTHETIC_PRODUCTS } from "../mocks/data";
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

const ALL: Product[] = SYNTHETIC_PRODUCTS;

function seedCart(lines: { id: string; qty: number }[]) {
  const state = {
    lines: lines.map(({ id, qty }) => ({ product_id: id, quantity: qty, product: ALL.find((p) => p.id === id)! })),
    requested_delivery_date: null,
    lastQuote: null,
  };
  window.localStorage.setItem("aimoannos.cart", JSON.stringify(state));
}

const envelope = (code: string, message: string, request_id: string, details: Record<string, unknown> = {}) => ({ error: { code, message, details }, request_id });

async function quoteCart(user: ReturnType<typeof userEvent.setup>) {
  renderAt("/cart");
  await user.click(await screen.findByRole("button", { name: "Get quote" }));
  return screen.findByTestId("quote-panel");
}

describe("CartPage", () => {
  it("shows the base-unit helper from the product's units", async () => {
    seedCart([{ id: "100008", qty: 5 }]);
    renderAt("/cart");
    expect(await screen.findByTestId("base-hint")).toHaveTextContent("5 KI = 10 ST");
  });

  it("quotes 1,000 × 100003 as PARTIAL, offers diet-safe substitutes and swaps one in", async () => {
    const user = userEvent.setup();
    seedCart([{ id: "100003", qty: 1000 }]);
    const panel = await quoteCart(user);
    expect(within(panel).getByText("Partial")).toBeInTheDocument();
    expect(within(panel).getAllByText("1 190,00 €").length).toBeGreaterThan(0);
    expect(within(panel).getByText("This order will be placed directly")).toBeInTheDocument();
    expect(within(panel).getByText("R-USER-DIRECT")).toBeInTheDocument();

    // The substitutes section sits next to the quote (wide column), not inside the quote panel.
    const subs = screen.getByRole("region", { name: "Supply risk detected" });
    await user.click(within(subs).getByRole("button", { name: "Find diet-safe substitutes" }));
    const cards = await within(subs).findAllByTestId("substitute-card");
    const chilled = cards.find((c) => within(c).queryByText("100001"));
    const uht = cards.find((c) => within(c).queryByText("100002"));
    expect(chilled).toBeTruthy();
    expect(uht).toBeTruthy();
    expect(uht!).toHaveTextContent(/UHT|Ambient/);

    await user.click(within(chilled!).getByRole("button", { name: /Swap in cart/ }));
    // the cart now holds 100001 and a fresh quote has no PARTIAL line
    await waitFor(() => expect(within(screen.getByTestId("quote-panel")).getByText("100001")).toBeInTheDocument());
    await waitFor(() => expect(within(screen.getByTestId("quote-panel")).queryByText("Partial")).not.toBeInTheDocument());
    expect(screen.getAllByTestId("cart-line")).toHaveLength(1);
    expect(within(screen.getByTestId("quote-panel")).getAllByText("1 010,00 €").length).toBeGreaterThan(0);
  });

  it("reuses the same Idempotency-Key when the order is retried, then shows 'Confirming…' without a retry", async () => {
    const keys: string[] = [];
    server.use(
      http.post("*/api/orders", async ({ request }) => {
        keys.push(request.headers.get("Idempotency-Key") ?? "");
        if (keys.length === 1) {
          await delay(150);
          return HttpResponse.json(envelope("UPSTREAM_UNAVAILABLE", "Valio Aimo API is down", "req_test_upstream"), { status: 503 });
        }
        return undefined; // fall through to the mock API
      }),
    );
    const user = userEvent.setup();
    seedCart([{ id: "100001", qty: 10 }]);
    const panel = await quoteCart(user);

    // double click while the first request is in flight: only one request
    await user.dblClick(within(panel).getByRole("button", { name: "Place order" }));
    expect(await within(panel).findByText("Valio Aimo is not reachable right now")).toBeInTheDocument();
    expect(within(panel).getByText("UPSTREAM_UNAVAILABLE")).toBeInTheDocument();
    expect(within(panel).getByText("req_test_upstream")).toBeInTheDocument();
    expect(keys).toHaveLength(1);

    await user.click(within(panel).getByRole("button", { name: "Try again" }));
    const result = await within(panel).findByTestId("order-result");
    expect(keys).toHaveLength(2);
    expect(keys[0]).toBeTruthy();
    expect(keys[1]).toBe(keys[0]);
    expect(result).toHaveTextContent("Confirming with Valio Aimo…");
    expect(within(panel).queryByRole("button", { name: /Try again|Place order/ })).not.toBeInTheDocument();
    expect(screen.getByText("Order sent. Your cart has been emptied.")).toBeInTheDocument();
  });

  it("QUOTE_EXPIRED shows the error with code + request_id and a re-quote button", async () => {
    server.use(http.post("*/api/orders", () => HttpResponse.json(envelope("QUOTE_EXPIRED", "The quote has expired.", "req_test_expired"), { status: 409 }), { once: true }));
    const user = userEvent.setup();
    seedCart([{ id: "100001", qty: 10 }]);
    const panel = await quoteCart(user);
    const firstId = within(panel).getByText(/^qt_/).textContent;

    await user.click(within(panel).getByRole("button", { name: "Place order" }));
    expect(await within(panel).findByText("This quote has expired")).toBeInTheDocument();
    expect(within(panel).getByText("QUOTE_EXPIRED")).toBeInTheDocument();
    expect(within(panel).getByText("req_test_expired")).toBeInTheDocument();
    expect(within(panel).getByRole("button", { name: "Place order" })).toBeDisabled();

    await user.click(within(panel).getByRole("button", { name: "Get a new quote" }));
    await waitFor(() => expect(within(screen.getByTestId("quote-panel")).getByText(/^qt_/).textContent).not.toBe(firstId));
    const fresh = screen.getByTestId("quote-panel");
    expect(within(fresh).queryByText("This quote has expired")).not.toBeInTheDocument();
    expect(within(fresh).getByRole("button", { name: "Place order" })).toBeEnabled();
  });

  it("POLICY_BLOCKED shows the fired rules (R-HARD-DIET)", async () => {
    const decision = { outcome: "BLOCKED", rules: [{ rule_id: "R-HARD-DIET", outcome: "BLOCKED", message: "Product 100001 is not lactose-free (L) as the ward diet requires" }] };
    server.use(http.post("*/api/orders", () => HttpResponse.json(envelope("POLICY_BLOCKED", "Blocked by company policy: R-HARD-DIET", "req_test_blocked", decision), { status: 403 }), { once: true }));
    const user = userEvent.setup();
    seedCart([{ id: "100001", qty: 10 }]);
    const panel = await quoteCart(user);
    await user.click(within(panel).getByRole("button", { name: "Place order" }));
    expect(await within(panel).findByText("Order blocked by company policy")).toBeInTheDocument();
    expect(within(panel).getByText("R-HARD-DIET")).toBeInTheDocument();
    expect(within(panel).getByText("req_test_blocked")).toBeInTheDocument();
  });

  it("QUOTE_STALE shows the fresh figures from the server and offers a re-quote", async () => {
    const user = userEvent.setup();
    seedCart([{ id: "100001", qty: 10 }]);
    const panel = await quoteCart(user);
    db.priceOverrides["100001"] = 1.05; // price changes after quoting
    await user.click(within(panel).getByRole("button", { name: "Place order" }));
    expect(await within(panel).findByText("Prices or availability changed")).toBeInTheDocument();
    expect(within(panel).getByText("10,50 €")).toBeInTheDocument(); // fresh line total from details.lines
    expect(within(panel).getByRole("button", { name: "Get a new quote" })).toBeInTheDocument();
  });

  it("marks the quote outdated when the cart changes", async () => {
    const user = userEvent.setup();
    seedCart([{ id: "100001", qty: 10 }]);
    const panel = await quoteCart(user);
    await user.click(screen.getByRole("button", { name: /Increase quantity/ }));
    expect(await within(panel).findByText(/Your cart changed since this quote/)).toBeInTheDocument();
    expect(within(panel).getByRole("button", { name: "Place order" })).toBeDisabled();
  });

  it("sends the cart's diet requirement with the quote; 100001 + M (milk-free) → R-HARD-DIET blocked preview", async () => {
    const bodies: unknown[] = [];
    server.use(
      http.post("*/api/quotes", async ({ request }) => {
        bodies.push(await request.clone().json());
        return undefined; // fall through to the mock API
      }),
    );
    const user = userEvent.setup();
    seedCart([{ id: "100001", qty: 10 }]);
    renderAt("/cart");
    const group = await screen.findByRole("group", { name: "Diet requirement" });
    await user.click(within(group).getByRole("button", { name: /^M · Milk-free/ }));
    expect(within(group).getByRole("button", { name: /^M · Milk-free/ })).toHaveAttribute("aria-pressed", "true");
    await user.click(screen.getByRole("button", { name: "Get quote" }));
    const panel = await screen.findByTestId("quote-panel");
    expect(bodies[0]).toMatchObject({ diet: "M", lines: [{ product_id: "100001", quantity: 10 }] });
    expect(within(panel).getByText("This order would be blocked by company policy")).toBeInTheDocument();
    expect(within(panel).getByText("R-HARD-DIET")).toBeInTheDocument();
    expect(within(panel).getByTestId("diet-requirement")).toHaveTextContent("Milk-free");
    expect(within(panel).getByRole("button", { name: "Blocked by policy" })).toBeDisabled();
    // the diet is persisted with the cart
    expect(JSON.parse(window.localStorage.getItem("aimoannos.cart")!).diet).toEqual(["M"]);

    // switching the diet to L (lactose-free) makes the quote outdated; a new quote passes
    await user.click(within(group).getByRole("button", { name: /^M · Milk-free/ }));
    await user.click(within(group).getByRole("button", { name: /^L · Lactose-free/ }));
    expect(await within(panel).findByText(/Your cart changed since this quote/)).toBeInTheDocument();
    await user.click(within(panel).getByRole("button", { name: "Get a new quote" }));
    await waitFor(() => expect(within(screen.getByTestId("quote-panel")).getByText("R-USER-DIRECT")).toBeInTheDocument());
    expect(bodies[1]).toMatchObject({ diet: "L" });
    expect(within(screen.getByTestId("quote-panel")).getByRole("button", { name: "Place order" })).toBeEnabled();
  });

  it("without a diet requirement no diet is sent", async () => {
    const bodies: Record<string, unknown>[] = [];
    server.use(
      http.post("*/api/quotes", async ({ request }) => {
        bodies.push((await request.clone().json()) as Record<string, unknown>);
        return undefined;
      }),
    );
    const user = userEvent.setup();
    seedCart([{ id: "100001", qty: 10 }]);
    const panel = await quoteCart(user);
    expect(bodies[0]).not.toHaveProperty("diet");
    expect(within(panel).queryByTestId("diet-requirement")).not.toBeInTheDocument();
  });

  it("optimizes cart for savings and applies selected swaps cleanly (plant-based swaps off by default)", async () => {
    const user = userEvent.setup();
    seedCart([{ id: "100002", qty: 10 }]); // €16.50 UHT milk
    renderAt("/cart");

    const toggle = (await screen.findByTestId("optimize-allow-plant-swaps")) as HTMLInputElement;
    expect(toggle.checked).toBe(false);

    await user.click(screen.getByRole("button", { name: "Save €" }));
    await user.click(screen.getByTestId("optimize-cart-btn"));

    const results = await screen.findByTestId("optimize-results");
    expect(within(results).getByTestId("optimize-swap-100002")).toHaveTextContent("100001");
    expect(within(results).getByTestId("optimize-eur-delta")).toHaveTextContent("−6,40 €");

    await user.click(within(results).getByTestId("optimize-apply-btn"));
    // Re-quotes automatically with 100001 at 10,10 €
    await waitFor(() => expect(screen.getAllByText("10,10 €").length).toBeGreaterThan(0));
  });

  it("withholds plant-based LOW_CARBON swaps unless allow_plant_based_swaps is explicitly enabled", async () => {
    const requests: Record<string, unknown>[] = [];
    server.use(
      http.post("*/api/quotes/optimize", async ({ request }) => {
        requests.push((await request.clone().json()) as Record<string, unknown>);
        return undefined;
      }),
    );

    const user = userEvent.setup();
    seedCart([{ id: "100001", qty: 10 }]);
    renderAt("/cart");

    await user.click(await screen.findByRole("button", { name: "Low CO₂e" }));
    await user.click(screen.getByTestId("optimize-cart-btn"));

    await screen.findByTestId("optimize-results");
    expect(requests[0]).toMatchObject({ objective: "LOW_CARBON", allow_plant_based_swaps: false });
    expect(screen.queryByTestId("optimize-swap-100001")).not.toBeInTheDocument();
    expect(screen.getByTestId("optimize-no-swaps")).toBeInTheDocument();

    // Opt in to plant-based swaps -> re-runs automatically with allow_plant_based_swaps=true
    await user.click(screen.getByTestId("optimize-allow-plant-swaps"));
    await waitFor(() => expect(requests.length).toBeGreaterThanOrEqual(2));
    expect(requests[1]).toMatchObject({ objective: "LOW_CARBON", allow_plant_based_swaps: true });
    expect(await screen.findByTestId("optimize-swap-100001")).toHaveTextContent("100005");
    expect(screen.getByTestId("optimize-co2-delta")).toHaveTextContent(/1\/1 comparable CO₂e lines/);
  });

  it("shows 1-tap Negotiate volume price button when allow_price_negotiation is enabled", async () => {
    const user = userEvent.setup();
    seedCart([{ id: "100001", qty: 20 }]);
    const first = renderAt("/cart");

    await screen.findByTestId("optimize-cart-btn");
    expect(screen.queryByTestId("negotiate-quote-btn")).not.toBeInTheDocument();
    expect(await screen.findByTestId("cart-negotiation-admin-link")).toHaveAttribute("href", "/admin");
    first.unmount();

    const firstDone = await endpoints.updateNegotiationSettings({ allow_price_negotiation: true });
    expect(firstDone.allow_price_negotiation).toBe(true);

    renderAt("/cart");
    const negBtn = await screen.findByTestId("negotiate-quote-btn");
    expect(screen.getByTestId("cart-negotiation-admin-link")).toHaveAttribute("href", "/admin");
    await user.click(negBtn);
    expect(await screen.findByTestId("negotiated-savings-badge")).toBeInTheDocument();
  });

  it("aligns cutoff/delayed line (100008) to last available delivery date and places order On time", async () => {
    const user = userEvent.setup();
    seedCart([{ id: "100008", qty: 5 }]);
    const panel = await quoteCart(user);

    await waitFor(() => {
      expect(screen.getByTestId("last-available-delivery-note")).toHaveTextContent(/last available item/i);
    });
    const eff = await within(panel).findByTestId("quote-effective-delivery");
    expect(eff).toHaveTextContent(/On time/i);

    await user.click(within(panel).getByRole("button", { name: "Place order" }));
    const res = await screen.findByTestId("order-result");
    expect(within(res).getByText(/Confirming|Order sent/i)).toBeInTheDocument();

    const ordersPage = await endpoints.listOrders();
    const placed = ordersPage.items[0]!;
    expect(placed.requested_delivery_date > "2026-11-16").toBe(true);
    expect(placed.lines[0]!.delivery_status).toBe("ON_TIME");
    expect(placed.lines[0]!.flags).not.toContain("LATE");
  });
});
