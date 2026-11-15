// Mock persistence (backlog): snapshot → "reload" (fresh seed) → restore keeps data, the login token and the clock.
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it } from "vitest";
import { endpoints } from "../api/endpoints";
import { clearSession } from "../auth/session";
import { advanceMockClock, mockNowMs, resetMockClock } from "./clock";
import { db, resetMockDb } from "./db";
import { MOCK_STATE_KEY, clearMockState, loadMockState, restoreMockState, saveMockState, serializeMockState } from "./persist";
import { setupMockServer } from "./server";

const server = setupMockServer();
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());
beforeEach(() => {
  resetMockDb();
  resetMockClock();
  clearSession();
  window.sessionStorage.clear();
});

describe("mock persistence", () => {
  it("restores orders, Maps, the login token and the clock after a simulated reload", async () => {
    const login = await endpoints.login({ email: "anna.korhonen@hotel.fi", password: "demo1234" });
    const token = login.token;
    const q = await endpoints.createQuote({ requested_delivery_date: "2026-11-16", lines: [{ product_id: "100001", quantity: 10 }] }, { token });
    const o = await endpoints.createOrder({ quote_id: q.id }, "idem-persist-1", { token });
    advanceMockClock(15 * 60_000);
    const savedNow = mockNowMs();
    saveMockState();
    expect(window.sessionStorage.getItem(MOCK_STATE_KEY)).toContain(o.id);

    // "Reload": the module state is re-seeded and the clock restarts at the demo epoch.
    resetMockDb();
    resetMockClock();
    expect(db.tokens.has(token)).toBe(false);
    expect(mockNowMs()).toBeLessThan(savedNow);

    expect(loadMockState()).toBe(true);
    expect(db.tokens).toBeInstanceOf(Map);
    expect(db.tokens.has(token)).toBe(true);
    expect(db.quotes.get(q.id)?.quote.id).toBe(q.id);
    expect(mockNowMs()).toBeGreaterThanOrEqual(savedNow);

    // The old session token still works, and the order is still there.
    const me = await endpoints.me({ token });
    expect(me.principal.id).toBe(login.user.id);
    const again = await endpoints.getOrder(o.id, { token });
    expect(again.id).toBe(o.id);
    // Idempotency records survive too: the same key + body replays the same order.
    const replay = await endpoints.createOrder({ quote_id: q.id }, "idem-persist-1", { token });
    expect(replay.id).toBe(o.id);
  });

  it("ignores missing, corrupt or old-version snapshots and keeps the fresh seed", () => {
    const orders = db.orders.length;
    expect(restoreMockState(null)).toBe(false);
    expect(restoreMockState("{not json")).toBe(false);
    expect(restoreMockState(JSON.stringify({ v: 999, saved_real_ms: 0, mock_now_ms: 0, db: {} }))).toBe(false);
    expect(restoreMockState(JSON.stringify({ v: 1, saved_real_ms: 0, mock_now_ms: 0, db: { tokens: [] } }))).toBe(false);
    expect(db.orders.length).toBe(orders);
  });

  it("continues the clock from the saved time plus the real time that passed", () => {
    const raw = serializeMockState(1_000);
    const saved = (JSON.parse(raw) as { mock_now_ms: number }).mock_now_ms;
    expect(restoreMockState(raw, 61_000)).toBe(true);
    expect(mockNowMs() - saved).toBeGreaterThanOrEqual(60_000);
    expect(mockNowMs() - saved).toBeLessThan(65_000);
  });

  it("clearMockState removes the snapshot", () => {
    saveMockState();
    expect(window.sessionStorage.getItem(MOCK_STATE_KEY)).not.toBeNull();
    clearMockState();
    expect(window.sessionStorage.getItem(MOCK_STATE_KEY)).toBeNull();
    expect(loadMockState()).toBe(false);
  });
});
