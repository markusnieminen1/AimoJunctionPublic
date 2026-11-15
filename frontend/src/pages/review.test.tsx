// Regression tests for the fresh-eyes review (see PROGRESS.md "Review findings").
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it } from "vitest";
import { act, render, renderHook, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClientProvider } from "@tanstack/react-query";
import { http, HttpResponse } from "msw";
import type { ReactNode } from "react";
import { endpoints } from "../api/endpoints";
import { clearIdempotencyKeys, useIdempotencyKey } from "../api/hooks";
import { createQueryClient } from "../api/queryClient";
import type { Product } from "../api/types";
import { AuthProvider, useAuth } from "../auth/AuthContext";
import { clearSession, getSession, setSession } from "../auth/session";
import { CartProvider, useCart } from "../cart/CartContext";
import { allergenBuckets } from "../domain/allergens";
import { productDiets } from "../domain/diets";
import { resetMockClock } from "../mocks/clock";
import { SEED_AGENT_SECRETS, SEED_COMPANY, SYNTHETIC_PRODUCTS } from "../mocks/data";
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
  clearIdempotencyKeys();
  window.localStorage.clear();
  window.sessionStorage.clear();
  await loginDemo();
});

const ALL: Product[] = SYNTHETIC_PRODUCTS;
const p100001 = ALL.find((p) => p.id === "100001")!;

function seedCart(lines: { id: string; qty: number }[]) {
  const state = {
    lines: lines.map(({ id, qty }) => ({ product_id: id, quantity: qty, product: ALL.find((p) => p.id === id)! })),
    requested_delivery_date: null,
    lastQuote: null,
  };
  window.localStorage.setItem("aimoannos.cart", JSON.stringify(state));
}

describe("useIdempotencyKey", () => {
  it("keeps the key for the same scope across remounts and changes it with the scope", () => {
    const first = renderHook(({ scope }) => useIdempotencyKey(scope), { initialProps: { scope: "place:qt_1:" } });
    const k1 = first.result.current.key;
    first.rerender({ scope: "place:qt_1:" });
    expect(first.result.current.key).toBe(k1);
    first.unmount();

    // e.g. the user navigated away from /cart and back, or the same quote is shown in a chat card
    const again = renderHook(() => useIdempotencyKey("place:qt_1:"));
    expect(again.result.current.key).toBe(k1);

    const other = renderHook(() => useIdempotencyKey("place:qt_1:deliver to back door"));
    expect(other.result.current.key).not.toBe(k1);

    act(() => again.result.current.rotate());
    expect(again.result.current.key).not.toBe(k1);

    clearIdempotencyKeys();
    const afterLogout = renderHook(() => useIdempotencyKey("place:qt_1:deliver to back door"));
    expect(afterLogout.result.current.key).not.toBe(other.result.current.key);
  });
});

describe("Cart is bound to the signed-in principal", () => {
  function wrapper({ children }: { children: ReactNode }) {
    return (
      <QueryClientProvider client={createQueryClient()}>
        <AuthProvider>
          <CartProvider>{children}</CartProvider>
        </AuthProvider>
      </QueryClientProvider>
    );
  }

  it("drops company A's cart when company B signs in on the same browser, and keeps it for A", () => {
    const a = getSession()!;
    const { result } = renderHook(() => useCart(), { wrapper });
    act(() => result.current.addLine(p100001, 5));
    act(() => result.current.setDiet(["M"]));
    expect(result.current.lines).toHaveLength(1);

    act(() => {
      clearSession();
      setSession({ token: "tok_b", principal: { ...a.principal, id: "usr_other_company", company_id: "cmp_other" }, company: { id: "cmp_other", name: "Other Oy" } });
    });
    expect(result.current.lines).toHaveLength(0);
    expect(result.current.diet).toEqual([]);
    expect(result.current.currentQuote).toBeNull();
    expect(JSON.parse(window.localStorage.getItem("aimoannos.cart")!).lines).toHaveLength(0);
  });

  it("adopts an unclaimed cart (saved before carts had owners)", () => {
    seedCart([{ id: "100001", qty: 3 }]);
    const { result } = renderHook(() => useCart(), { wrapper });
    expect(result.current.lines).toHaveLength(1);
    expect(JSON.parse(window.localStorage.getItem("aimoannos.cart")!).owner).toBe(getSession()!.principal.id);
  });
});

