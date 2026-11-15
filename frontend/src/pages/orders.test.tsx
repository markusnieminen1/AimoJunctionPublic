// M6: approvals inbox (approve with content_hash + Idempotency-Key, 409 re-ask with highlights), orders list, order detail.
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
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

const pendingOrder = () => db.orders.map((r) => r.order).find((o) => o.status === "PENDING_APPROVAL")!;

type ApproveCall = { key: string | null; body: { content_hash?: string; note?: string } };
function recordApprovals(): ApproveCall[] {
  const calls: ApproveCall[] = [];
  server.use(
    http.post("*/api/orders/:id/approve", async ({ request }) => {
      calls.push({ key: request.headers.get("Idempotency-Key"), body: (await request.clone().json()) as ApproveCall["body"] });
      return undefined; // fall through to the mock API
    }),
  );
  return calls;
}

describe("ApprovalsPage", () => {
  it("renders the seeded pending agent order as raw facts", async () => {
    renderAt("/approvals");
    const card = await screen.findByTestId("approval-card");
    expect(within(card).getByText("Kitchen Reorder Bot")).toBeInTheDocument();
    expect(within(card).getByText(/authorized by Anna Korhonen/)).toBeInTheDocument();
    expect(within(card).getAllByText("1 010,00 €").length).toBeGreaterThan(0);
    expect(within(card).getAllByText("R-AUTO-LIMIT").length).toBeGreaterThan(0);
    expect(within(card).getByText(/Expires in 3h 1\dm/)).toBeInTheDocument();
    expect(within(card).getByText("Delivery Mon 16.11.2026")).toBeInTheDocument();
    expect(within(card).getByText("100001")).toBeInTheDocument();
    expect(within(card).getByText(pendingOrder().content_hash)).toBeInTheDocument();
    expect(screen.getByText("Live")).toBeInTheDocument();
    // Phone demo: Approve / Reject stick to the screen bottom while the card is in view. `position: sticky` only works
    // if no ancestor inside the card is a scroll container (overflow-hidden on the card silently broke it once).
    const actions = within(card).getByTestId("approval-actions");
    expect(actions).toHaveClass("sticky", "bottom-0");
    expect(within(actions).getByRole("button", { name: /Approve order/i })).toBeInTheDocument();
    expect(within(actions).getByRole("button", { name: /Reject/i })).toBeInTheDocument();
    expect(card.className).not.toMatch(/overflow-(hidden|auto|scroll)/);
  });

  it("shows the diet requirement the server checked the order against", async () => {
    pendingOrder().diet = "L";
    renderAt("/approvals");
    const card = await screen.findByTestId("approval-card");
    expect(within(card).getByTestId("diet-requirement")).toHaveTextContent(/L\s*Lactose-free/);
  });

  it("approves with the displayed content_hash + an Idempotency-Key, removes the card and updates the nav badge", async () => {
    const calls = recordApprovals();
    const user = userEvent.setup();
    const hash = pendingOrder().content_hash;
    renderAt("/approvals");
    const card = await screen.findByTestId("approval-card");
    // nav badge shows 1 pending (desktop rail + mobile bar)
    const nav = screen.getAllByRole("link", { name: /Approvals/ });
    await waitFor(() => expect(nav.some((l) => within(l).queryByText("1"))).toBe(true));

    await user.type(within(card).getByLabelText(/Decision note/), "OK for Monday");
    await user.click(within(card).getByRole("button", { name: "Approve order" }));

    await waitFor(() => expect(screen.queryByTestId("approval-card")).not.toBeInTheDocument());
    expect(calls).toHaveLength(1);
    expect(calls[0]!.body.content_hash).toBe(hash);
    expect(calls[0]!.body.note).toBe("OK for Monday");
    expect(calls[0]!.key).toMatch(/.{8,}/);
    expect(pendingOrder()).toBeUndefined();
    const result = await screen.findByTestId("order-result");
    expect(result).toHaveTextContent(/Confirming with Valio Aimo…|Order placed/);
    expect(await screen.findByText("All caught up")).toBeInTheDocument();
    await waitFor(() => expect(screen.getAllByRole("link", { name: /Approvals/ }).every((l) => !within(l).queryByText("1"))).toBe(true));
  });

  it("after a price change (409 QUOTE_STALE) shows the updated-details warning, highlights the line and re-asks with a new hash + key", async () => {
    const calls = recordApprovals();
    const user = userEvent.setup();
    const oldHash = pendingOrder().content_hash;
    renderAt("/approvals");
    const card = await screen.findByTestId("approval-card");
    db.priceOverrides["100001"] = 1.05;

    await user.click(within(card).getByRole("button", { name: "Approve order" }));
    const warning = await within(card).findByTestId("approval-changed");
    expect(warning).toHaveTextContent("Order details updated since you opened this card — review before confirming.");
    expect(warning).toHaveTextContent("QUOTE_STALE");
    await waitFor(() => expect(within(card).getAllByText("1 050,00 €").length).toBeGreaterThan(0));
    expect(warning).toHaveTextContent("was 1 010,00 €");
    const changed = card.querySelectorAll('[data-changed="true"]');
    expect(changed.length).toBe(1);
    expect(changed[0]).toHaveTextContent("Before: 1 000 ST × 1,01 € = 1 010,00 €");

    const newHash = pendingOrder().content_hash;
    expect(newHash).not.toBe(oldHash);
    await user.click(within(card).getByRole("button", { name: "Approve updated order" }));
    await waitFor(() => expect(screen.queryByTestId("approval-card")).not.toBeInTheDocument());
    expect(calls).toHaveLength(2);
    expect(calls[0]!.body.content_hash).toBe(oldHash);
    expect(calls[1]!.body.content_hash).toBe(newHash);
    expect(calls[1]!.key).not.toBe(calls[0]!.key);
  });

  it("409 APPROVAL_CONTENT_CHANGED shows the warning with code + request_id and asks again with a fresh key", async () => {
    const keys: (string | null)[] = [];
    server.use(
      http.post(
        "*/api/orders/:id/approve",
        ({ request }) => {
          keys.push(request.headers.get("Idempotency-Key"));
          return HttpResponse.json(
            { error: { code: "APPROVAL_CONTENT_CHANGED", message: "The order changed since you opened it.", details: {} }, request_id: "req_test_changed" },
            { status: 409 },
          );
        },
        { once: true },
      ),
    );
    const calls = recordApprovals();
    const user = userEvent.setup();
    renderAt("/approvals");
    const card = await screen.findByTestId("approval-card");
    await user.click(within(card).getByRole("button", { name: "Approve order" }));
    const warning = await within(card).findByTestId("approval-changed");
    expect(warning).toHaveTextContent("APPROVAL_CONTENT_CHANGED");
    expect(warning).toHaveTextContent("req_test_changed");

    await user.click(within(card).getByRole("button", { name: "Approve updated order" }));
    await waitFor(() => expect(screen.queryByTestId("approval-card")).not.toBeInTheDocument());
    expect(calls.at(-1)!.key).not.toBe(keys[0]);
  });

  it("rejects with a note after confirming", async () => {
    const user = userEvent.setup();
    renderAt("/approvals");
    const card = await screen.findByTestId("approval-card");
    await user.type(within(card).getByLabelText(/Decision note/), "Too much milk");
    await user.click(within(card).getByRole("button", { name: "Reject" }));
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "Reject order" }));
    await waitFor(() => expect(screen.queryByTestId("approval-card")).not.toBeInTheDocument());
    expect(await screen.findByText("Order rejected")).toBeInTheDocument();
    expect(db.orders.map((r) => r.order).find((o) => o.status === "REJECTED" && o.decision_note === "Too much milk")).toBeTruthy();
  });
});

