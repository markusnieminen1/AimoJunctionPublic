// fetch wrapper: relative /api URLs only, bearer auth, error envelope → ApiError,
// X-Server-Time → server clock offset, optional Idempotency-Key, 401 → logout + redirect.
import { clearSession, getToken } from "../auth/session";
import { recordServerTime } from "../lib/serverClock";
import type { ErrorEnvelope, PolicyDecision } from "./types";

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly request_id: string | null;
  readonly details: Record<string, unknown>;

  constructor(status: number, code: string, message: string, request_id: string | null, details: Record<string, unknown> = {}) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.request_id = request_id;
    this.details = details;
  }
}

export function isApiError(e: unknown): e is ApiError {
  return e instanceof ApiError;
}

/** Normalize anything thrown into an ApiError (for toasts / inline error panels). */
export function toApiError(e: unknown): ApiError {
  if (e instanceof ApiError) return e;
  const msg = e instanceof Error ? e.message : String(e);
  return new ApiError(0, "NETWORK_ERROR", msg || "Network error", null);
}

/**
 * Extract a PolicyDecision from a 403 POLICY_BLOCKED error's details.
 * Accepts `details = PolicyDecision` or `details.policy` / `details.policy_decision` / `details.decision`.
 */
export function policyFromError(e: unknown): PolicyDecision | null {
  if (!isApiError(e)) return null;
  const d = e.details as Record<string, unknown>;
  const candidates = [d, d.policy, d.policy_decision, d.decision];
  for (const c of candidates) {
    if (c && typeof c === "object" && "outcome" in c && Array.isArray((c as PolicyDecision).rules)) {
      return c as PolicyDecision;
    }
  }
  return null;
}

/**
 * Synthetic code for a 404 without the error envelope: the backend has no such route (FastAPI's default
 * `{"detail":"Not Found"}`). UI treats it as "not available on this server" and stops polling it.
 */
export const ENDPOINT_MISSING = "ENDPOINT_NOT_AVAILABLE";

/** True when the server does not implement the endpoint at all (not "this record was not found"). */
export function isEndpointMissing(e: unknown): boolean {
  return isApiError(e) && e.code === ENDPOINT_MISSING;
}

function isBareNotFound(json: unknown): boolean {
  if (json === undefined || json === null) return true;
  return typeof json === "object" && !("error" in (json as object)) && "detail" in (json as object);
}

/**
 * Backend adaptation (M9): VALIDATION_ERROR details come as FastAPI's `{ errors: [{ loc: ["body", "email"], msg }] }`.
 * The UI (and the CONTRACT_CHANGES proposal) reads `details.field` (+ `details.field_message`), so derive them from the first
 * body error. Existing `details.field` wins.
 */
function normalizeDetails(code: string, details: Record<string, unknown>): Record<string, unknown> {
  if (code !== "VALIDATION_ERROR" || typeof details.field === "string") return details;
  const errors = details.errors;
  if (!Array.isArray(errors) || errors.length === 0) return details;
  const first = errors[0] as { loc?: unknown; msg?: unknown };
  const loc = Array.isArray(first.loc) ? first.loc : [];
  const field = loc.length > 1 && loc[0] === "body" ? loc[loc.length - 1] : undefined;
  const message = typeof first.msg === "string" ? first.msg.replace(/^Value error, /, "") : undefined;
  return {
    ...details,
    ...(typeof field === "string" ? { field } : {}),
    ...(message ? { field_message: message } : {}),
  };
}

type Query = Record<string, string | number | boolean | null | undefined>;

export type RequestOptions = {
  query?: Query;
  body?: unknown;
  /** Sent as `Idempotency-Key`; reuse the same key when retrying the same action. */
  idempotencyKey?: string;
  /** Skip the Authorization header (login/register/agent-token). */
  anonymous?: boolean;
  /** Override the bearer token (e.g. tests acting as an agent). */
  token?: string;
  signal?: AbortSignal;
};

/** Called on 401 for an authenticated request. Default: redirect to /login. Tests override it. */
let onUnauthorized: () => void = () => {
  const path = window.location.pathname;
  if (path !== "/login" && path !== "/register") {
    window.location.assign(`/login?expired=1&next=${encodeURIComponent(path + window.location.search)}`);
  }
};