describe("Logout clears cached company data", () => {
  it("the query cache is empty after logout", async () => {
    const user = userEvent.setup();
    const qc = createQueryClient();
    render(
      <QueryClientProvider client={qc}>
        <AuthProvider>
          <CartProvider>
            <LogoutProbe />
          </CartProvider>
        </AuthProvider>
      </QueryClientProvider>,
    );
    qc.setQueryData(["orders", "list", {}], { items: [{ id: "ord_company_a" }], total: 1 });
    await user.click(screen.getByRole("button", { name: "logout" }));
    expect(qc.getQueryCache().getAll()).toHaveLength(0);
    expect(getSession()).toBeNull();
  });
});

function LogoutProbe() {
  const { logout } = useAuth();
  return (
    <button type="button" onClick={logout}>
      logout
    </button>
  );
}

describe("Allergen safety with conflicting supplier data", () => {
  it("a declared allergen is never also listed as free-from", () => {
    const b = allergenBuckets({ allergens: ["AM", "AE:MAY_CONTAIN"], non_allergens: ["AM", "AE", "AF"] });
    expect(b.contains.map((a) => a.code)).toEqual(["AM"]);
    expect(b.mayContain.map((a) => a.code)).toEqual(["AE"]);
    expect(b.freeFrom.map((a) => a.code)).toEqual(["AF"]);
    expect(b.unknown).toBe(false);
  });

  it("no free-from diet chip when the product declares that allergen", () => {
    expect(productDiets({ labels: ["FREE_FROM_MILK", "FREE_FROM_GLUTEN"], non_allergens: ["AM"], allergens: ["AM:MAY_CONTAIN", "AW"] })).toEqual([]);
    expect(productDiets({ labels: ["FREE_FROM_LACTOSE"], non_allergens: [], allergens: ["AM"] })).toEqual(["L"]);
    expect(allergenBuckets({ allergens: [], non_allergens: [] }).unknown).toBe(true);
  });
});

describe("Place order: lost response", () => {
  it("never creates a second order: the quote shows as used and the same key + body replays the existing order", async () => {
    const keys: string[] = [];
    server.use(
      http.post("*/api/orders", async ({ request }) => {
        keys.push(request.headers.get("Idempotency-Key") ?? "");
        if (keys.length === 1) {
          // The server creates the order, but the browser never gets the answer.
          await fetch(request.clone());
          return HttpResponse.error();
        }
        return undefined; // mock API (idempotent replay)
      }),
    );
    const user = userEvent.setup();
    seedCart([{ id: "100001", qty: 10 }]);
    renderAt("/cart");
    await user.click(await screen.findByRole("button", { name: "Get quote" }));
    const panel = await screen.findByTestId("quote-panel");
    await user.type(within(panel).getByLabelText(/note/i), "back door");
    await user.click(within(panel).getByRole("button", { name: "Place order" }));

    expect(await within(panel).findByText("This quote has already been ordered", {}, { timeout: 3000 })).toBeInTheDocument();
    expect(within(panel).getByText("NETWORK_ERROR")).toBeInTheDocument();
    expect(within(panel).queryByRole("button", { name: "Place order" })).not.toBeInTheDocument();
    const before = db.orders.length;

    await user.click(within(panel).getByRole("button", { name: "Show my order" }));
    expect(await within(panel).findByTestId("order-result")).toBeInTheDocument();
    expect(db.orders.length).toBe(before);
    // the user's request (1), the "server" leg of it (2), the replay (3): one key, one body
    expect(new Set(keys).size).toBe(1);
  });

  it("persists the approval baseline per order in sessionStorage across remounts and highlights changes", async () => {
    const pending = db.orders.map((r) => r.order).find((o) => o.status === "PENDING_APPROVAL")!;
    const first = renderAt("/approvals");
    expect(await screen.findByText(new RegExp(pending.id))).toBeInTheDocument();
    expect(screen.queryByText(/Before:/)).not.toBeInTheDocument();
    first.unmount();

    // Another actor mutates line quantity & content_hash while user is navigated away.
    pending.lines = pending.lines.map((l) => ({ ...l, quantity: l.quantity + 100, line_total_eur: (l.unit_price_eur ?? 1) * (l.quantity + 100) }));
    pending.content_hash = "sha256:mutated-while-unmounted";

    renderAt("/approvals");
    expect(await screen.findByText(/Before:/)).toBeInTheDocument();
  });
});

