// M9a: adaptations to the real FastAPI backend (see PROGRESS.md "Contract mismatches").
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, ENDPOINT_MISSING, api, isEndpointMissing } from "./client";
import { normalizeSubstitutes } from "./endpoints";
import { clearSession } from "../auth/session";
import { shortId } from "../lib/ids";

function respond(status: number, body: unknown, headers: Record<string, string> = {}) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json", ...headers } });
}

describe("real-backend compatibility", () => {
  const fetchMock = vi.fn();
  beforeEach(() => {
    fetchMock.mockReset();
    vi.stubGlobal("fetch", fetchMock);
    clearSession();
  });
  afterEach(() => vi.unstubAllGlobals());

  it("maps FastAPI's bare 404 (missing route) to ENDPOINT_NOT_AVAILABLE", async () => {
    fetchMock.mockResolvedValue(respond(404, { detail: "Not Found" }, { "X-Request-ID": "req_404" }));
    const err = (await api.get("/api/a2a/tasks").catch((e) => e)) as ApiError;
    expect(err.status).toBe(404);
    expect(err.code).toBe(ENDPOINT_MISSING);
    expect(err.request_id).toBe("req_404");
    expect(isEndpointMissing(err)).toBe(true);
  });

  it("keeps an enveloped 404 (record not found) as NOT_FOUND", async () => {
    fetchMock.mockResolvedValue(respond(404, { error: { code: "NOT_FOUND", message: "Product not found", details: {} }, request_id: "req_1" }));
    const err = (await api.get("/api/products/1").catch((e) => e)) as ApiError;
    expect(err.code).toBe("NOT_FOUND");
    expect(isEndpointMissing(err)).toBe(false);
  });

  it("derives details.field from FastAPI validation errors", async () => {
    fetchMock.mockResolvedValue(
      respond(422, {
        error: {
          code: "VALIDATION_ERROR",
          message: "Request validation failed",
          details: { errors: [{ type: "value_error", loc: ["body", "email"], msg: "Value error, Invalid email address" }] },
        },
        request_id: "req_2",
      }),
    );
    const err = (await api.post("/api/auth/register", {}).catch((e) => e)) as ApiError;
    expect(err.details).toMatchObject({ field: "email", field_message: "Invalid email address" });
  });

  it("normalizes the backend substitutes shape (quote_line / tradeoffs) and the mock shape (line / trade_offs)", () => {
    const product = { id: "100001" } as never;
    const line = { product_id: "100001", delivery_status: "ON_TIME" } as never;
    const real = normalizeSubstitutes({
      requested_product_id: "100003",
      items: [{ product, quote_line: line, tradeoffs: ["Price -€1.47/ST"], reason: "Same category", price_delta_eur: -1.47 }, { reason: "broken" }],
    });
    expect(real.items).toEqual([{ product, line, trade_offs: ["Price -€1.47/ST"], reason: "Same category" }]);
    const mock = normalizeSubstitutes({ items: [{ product, line, trade_offs: ["x"], reason: "y" }] });
    expect(mock.items[0]).toEqual({ product, line, trade_offs: ["x"], reason: "y" });
    expect(normalizeSubstitutes(null).items).toEqual([]);
  });

  it("shortens long ULID ids for tables but keeps short mock ids", () => {
    expect(shortId("ord_01M7C5DMP4B3DS0DZ3C41GN3G5")).toBe("ord_…C41GN3G5");
    expect(shortId("ord_0001ABCDEFGHIJ")).toBe("ord_0001ABCDEFGHIJ");
  });
});
