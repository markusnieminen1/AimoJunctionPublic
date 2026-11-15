// Mock-mode persistence: snapshot the in-memory mock database + mock clock to sessionStorage, so a page reload
// (F5) in mock mode keeps the demo state and the mock login token. Browser worker only (browser.ts wires it);
// Vitest never calls it, so tests stay isolated. sessionStorage = per tab, cleared when the tab closes.
import { mockNowMs, setMockNow } from "./clock";
import { db, emptyMockDb, replaceMockDb, type MockDb } from "./db";

export const MOCK_STATE_KEY = "aimoannos.mockdb";
/** Bump when the MockDb shape or the seed changes in a way old snapshots cannot satisfy. */
export const MOCK_STATE_VERSION = 1;

type Snapshot = { v: number; saved_real_ms: number; mock_now_ms: number; db: unknown };

const MAP_TAG = "__map__";

function replacer(_key: string, value: unknown) {
  return value instanceof Map ? { [MAP_TAG]: Array.from(value.entries()) } : value;
}

function reviver(_key: string, value: unknown) {
  if (value && typeof value === "object" && !Array.isArray(value) && MAP_TAG in value) {
    const entries = (value as Record<string, unknown>)[MAP_TAG];
    if (Array.isArray(entries)) return new Map(entries as [unknown, unknown][]);
  }
  return value;
}

/** Serialize the current mock database and clock. */
export function serializeMockState(nowReal = Date.now()): string {
  const snap: Snapshot = { v: MOCK_STATE_VERSION, saved_real_ms: nowReal, mock_now_ms: mockNowMs(), db };
  return JSON.stringify(snap, replacer);
}

/**
 * Restore a snapshot. The mock clock continues from the saved mock time plus the real time that passed
 * (it runs in real time, like before the reload). Returns false (and changes nothing) for missing/invalid/old data.
 */
export function restoreMockState(raw: string | null, nowReal = Date.now()): boolean {
  if (!raw) return false;
  try {
    const snap = JSON.parse(raw, reviver) as Snapshot;
    if (!snap || snap.v !== MOCK_STATE_VERSION || typeof snap.mock_now_ms !== "number" || !snap.db || typeof snap.db !== "object") return false;
    const base = emptyMockDb();
    const restored = { ...base, ...(snap.db as Partial<MockDb>) } as MockDb;
    // Every Map field must come back as a Map, every array as an array; otherwise ignore the snapshot.
    for (const k of Object.keys(base) as (keyof MockDb)[]) {
      const want = base[k];
      const got = restored[k];
      if (want instanceof Map && !(got instanceof Map)) return false;
      if (Array.isArray(want) && !Array.isArray(got)) return false;
    }
    replaceMockDb(restored);
    const elapsed = Math.max(0, nowReal - (typeof snap.saved_real_ms === "number" ? snap.saved_real_ms : nowReal));
    setMockNow(snap.mock_now_ms + elapsed);
    return true;
  } catch {
    return false;
  }
}

function storage(): Storage | null {
  try {
    return window.sessionStorage;
  } catch {
    return null;
  }
}

export function saveMockState(s: Storage | null = storage()): void {
  try {
    s?.setItem(MOCK_STATE_KEY, serializeMockState());
  } catch {
    /* quota / private mode: mock state stays in memory only */
  }
}

export function loadMockState(s: Storage | null = storage()): boolean {
  try {
    return restoreMockState(s?.getItem(MOCK_STATE_KEY) ?? null);
  } catch {
    return false;
  }
}

export function clearMockState(s: Storage | null = storage()): void {
  try {
    s?.removeItem(MOCK_STATE_KEY);
  } catch {
    /* ignore */
  }
}

let timer: ReturnType<typeof setTimeout> | null = null;

/** Debounced save (many mocked responses arrive in bursts). */
export function scheduleMockSave(delayMs = 200): void {
  if (timer) return;
  timer = setTimeout(() => {
    timer = null;
    saveMockState();
  }, delayMs);
}

/** Save right away (page hide / dev helpers), cancelling a pending debounced save. */
export function flushMockSave(): void {
  if (timer) {
    clearTimeout(timer);
    timer = null;
  }
  saveMockState();
}