export function setUnauthorizedHandler(handler: () => void): void {
  onUnauthorized = handler;
}

function buildUrl(path: string, query?: Query): string {
  if (!path.startsWith("/api/")) throw new Error(`API paths must be relative /api/... (got ${path})`);
  // Same origin as the page (works on localhost, the Vite proxy, and any reverse-proxy origin). Never a hardcoded host.
  const url = new URL(path, window.location.origin);
  if (query) {
    for (const [k, v] of Object.entries(query)) {
      if (v === undefined || v === null || v === "") continue;
      url.searchParams.set(k, String(v));
    }
  }
  return url.toString();
}

export async function apiRequest<T>(method: string, path: string, opts: RequestOptions = {}): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json" };
  const token = opts.token ?? (opts.anonymous ? null : getToken());
  if (token) headers.Authorization = `Bearer ${token}`;
  if (opts.idempotencyKey) headers["Idempotency-Key"] = opts.idempotencyKey;
  let body: string | undefined;
  if (opts.body !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(opts.body);
  }

  const url = buildUrl(path, opts.query);
  let res: Response;
  try {
    res = await fetch(url, { method, headers, body, signal: opts.signal });
  } catch (e) {
    if (e instanceof DOMException && e.name === "AbortError") throw e;
    throw new ApiError(0, "NETWORK_ERROR", "Cannot reach the Aimo Annos API. Check your connection.", null, {
      cause: e instanceof Error ? e.message : String(e),
    });
  }

  recordServerTime(res.headers.get("X-Server-Time"));
  const headerRequestId = res.headers.get("X-Request-ID");

  const text = await res.text();
  let json: unknown = undefined;
  if (text) {
    try {
      json = JSON.parse(text);
    } catch {
      json = undefined;
    }
  }

  if (!res.ok) {
    const env = json as Partial<ErrorEnvelope> | undefined;
    const err =
      env && env.error && typeof env.error.code === "string"
        ? new ApiError(
            res.status,
            env.error.code,
            env.error.message || res.statusText || "Request failed",
            env.request_id ?? headerRequestId,
            normalizeDetails(env.error.code, (env.error.details as Record<string, unknown>) ?? {}),
          )
        : res.status === 404 && isBareNotFound(json)
          ? // Real backend (FastAPI): a route that does not exist answers `{"detail":"Not Found"}` without the
            // envelope. That is "this server does not implement the endpoint" (additive / not-yet-built features).
            new ApiError(
              404,
              ENDPOINT_MISSING,
              `This server does not provide ${method} ${path} yet (404).`,
              headerRequestId,
              { path },
            )
          : new ApiError(
              res.status,
              res.status >= 500 ? "BAD_RESPONSE" : `HTTP_${res.status}`,
              `Unexpected ${res.status} response from the server${res.statusText ? ` (${res.statusText})` : ""}.`,
              headerRequestId,
              {},
            );
    if (res.status === 401 && token) {
      clearSession();
      onUnauthorized();
    }
    throw err;
  }

  if (res.status === 204 || !text) return undefined as T;
  if (json === undefined) {
    throw new ApiError(res.status, "BAD_RESPONSE", "The server returned a non-JSON response.", headerRequestId);
  }
  return json as T;
}

export const api = {
  get: <T>(path: string, opts?: Omit<RequestOptions, "body">) => apiRequest<T>("GET", path, opts),
  post: <T>(path: string, body?: unknown, opts?: Omit<RequestOptions, "body">) =>
    apiRequest<T>("POST", path, { ...opts, body: body ?? {} }),
  put: <T>(path: string, body: unknown, opts?: Omit<RequestOptions, "body">) => apiRequest<T>("PUT", path, { ...opts, body }),
  patch: <T>(path: string, body: unknown, opts?: Omit<RequestOptions, "body">) => apiRequest<T>("PATCH", path, { ...opts, body }),
  delete: <T>(path: string, opts?: Omit<RequestOptions, "body">) => apiRequest<T>("DELETE", path, opts),
};

/** New random Idempotency-Key (uuid v4). */
export function newIdempotencyKey(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) return crypto.randomUUID();
  return "idk-" + Math.random().toString(36).slice(2) + Date.now().toString(36);
}