describe("OrdersPage", () => {
  it("lists orders as OrderBlocks, expands lines, and filters by status pill and created-by", async () => {
    const user = userEvent.setup();
    renderAt("/orders");
    const list = await screen.findByTestId("orders-list");
    expect(within(list).getAllByTestId("order-block")).toHaveLength(5);

    await user.click(within(screen.getByRole("group", { name: "Order status" })).getByRole("button", { name: /Waiting for approval/ }));
    await waitFor(() => expect(within(screen.getByTestId("orders-list")).getAllByTestId("order-block")).toHaveLength(1));
    expect(within(screen.getByTestId("orders-list")).getByText("Waiting for approval")).toBeInTheDocument();

    // Expand an order block to reveal lines
    await user.click(within(screen.getByTestId("orders-list")).getByTestId("order-block-header"));
    expect(within(screen.getByTestId("orders-list")).getAllByTestId("order-line").length).toBeGreaterThan(0);

    await user.click(within(screen.getByRole("group", { name: "Order status" })).getByRole("button", { name: /^All$/i }));
    await user.click(within(screen.getByRole("group", { name: "Created by" })).getByRole("button", { name: "Humans" }));
    await waitFor(() => expect(within(screen.getByTestId("orders-list")).getAllByTestId("order-block")).toHaveLength(1));
    expect(within(screen.getByTestId("orders-list")).getByText("Anna Korhonen")).toBeInTheDocument();
  });
});