describe("Telegram 1-tap opt-in UI", () => {
  it("toggles on /approvals, validates negative ID & consent client-side, saves via PUT, and revokes via DELETE", async () => {
    const user = userEvent.setup();
    let putCalls = 0;
    server.use(
      http.put("*/api/auth/telegram", async ({ request }) => {
        putCalls += 1;
        const body = (await request.json()) as { telegram_id: string; enabled?: boolean; consent_confirmed: boolean };
        return HttpResponse.json({
          user_id: "usr_demo",
          company_id: "cmp_demo",
          telegram_username: body.telegram_id.startsWith("@") ? body.telegram_id.slice(1) : body.telegram_id,
          telegram_chat_id: null,
          enabled: true,
          bot_username: "your_aimo_bot",
          consented_at: "2026-11-14T14:35:00Z",
          updated_at: "2026-11-14T14:35:00Z",
        });
      }),
    );

    renderAt("/approvals");
    const toggle = await screen.findByTestId("telegram-optin-toggle");
    await user.click(toggle);

    const card = await screen.findByTestId("telegram-optin-card");
    expect(within(card).getByTestId("telegram-optin-status")).toHaveTextContent("Off");
    expect(within(card).getByTestId("telegram-bot-link")).toHaveAttribute("href", "https://t.me/your_aimo_bot");

    const input = within(card).getByTestId("telegram-id-input");
    const saveBtn = within(card).getByTestId("telegram-save-btn");

    // Reject negative group IDs client-side without issuing PUT.
    await user.type(input, "-100123456");
    await user.click(saveBtn);
    expect(await within(card).findByTestId("telegram-validation-error")).toHaveTextContent(/Negative group/);
    expect(putCalls).toBe(0);

    // Require explicit consent checkbox before PUT.
    await user.clear(input);
    await user.type(input, "@chef_anna");
    await user.click(saveBtn);
    expect(await within(card).findByTestId("telegram-validation-error")).toHaveTextContent(/explicit consent/i);
    expect(putCalls).toBe(0);

    // Check consent and save.
    await user.click(within(card).getByTestId("telegram-consent-checkbox"));
    await user.click(saveBtn);
    expect(putCalls).toBe(1);
    expect(await within(card).findByTestId("telegram-optin-status")).toHaveTextContent("Active · @chef_anna");

    // Revoke via DELETE.
    await user.click(within(card).getByTestId("telegram-revoke-btn"));
    expect(await within(card).findByTestId("telegram-optin-status")).toHaveTextContent("Off");
  });

  it("renders Telegram opt-in directly on /account and handles 404 gracefully", async () => {
    const first = renderAt("/account");
    const card = await screen.findByTestId("telegram-optin-card");
    expect(await within(card).findByTestId("telegram-optin-status")).toHaveTextContent("Off");
    first.unmount();

    server.use(
      http.get("*/api/auth/telegram", () => HttpResponse.json({ detail: "Not Found" }, { status: 404 })),
    );
    renderAt("/account");
    expect(await screen.findByText(/not enabled on this backend yet/i)).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("hides Telegram opt-in controls when authenticated as an agent principal", async () => {
    const cur = getSession()!;
    setSession({
      ...cur,
      principal: { ...cur.principal, kind: "agent", name: "ChefBot" },
    });
    renderAt("/approvals");
    await screen.findByRole("heading", { level: 1, name: "Approvals" });
    expect(screen.queryByTestId("telegram-optin-toggle")).not.toBeInTheDocument();
    expect(screen.queryByTestId("telegram-optin-card")).not.toBeInTheDocument();
  });

  it("displays QR code, 1-tap deep link, and branch switcher on /account and /assistant", async () => {
    const user = userEvent.setup();
    const first = renderAt("/account");
    const card = await screen.findByTestId("telegram-optin-card");

    const qrImg = await within(card).findByTestId("telegram-qr-img");
    expect(qrImg.getAttribute("src")).toMatch(/^data:image\/svg\+xml;utf8,/);
    const deepLink = await within(card).findByTestId("telegram-deeplink-btn");
    expect(deepLink).toHaveAttribute("href", expect.stringMatching(/^https:\/\/t\.me\/your_aimo_bot\?start=link_/));

    expect(within(card).getByTestId("telegram-branch-badge")).toHaveTextContent(/Select branch/i);
    await user.selectOptions(within(card).getByTestId("telegram-branch-select"), "brn_strand");
    await waitFor(() => {
      expect(within(card).getByTestId("telegram-branch-badge")).toHaveTextContent("Hilton Strand");
    });
    first.unmount();

    // /assistant renders compact TelegramConnectBanner with QR expand + branch switcher
    renderAt("/assistant");
    const banner = await screen.findByTestId("telegram-connect-banner");
    expect(within(banner).getByTestId("telegram-branch-badge")).toHaveTextContent("Hilton Strand");
    await user.click(within(banner).getByTestId("telegram-banner-connect-btn"));
    expect((await within(banner).findByTestId("telegram-qr-img")).getAttribute("src")).toMatch(
      /^data:image\/svg\+xml;utf8,/,
    );
    expect(await within(banner).findByTestId("telegram-deeplink-btn")).toHaveAttribute(
      "href",
      expect.stringMatching(/^https:\/\/t\.me\/your_aimo_bot\?start=link_/),
    );
    await user.selectOptions(within(banner).getByTestId("telegram-branch-select"), "brn_kalastaja");
    await waitFor(() => {
      expect(within(banner).getByTestId("telegram-branch-badge")).toHaveTextContent("Hilton Kalastajantorppa");
    });
  });

  it("mints a fresh QR link token after enabling and revoking Telegram pairing", async () => {
    const user = userEvent.setup();
    renderAt("/account");
    const card = await screen.findByTestId("telegram-optin-card");

    const firstLink = await within(card).findByTestId("telegram-deeplink-btn");
    const firstHref = firstLink.getAttribute("href");
    expect(firstHref).toMatch(/start=link_/);

    await user.type(within(card).getByTestId("telegram-id-input"), "@chef_anna");
    await user.click(within(card).getByTestId("telegram-consent-checkbox"));
    await user.click(within(card).getByTestId("telegram-save-btn"));
    expect(await within(card).findByTestId("telegram-optin-status")).toHaveTextContent("Active · @chef_anna");
    expect(within(card).queryByTestId("telegram-qr-img")).not.toBeInTheDocument();

    await user.click(within(card).getByTestId("telegram-revoke-btn"));
    expect(await within(card).findByTestId("telegram-optin-status")).toHaveTextContent("Off");

    await waitFor(() => {
      const nextHref = within(card).getByTestId("telegram-deeplink-btn").getAttribute("href");
      expect(nextHref).toMatch(/start=link_/);
      expect(nextHref).not.toBe(firstHref);
    });
  });

  it("displays R-RECIPE-SANITY recipe over-ordering comparison on /approvals card", async () => {
    const pending = db.orders.map((r) => r.order).find((o) => o.status === "PENDING_APPROVAL")!;
    pending.recipe_sanity_warning = {
      triggered: true,
      rule_id: "R-RECIPE-SANITY",
      recipe_id: "rcp_lohikeitto_01",
      recipe_name: "Lohikeitto (Lactose-Free)",
      target_pax: 60,
      message: "Ordered 120 L milk vs ~18 L expected for 60 pax.",
      offending_lines: [
        {
          product_id: "100001",
          product_name: "Lactose-free milk 1 L",
          ordered_qty: 120,
          unit: "L",
          expected_qty: 18,
          excess_ratio: 6.67,
          excess_cost_eur: 103.02,
        },
      ],
    };

    renderAt("/approvals");
    const notice = await screen.findByTestId("recipe-sanity-warning");
    expect(notice).toHaveTextContent("R-RECIPE-SANITY · Lohikeitto (Lactose-Free) (60 pax)");
    expect(notice).toHaveTextContent("120 L");
    expect(notice).toHaveTextContent("18 L");
  });

  it("renders the 3-gate governance funnel and 1-click remediation links on /approvals", async () => {
    const pending = db.orders.map((r) => r.order).find((o) => o.status === "PENDING_APPROVAL")!;
    pending.policy.rules = [
      { rule_id: "R-MAX-VALUE", outcome: "BLOCKED", message: "Hard order ceiling exceeded" },
      { rule_id: "R-RECIPE-SANITY", outcome: "APPROVAL_REQUIRED", message: "Over-ordered for pax" },
      { rule_id: "R-AUTO-LIMIT", outcome: "APPROVAL_REQUIRED", message: "Exceeds auto-order limit" },
    ];

    renderAt("/approvals");
    const funnel = await screen.findByTestId("governance-funnel");
    expect(within(funnel).getByTestId("governance-gate-SAFETY_LOCK")).toBeInTheDocument();
    const g2 = within(funnel).getByTestId("governance-gate-OPERATIONAL_SANITY");
    const g3 = within(funnel).getByTestId("governance-gate-AUTONOMY_ENVELOPE");

    expect(g2).toHaveTextContent("R-RECIPE-SANITY");
    expect(g2).toHaveTextContent("Human approvable");
    expect(g3).toHaveTextContent("R-MAX-VALUE");
    expect(g3).toHaveTextContent("Hard lock");
    expect(g3).toHaveTextContent("R-AUTO-LIMIT");

    expect(within(funnel).getByTestId("remediate-R-RECIPE-SANITY")).toHaveAttribute("href", "/cart");
    const remLimit = within(funnel).getByTestId("remediate-R-AUTO-LIMIT");
    expect(remLimit.getAttribute("href")).toMatch(/^\/agents/);
  });

  it("applies 1-tap safe recipe fix & place on /approvals without losing the draft", async () => {
    const user = userEvent.setup();
    const pending = db.orders.map((r) => r.order).find((o) => o.status === "PENDING_APPROVAL")!;
    pending.policy.rules = [{ rule_id: "R-RECIPE-SANITY", outcome: "APPROVAL_REQUIRED", message: "Over-ordered for pax" }];
    pending.lines[0]!.quantity = 120;
    pending.recipe_sanity_warning = {
      triggered: true,
      rule_id: "R-RECIPE-SANITY",
      recipe_id: "rcp_lohikeitto_01",
      recipe_name: "Lohikeitto (Lactose-Free)",
      target_pax: 60,
      message: "Ordered 120 L milk vs ~18 L expected for 60 pax.",
      offending_lines: [
        {
          product_id: "100001",
          product_name: "Lactose-free milk 1 L",
          ordered_qty: 120,
          unit: "L",
          expected_qty: 18,
          excess_ratio: 6.67,
          excess_cost_eur: 103.02,
        },
      ],
    };

    const ops: string[] = [];
    let sentKey: string | null = null;
    server.use(
      http.post("*/api/orders", ({ request }) => {
        ops.push("createOrder");
        sentKey = request.headers.get("Idempotency-Key");
        return undefined;
      }),
      http.post("*/api/orders/:id/reject", () => {
        ops.push("rejectOrder");
        return undefined;
      }),
    );

    renderAt("/approvals");
    const btn = await screen.findByTestId("rescue-recipe-btn");
    expect(btn).toHaveTextContent(/Right-size to recipe norm.*103,02.*Place/);

    await user.click(btn);
    expect(await screen.findByText(/Confirming with Valio Aimo|Order placed/i)).toBeInTheDocument();
    expect(ops).toEqual(["createOrder", "rejectOrder"]);
    expect(sentKey).toBe(`rescue-place:${pending.id}:${pending.content_hash.slice(0, 24)}:ADJUST_TO_RECIPE_YIELD`);
    expect(sentKey!.length).toBeLessThanOrEqual(128);
    const rescued = db.orders.map((r) => r.order).find((o) => o.id !== pending.id && (o.status === "SUBMITTING" || o.status === "PLACED"));
    expect(rescued?.lines[0]?.quantity).toBe(18);
    expect(rescued?.policy.outcome).toBe("AUTO");
    const updatedOrig = db.orders.find((r) => r.order.id === pending.id)?.order;
    expect(updatedOrig?.status).toBe("REJECTED");
    expect(updatedOrig?.decision_note).toContain("Superseded by rescued order");
  });

  it("applies 1-tap ON_TIME substitute swap & place preserving diet on R-DELIVERY-RISK", async () => {
    const user = userEvent.setup();
    const pending = db.orders.map((r) => r.order).find((o) => o.status === "PENDING_APPROVAL")!;
    pending.diet = "L";
    pending.total_excl_vat_eur = 2480;
    pending.policy.rules = [{ rule_id: "R-DELIVERY-RISK", outcome: "APPROVAL_REQUIRED", message: "Partial stock on 100003" }];
    pending.lines = [
      {
        ...pending.lines[0]!,
        product_id: "100003",
        product_name: "Lactose-Free Catering Milk Drink 1 L",
        quantity: 1000,
        unit_price_eur: 2.48,
        line_total_eur: 2480,
        delivery_status: "AT_RISK",
        available_qty_state: "FINITE",
        available_amount: 384,
        flags: ["PARTIAL"],
      },
    ];

    renderAt("/approvals");
    const swapBtn = await screen.findByTestId("rescue-swap-btn");
    expect(swapBtn).toHaveTextContent(/100001/);
    expect(swapBtn).toHaveTextContent(/1\s?470,00/);

    await user.click(swapBtn);
    expect(await screen.findByText(/Confirming with Valio Aimo|Order placed/i)).toBeInTheDocument();
    const placed = db.orders
      .map((r) => r.order)
      .find((o) => (o.status === "SUBMITTING" || o.status === "PLACED") && o.id !== pending.id && o.lines[0]?.product_id === "100001");
    expect(placed).toBeDefined();
    expect(placed?.policy.outcome).toBe("AUTO");
    expect(placed?.diet).toBe("L");
    expect(db.orders.find((r) => r.order.id === pending.id)?.order.status).toBe("REJECTED");
  });

  it("hides 1-tap rescue when Gate 1 safety lock or UNIT_MISMATCH is active and aborts on stale hash", async () => {
    const user = userEvent.setup();
    const pending = db.orders.map((r) => r.order).find((o) => o.status === "PENDING_APPROVAL")!;
    pending.policy.rules = [{ rule_id: "R-RECIPE-SANITY", outcome: "APPROVAL_REQUIRED", message: "Over-ordered" }];
    pending.lines[0]!.flags = ["UNIT_MISMATCH"];
    pending.recipe_sanity_warning = {
      triggered: true,
      rule_id: "R-RECIPE-SANITY",
      recipe_id: "rcp_1",
      recipe_name: "Soup",
      target_pax: 10,
      message: "Mismatch",
      offending_lines: [
        { product_id: "100001", product_name: "Milk", ordered_qty: 120, unit: "L", expected_qty: 18, excess_ratio: 6, excess_cost_eur: 50 },
      ],
    };

    const view = renderAt("/approvals");
    expect(await screen.findByTestId("approval-card")).toBeInTheDocument();
    expect(screen.queryByTestId("safe-rescue-strip")).not.toBeInTheDocument();
    view.unmount();

    // Remove UNIT_MISMATCH so strip renders, then simulate stale content_hash on pre-check
    pending.lines[0]!.flags = [];
    let created = false;
    server.use(
      http.get(`*/api/orders/${pending.id}`, () =>
        HttpResponse.json({ ...pending, content_hash: "sha256:stale_drifted_hash" }),
      ),
      http.post("*/api/orders", () => {
        created = true;
        return undefined;
      }),
    );

    renderAt("/approvals");
    const btn = await screen.findByTestId("rescue-recipe-btn");
    await user.click(btn);
    expect(await screen.findByText(/APPROVAL_CONTENT_CHANGED/)).toBeInTheDocument();
    expect(created).toBe(false);
    expect(db.orders.find((r) => r.order.id === pending.id)?.order.status).toBe("PENDING_APPROVAL");
  });
});

describe("Approval 5h inventory hold pill & hanging-order warning line", () => {
  it("renders HELD_5H countdown pill and UNRESERVED_DUE_TO_WARNINGS pill + warning line from server fields", async () => {
    const pending = db.orders.map((r) => r.order).find((o) => o.status === "PENDING_APPROVAL")!;
    pending.reservation_status = "HELD_5H";
    pending.reservation_expires_at = new Date(Date.now() + 4 * 3_600_000 + 30 * 60_000).toISOString();
    pending.company_warning_count_7d = 0;
    pending.reservation_warning_message = null;

    const first = renderAt("/approvals");
    const heldPill = await screen.findByTestId("reservation-status-pill");
    expect(heldPill).toHaveTextContent(/5h hold/i);
    expect(screen.queryByTestId("reservation-warning-line")).not.toBeInTheDocument();
    first.unmount();

    pending.reservation_status = "UNRESERVED_DUE_TO_WARNINGS";
    pending.reservation_expires_at = null;
    pending.company_warning_count_7d = 3;
    pending.reservation_warning_message = "3 unresolved hold warnings in 7 days — stock is not held.";

    renderAt("/approvals");
    const warnPill = await screen.findByTestId("reservation-status-pill");
    expect(warnPill).toHaveTextContent(/Unreserved/i);
    expect(await screen.findByTestId("reservation-warning-line")).toHaveTextContent(
      /3 unresolved hold warnings in 7 days/,
    );
  });
});

describe("Order assurance settings card on /agents", () => {
  it("updates threshold via preset on /agents for humans and shows read-only notice for agents", async () => {
    const user = userEvent.setup();
    const first = renderAt("/agents");
    const card = await screen.findByTestId("assurance-settings-card");
    expect(await within(card).findByTestId("assurance-threshold-pill")).toHaveTextContent("80%");

    await user.click(within(card).getByTestId("assurance-preset-50"));
    await waitFor(() => {
      expect(within(card).getByTestId("assurance-threshold-pill")).toHaveTextContent("50%");
    });
    first.unmount();

    const agent = db.agents.find((a) => a.id === "agt_kitchen01")!;
    const tok = await endpoints.agentToken({ agent_id: agent.id, agent_secret: SEED_AGENT_SECRETS.agt_kitchen01 });
    setSession({
      token: tok.token,
      principal: { id: agent.id, name: agent.name, kind: "agent", company_id: agent.company_id },
      company: SEED_COMPANY,
    });

    renderAt("/agents");
    const agentCard = await screen.findByTestId("assurance-settings-card");
    expect(await within(agentCard).findByTestId("assurance-readonly-note")).toBeInTheDocument();
    expect(within(agentCard).getByTestId("assurance-slider")).toBeDisabled();
    expect(within(agentCard).getByTestId("assurance-preset-50")).toBeDisabled();
  });
});
