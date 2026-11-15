// Mock server clock: starts at the demo time 2026-11-14T10:00:00+02:00 (Sat) and advances in real time.
// Dev helpers can jump it forward (e.g. to expire a quote). Sent as X-Server-Time on every mock response.

export const MOCK_EPOCH_ISO = "2026-11-14T10:00:00+02:00";
const EPOCH = Date.parse(MOCK_EPOCH_ISO);
let realStart = Date.now();
let manualMs = 0;

export function mockNowMs(): number {
  return EPOCH + (Date.now() - realStart) + manualMs;
}

export function advanceMockClock(ms: number): void {
  manualMs += ms;
}

export function resetMockClock(): void {
  realStart = Date.now();
  manualMs = 0;
}

/** Restore the clock (mock persistence): from now on mockNowMs() continues from `ms` in real time. */
export function setMockNow(ms: number): void {
  realStart = Date.now();
  manualMs = ms - EPOCH;
}

/** ISO 8601 with the Helsinki winter offset (+02:00), second precision. */
export function isoAt(ms: number): string {
  return new Date(ms + 2 * 3600_000).toISOString().slice(0, 19) + "+02:00";
}
