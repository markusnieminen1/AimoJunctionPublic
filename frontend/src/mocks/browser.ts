// Starts the MSW worker in the browser (only when VITE_USE_MOCKS === "true", see main.tsx)
// and exposes dev helpers on `window.__mock` for demos/testing edge cases from the console.
// The mock database + clock are snapshotted to sessionStorage after every mocked response and restored on start,
// so a reload keeps the demo state and the mock login (persist.ts). `__mock.reset()` clears the snapshot.
import { delay, http, HttpResponse } from "msw";
import { setupWorker } from "msw/browser";
import { handlers } from "./handlers";
import { advanceMockClock, mockNowMs, isoAt, resetMockClock } from "./clock";
import { db, resetMockDb, simulateAgentOrder, type GatewayMode } from "./db";
import { clearMockState, flushMockSave, loadMockState, scheduleMockSave } from "./persist";

/** Run a dev helper, then persist its effect right away. */
function persisted<A extends unknown[], R>(fn: (...a: A) => R): (...a: A) => R {
  return (...a: A) => {
    const r = fn(...a);
    flushMockSave();
    return r;
  };
}

let worker: ReturnType<typeof setupWorker> | null = null;

export const mockDevTools = {
  /**
   * Make one endpoint fail (error-state demos/tests), e.g. failEndpoint("orders") or
   * failEndpoint("dashboard", 503, "UPSTREAM_UNAVAILABLE"). Path is relative to /api (MSW path pattern, "*" allowed).
   * Not persisted: a reload or clearFailures() restores normal answers.
   */
  failEndpoint: (path: string, status = 500, code = "INTERNAL_ERROR", method: "get" | "post" | "patch" | "all" = "all") => {
    const rid = `req_mockfail_${Math.random().toString(36).slice(2, 10)}`;
    worker?.use(
      http[method](`*/api/${path.replace(/^\/+/, "")}`, () =>
        HttpResponse.json(
          { error: { code, message: `Simulated failure of /api/${path} (__mock.failEndpoint)`, details: {} }, request_id: rid },
          { status, headers: { "X-Request-ID": rid, "X-Server-Time": isoAt(mockNowMs()) } },
        ),
      ),
    );
    return rid;
  },
  /** Delay one endpoint (loading-skeleton demos), e.g. slowEndpoint("dashboard", 3000). Falls through to the normal mock afterwards. */
  slowEndpoint: (path: string, ms = 3000) => {
    worker?.use(
      http.all(`*/api/${path.replace(/^\/+/, "")}`, async () => {
        await delay(ms);
        return undefined;
      }),
    );
  },
  /** Undo every failEndpoint() / slowEndpoint(). */
  clearFailures: () => worker?.resetHandlers(),
  /** A new agent order arrives (default: Kitchen Reorder Bot, 1,000 × 100001 → PENDING_APPROVAL). */
  simulateAgentOrder: persisted(simulateAgentOrder),
  /** Jump the mock server clock forward, e.g. advanceMinutes(31) to expire open quotes. */
  advanceMinutes: persisted((min: number) => {
    advanceMockClock(min * 60_000);
    return isoAt(mockNowMs());
  }),
  /** Change a product's live price → next order/approval of an older quote gets 409 QUOTE_STALE. */
  setPrice: persisted((productId: string, price: number) => {
    db.priceOverrides[productId] = price;
  }),
  /** Order gateway behaviour for new submissions: "ok" → PLACED, "timeout" → SUBMISSION_UNKNOWN → PLACED, "error" → FAILED. */
  setGateway: persisted((mode: GatewayMode) => {
    db.gatewayMode = mode;
  }),
  /** Assistant availability: false → POST /api/assistant/chat answers 503 LLM_UNAVAILABLE. */
  setAssistantAvailable: persisted((ok: boolean) => {
    db.assistantDown = !ok;
  }),
  /** Reset all mock state and the clock, and clear the sessionStorage snapshot (tokens are dropped too, so log in again). */
  reset: () => {
    clearMockState();
    resetMockDb();
    resetMockClock();
    flushMockSave();
  },
  now: () => isoAt(mockNowMs()),
  db: () => db,
};

declare global {
  interface Window {
    __mock?: typeof mockDevTools;
  }
}

export async function startMockWorker(): Promise<void> {
  const restored = loadMockState();
  worker = setupWorker(...handlers);
  worker.events.on("response:mocked", () => scheduleMockSave());
  window.addEventListener("pagehide", () => flushMockSave());
  await worker.start({ onUnhandledRequest: "bypass", serviceWorker: { url: "/mockServiceWorker.js" }, quiet: true });
  window.__mock = mockDevTools;
  console.info(
    `[AimoAnnos] Mock API active (VITE_USE_MOCKS=true, ${restored ? "state restored from sessionStorage" : "fresh seed"}). Dev helpers: window.__mock`,
    Object.keys(mockDevTools),
  );
}
