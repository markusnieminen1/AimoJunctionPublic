import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, apiRequest, api, policyFromError, setUnauthorizedHandler } from "./client";
import { getSession, setSession, clearSession } from "../auth/session";
import { getServerOffsetMs, isServerClockSynced, resetServerClock, serverNowMs } from "../lib/serverClock";

function respond(status: number, body: unknown, headers: Record<string, string> = {}) {
  const text = typeof body === "string" ? body : JSON.stringify(body);
  return new Response(text, { status, headers: { "Content-Type": "application/json", ...headers } });
}

describe("api client", () => {
  const fetchMock = vi.fn();
  beforeEach(() => {
    fetchMock.mockReset();
    vi.stubGlobal("fetch", fetchMock);
    clearSession();
    resetServerClock();
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("parses the error envelope into ApiError(code, message, request_id, details)", async () => {
    fetchMock.mockResolvedValue(
      respond(409, { error: { code: "QUOTE_EXPIRED", message: "The quote has expired", details: { quote_id: "qt_1" } }, request_id: "req_01JABC" }, { "X-Request-ID": "req_01JABC" }),
    );
    const err = await apiRequest("POST", "/api/orders", { body: { quote_id: "qt_1" } }).catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err).toMatchObject({ status: 409, code: "QUOTE_EXPIRED", message: "The quote has expired", request_id: "req_01JABC", details: { quote_id: "qt_1" } });
  });

  it("falls back to the X-Request-ID header and a synthetic code for non-JSON errors", async () => {
    fetchMock.mockResolvedValue(new Response("<html>Bad Gateway</html>", { status: 502, statusText: "Bad Gateway", headers: { "X-Request-ID": "req_hdr" } }));
    const err = (await api.get("/api/health").catch((e) => e)) as ApiError;
    expect(err.code).toBe("BAD_RESPONSE");
    expect(err.status).toBe(502);
    expect(err.request_id).toBe("req_hdr");
  });

  it("maps network failures to NETWORK_ERROR", async () => {
    fetchMock.mockRejectedValue(new TypeError("Failed to fetch"));
    const err = (await api.get("/api/health").catch((e) => e)) as ApiError;
    expect(err.code).toBe("NETWORK_ERROR");
    expect(err.status).toBe(0);
    expect(err.request_id).toBeNull();
  });

  it("uses relative /api URLs on the page origin, bearer token and Idempotency-Key", async () => {
    setSession({ token: "tok123", principal: { kind: "user", id: "u", name: "U", company_id: "c" }, company: null });
    fetchMock.mockResolvedValue(respond(201, { id: "ord_1" }));
    await api.post("/api/orders", { quote_id: "q" }, { idempotencyKey: "idem-1" });
    const [url, init] = fetchMock.mock.calls[0]!;
    expect(String(url)).toBe(`${window.location.origin}/api/orders`);
    expect(init.headers.Authorization).toBe("Bearer tok123");
    expect(init.headers["Idempotency-Key"]).toBe("idem-1");
    expect(JSON.parse(init.body)).toEqual({ quote_id: "q" });
    await expect(apiRequest("GET", "http://evil.example/api/x")).rejects.toThrow(/relative/);
  });

  it("serializes query params and skips empty ones", async () => {
    fetchMock.mockResolvedValue(respond(200, { items: [], total: 0 }));
    await api.get("/api/products", { query: { q: "maito", diet: "L,G", category: "", offset: 0, limit: undefined } });
    const url = new URL(String(fetchMock.mock.calls[0]![0]));
    expect(url.searchParams.get("q")).toBe("maito");
    expect(url.searchParams.get("diet")).toBe("L,G");
    expect(url.searchParams.get("offset")).toBe("0");
    expect(url.searchParams.has("category")).toBe(false);
    expect(url.searchParams.has("limit")).toBe(false);
  });

  it("on 401 with a token: clears the session and calls the unauthorized handler", async () => {
    const onUnauth = vi.fn();
    setUnauthorizedHandler(onUnauth);
    setSession({ token: "expired", principal: { kind: "user", id: "u", name: "U", company_id: "c" }, company: null });
    fetchMock.mockResolvedValue(respond(401, { error: { code: "UNAUTHENTICATED", message: "Session expired" }, request_id: "req_401" }));
    const err = (await api.get("/api/me").catch((e) => e)) as ApiError;
    expect(err.code).toBe("UNAUTHENTICATED");
    expect(getSession()).toBeNull();
    expect(onUnauth).toHaveBeenCalledTimes(1);
  });

  it("a 401 on an anonymous call (wrong password) does not redirect", async () => {
    const onUnauth = vi.fn();
    setUnauthorizedHandler(onUnauth);
    fetchMock.mockResolvedValue(respond(401, { error: { code: "UNAUTHENTICATED", message: "Wrong email or password" }, request_id: "req_x" }));
    await api.post("/api/auth/login", { email: "a", password: "b" }, { anonymous: true }).catch(() => undefined);
    expect(onUnauth).not.toHaveBeenCalled();
  });

  it("tracks the server clock offset from X-Server-Time", async () => {
    const serverTime = "2026-11-14T10:00:00+02:00";
    fetchMock.mockResolvedValue(respond(200, { status: "ok" }, { "X-Server-Time": serverTime }));
    await api.get("/api/health");
    expect(isServerClockSynced()).toBe(true);
    const expectedOffset = Date.parse(serverTime) - Date.now();
    expect(Math.abs(getServerOffsetMs() - expectedOffset)).toBeLessThan(1000);
    expect(Math.abs(serverNowMs() - Date.parse(serverTime))).toBeLessThan(1000);
  });

  it("extracts a PolicyDecision from POLICY_BLOCKED details in either shape", () => {
    const decision = { outcome: "BLOCKED", rules: [{ rule_id: "R-MAX-ORDER", outcome: "BLOCKED", message: "too much" }] };
    expect(policyFromError(new ApiError(403, "POLICY_BLOCKED", "x", "r", decision))?.rules[0]?.rule_id).toBe("R-MAX-ORDER");
    expect(policyFromError(new ApiError(403, "POLICY_BLOCKED", "x", "r", { policy: decision }))?.outcome).toBe("BLOCKED");
    expect(policyFromError(new ApiError(403, "FORBIDDEN", "x", "r", {}))).toBeNull();
  });
});
