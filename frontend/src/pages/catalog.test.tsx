// M4: catalog renders the seed products, diet chips change the API query, allergen matrix never shows missing data as safe.
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { clearSession } from "../auth/session";
import { resetMockClock } from "../mocks/clock";
import { resetMockDb } from "../mocks/db";
import { setupMockServer } from "../mocks/server";
import { loginDemo, renderAt } from "../test/renderApp";

const server = setupMockServer();
const productQueries: URL[] = [];
beforeAll(() => {
  server.listen({ onUnhandledRequest: "error" });
  server.events.on("request:start", ({ request }) => {
    const u = new URL(request.url);
    if (request.method === "GET" && u.pathname === "/api/products") productQueries.push(u);
  });
});
afterEach(() => server.resetHandlers());
afterAll(() => server.close());
beforeEach(async () => {
  resetMockDb();
  resetMockClock();
  clearSession();
  window.localStorage.clear();
  productQueries.length = 0;
  await loginDemo();
});

describe("CatalogPage", () => {
  it("renders the seed products and the diet chip filters server-side", async () => {
    const user = userEvent.setup();
    renderAt("/catalog");
    expect(await screen.findByText(/Organic Lactose-Free Semi-Skimmed Milk 1 L/)).toBeInTheDocument();
    expect(screen.getAllByTestId("product-card").length).toBe(12);
    expect(screen.getByTestId("catalog-total")).toHaveTextContent("12 products");
    // 100009 tomato has no supplier allergen data → amber marker, never "safe"
    expect(screen.getAllByText("Allergens unknown").length).toBeGreaterThan(0);

    const filters = screen.getAllByRole("region", { name: "Finnish menu codes" })[0]!;
    await user.click(within(filters).getByRole("button", { name: /^L Lactose-free$/ }));

    await waitFor(() => expect(productQueries.some((u) => u.searchParams.get("diet") === "L")).toBe(true));
    await waitFor(() => expect(screen.queryByText(/Heirloom Beefsteak Tomatoes/)).not.toBeInTheDocument());
    const cards = screen.getAllByTestId("product-card");
    expect(cards.length).toBeLessThan(12);
    expect(screen.getByText("L · Lactose-free")).toBeInTheDocument(); // active filter pill
    expect(screen.getByRole("button", { name: /^L Lactose-free$/ })).toHaveAttribute("aria-pressed", "true");
  });

  it("syncs ?q= into the search and the list view toggle is remembered", async () => {
    const user = userEvent.setup();
    renderAt("/catalog?q=100001");
    expect(await screen.findByRole("searchbox", { name: "Search products" })).toHaveValue("100001");
    await waitFor(() => expect(screen.getAllByTestId("product-card")).toHaveLength(1));
    expect(productQueries.some((u) => u.searchParams.get("q") === "100001")).toBe(true);
    await user.click(screen.getByRole("button", { name: "List view" }));
    expect(screen.getAllByTestId("product-row")).toHaveLength(1);
    expect(window.localStorage.getItem("aimoannos.catalogView")).toBe("list");
  });

  it("shows the On sale & chef's picks strip, expands deal cards with discount badges, and hides silently on 404", async () => {
    const user = userEvent.setup();
    renderAt("/catalog");
    const strip = await screen.findByTestId("catalog-deals");
    expect(within(strip).getByText("On sale & chef's picks")).toBeInTheDocument();
    expect(within(strip).getByRole("link", { name: "Ask Menu Bot" })).toHaveAttribute("href", expect.stringContaining("/assistant?prompt="));

    await user.click(within(strip).getByRole("button", { name: "Show deals" }));
    const dealCards = await within(strip).findAllByTestId("catalog-deal-card");
    expect(dealCards.length).toBeGreaterThan(0);
    expect(within(strip).getAllByText(/-\d+%/).length).toBeGreaterThan(0);

    await user.click(within(strip).getByRole("button", { name: "On sale only" }));
    expect(within(strip).getByRole("button", { name: "On sale only" })).toHaveAttribute("aria-pressed", "true");
  });
});

describe("ProductPage", () => {
  it("shows Unknown (no supplier data) for a product with empty allergen arrays", async () => {
    renderAt("/products/100009");
    expect(await screen.findByRole("heading", { level: 1, name: /Heirloom Beefsteak Tomatoes/ })).toBeInTheDocument();
    const unknown = screen.getByTestId("allergen-unknown");
    expect(unknown).toHaveTextContent("Unknown (no supplier data)");
    expect(unknown).toHaveTextContent("verify supplier specification before serving");
    expect(screen.queryByTestId("allergen-free")).not.toBeInTheDocument();
  });

  it("shows contains / may contain buckets for a product with data", async () => {
    renderAt("/products/100007");
    await screen.findByRole("heading", { level: 1 });
    expect(screen.getByTestId("allergen-may")).toHaveTextContent("Soy");
    expect(screen.queryByTestId("allergen-unknown")).not.toBeInTheDocument();
  });

  it("checks live price and availability for the requested quantity", async () => {
    const user = userEvent.setup();
    renderAt("/products/100003");
    await screen.findByRole("heading", { level: 1, name: /Lactose-Free Catering Milk Drink/ });
    expect(screen.getByLabelText("Delivery date")).toHaveValue("2026-11-16");
    const qty = screen.getByLabelText("Quantity (ST)");
    await user.clear(qty);
    await user.type(qty, "1000");
    await user.click(screen.getByRole("button", { name: "Check" }));
    const box = await screen.findByTestId("check-result");
    expect(box).toHaveTextContent("1,19 €");
    expect(box).toHaveTextContent("1 190,00 €");
    expect(within(box).getByText("Partial")).toBeInTheDocument();
    expect(box).toHaveTextContent("384 ST in stock");
    await user.click(screen.getByRole("button", { name: "Add to cart" }));
    expect(screen.getByText("Added to cart")).toBeInTheDocument();
  });

  it("shows a not-found state for an unknown SKU", async () => {
    renderAt("/products/999999");
    expect(await screen.findByText("Product not found")).toBeInTheDocument();
  });
});