describe("OrderDetailPage", () => {
  it("renders header, policy, lines and the event timeline with request id chips", async () => {
    const placed = db.orders.map((r) => r.order).find((o) => o.status === "PLACED" && o.decided_by)!;
    renderAt(`/orders/${placed.id}`);
    const timeline = await screen.findByTestId("order-timeline");
    const events = within(timeline).getAllByTestId("timeline-event");
    expect(events.length).toBe(placed.events.length);
    expect(timeline).toHaveTextContent("Approved by Anna Korhonen");
    expect(within(timeline).getAllByText(placed.events[0]!.request_id).length).toBeGreaterThan(0);
    expect(within(timeline).getAllByRole("link", { name: /in the audit log/ })[0]).toHaveAttribute("href", `/audit?request_id=${placed.events[0]!.request_id}`);
    expect(screen.getAllByText("Placed").length).toBeGreaterThan(0);
    expect(screen.getAllByText(placed.gateway_reference!).length).toBeGreaterThan(0);
    expect(screen.getByText("R-AUTO-LIMIT")).toBeInTheDocument();
    expect(screen.getAllByText("“OK for Monday”").length).toBeGreaterThan(0);
    expect(screen.queryByRole("button", { name: /retry/i })).not.toBeInTheDocument();
  });

  it.each([
    ["SUBMISSION_UNKNOWN", "Confirming with Valio Aimo…"],
    ["SUBMITTING", "Sending to Valio Aimo…"],
  ] as const)("never offers a retry (or reorder) in %s", async (status, label) => {
    const o = pendingOrder();
    o.status = status;
    renderAt(`/orders/${o.id}`);
    expect((await screen.findAllByText(label)).length).toBeGreaterThan(0);
    expect(screen.queryByRole("button", { name: /retry|try again|reorder/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /approve|reject/i })).not.toBeInTheDocument();
  });

  it("approves a pending order inline", async () => {
    const user = userEvent.setup();
    const o = pendingOrder();
    renderAt(`/orders/${o.id}`);
    const panel = await screen.findByTestId("pending-panel");
    await user.click(within(panel).getByRole("button", { name: "Approve order" }));
    await waitFor(() => expect(screen.queryByTestId("pending-panel")).not.toBeInTheDocument());
    expect(screen.getAllByText(/Sending to Valio Aimo…|Placed/).length).toBeGreaterThan(0);
  });

  it("cancels a pending order", async () => {
    const user = userEvent.setup();
    const o = pendingOrder();
    renderAt(`/orders/${o.id}`);
    await user.click(await screen.findByRole("button", { name: "Cancel order" }));
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "Cancel order" }));
    await waitFor(() => expect(screen.getAllByText("Cancelled").length).toBeGreaterThan(0));
  });

  it("renders the How we got here reasoning & conversation trace with chain verification and A2A link", async () => {
    const user = userEvent.setup();
    const placed = db.orders.map((r) => r.order).find((o) => o.status === "PLACED" && o.decided_by)!;
    renderAt(`/orders/${placed.id}`);
    const trace = await screen.findByTestId("order-trace");
    expect(within(trace).getByText("How we got here · Reasoning & Conversation Trace")).toBeInTheDocument();
    expect(await within(trace).findByTestId("trace-chain-badge")).toHaveTextContent("Chain verified");
    const steps = await within(trace).findAllByTestId("order-trace-step");
    expect(steps.length).toBeGreaterThanOrEqual(placed.events.length);
    // Collapsing hides the step list cleanly
    await user.click(within(trace).getByRole("button", { name: "Hide trace" }));
    expect(within(trace).queryByTestId("order-trace-steps")).not.toBeInTheDocument();
  });

  it("shows a calm fallback when the order trace endpoint returns 404", async () => {
    server.use(
      http.get("*/api/orders/:id/trace", () =>
        HttpResponse.json({ error: { code: "NOT_FOUND", message: "Not Found", details: {} }, request_id: "req_trace_404" }, { status: 404 }),
      ),
    );
    const placed = db.orders.map((r) => r.order).find((o) => o.status === "PLACED")!;
    renderAt(`/orders/${placed.id}`);
    const trace = await screen.findByTestId("order-trace");
    expect(await within(trace).findByText("Detailed reasoning trace is not available from this server.")).toBeInTheDocument();
  });

  it("changes page size via Showing selector and passes limit to GET /api/orders", async () => {
    const limits: string[] = [];
    server.use(
      http.get("*/api/orders", ({ request }) => {
        const url = new URL(request.url);
        if (url.searchParams.get("status") !== "PENDING_APPROVAL") {
          limits.push(url.searchParams.get("limit") ?? "");
        }
        return undefined;
      }),
    );
    const user = userEvent.setup();
    renderAt("/orders");
    const select = (await screen.findByTestId("orders-page-size")) as HTMLSelectElement;
    expect(select.value).toBe("20");
    await user.selectOptions(select, "50");
    await waitFor(() => expect(limits).toContain("50"));
  });

  it("renders assistant chat_trace section on /orders/:id when trace includes chat_trace", async () => {
    const placed = db.orders.map((r) => r.order).find((o) => o.status === "PLACED")!;
    server.use(
      http.get(`*/api/orders/${placed.id}/trace`, () =>
        HttpResponse.json({
          order_id: placed.id,
          status: placed.status,
          chain_verified: true,
          steps: [],
          chat_trace: {
            channel: "web",
            user_message: "20 liters of milk",
            assistant_reply: "Placed routine milk order automatically.",
            assurance_score: 62,
            assurance_threshold: 50,
            auto_executed: true,
            llm_mode: "live",
          },
        }),
      ),
    );
    renderAt(`/orders/${placed.id}`);
    const chatTrace = await screen.findByTestId("order-chat-trace");
    expect(chatTrace).toHaveTextContent("20 liters of milk");
    expect(chatTrace).toHaveTextContent("Placed routine milk order automatically.");
  });
});
