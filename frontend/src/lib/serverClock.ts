// Server clock: every API response carries `X-Server-Time`; we keep `offset = server - browser`
// so countdowns follow the backend clock (which may be frozen with APP_NOW). AGENTS.md rule 14.

type Listener = () => void;

let offsetMs = 0;
let synced = false;
const listeners = new Set<Listener>();

/** Record an `X-Server-Time` header value. Ignores missing/invalid values. */
export function recordServerTime(header: string | null | undefined, receivedAtMs: number = Date.now()): void {
  if (!header) return;
  const serverMs = Date.parse(header);
  if (Number.isNaN(serverMs)) return;
  const next = serverMs - receivedAtMs;
  // Ignore sub-second jitter so subscribers do not re-render on every response.
  if (synced && Math.abs(next - offsetMs) < 1000) return;
  offsetMs = next;
  synced = true;
  listeners.forEach((l) => l());
}

/** Current server-clock offset in ms (server − browser). */
export function getServerOffsetMs(): number {
  return offsetMs;
}

/** True once at least one response with X-Server-Time has been seen. */
export function isServerClockSynced(): boolean {
  return synced;
}

/** Server "now" as epoch ms. */
export function serverNowMs(): number {
  return Date.now() + offsetMs;
}

/** Server "now" as a Date. */
export function serverNow(): Date {
  return new Date(serverNowMs());
}

export function subscribeServerClock(listener: Listener): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/** Test helper. */
export function resetServerClock(): void {
  offsetMs = 0;
  synced = false;
  listeners.forEach((l) => l());
}
