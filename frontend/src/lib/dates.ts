// Date helpers. All calendar logic is in Europe/Helsinki (the backend's zone).
// Display formatting only; never used to decide delivery status or policy.

export const APP_TIME_ZONE = "Europe/Helsinki";

const isoDateFmt = new Intl.DateTimeFormat("en-CA", {
  timeZone: APP_TIME_ZONE,
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
});

/** Calendar date (YYYY-MM-DD) of an instant in Helsinki. */
export function helsinkiDate(at: Date | number): string {
  return isoDateFmt.format(typeof at === "number" ? new Date(at) : at);
}

/** Adds days to a YYYY-MM-DD date string (pure calendar math). */
export function addDays(date: string, days: number): string {
  const d = new Date(`${date}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + days);
  return d.toISOString().slice(0, 10);
}

/** 0 = Sunday … 6 = Saturday for a YYYY-MM-DD date. */
export function weekdayOf(date: string): number {
  return new Date(`${date}T00:00:00Z`).getUTCDay();
}

/**
 * Default requested delivery date: the next weekday (Mon–Fri) ≥ serverNow + 1 day, in Helsinki.
 * Pass `serverNow()` / `useServerNow()`, never the raw browser clock.
 * Sat 2026-11-14 → Mon 2026-11-16.
 */
export function defaultDeliveryDate(serverNow: Date | number): string {
  let d = addDays(helsinkiDate(serverNow), 1);
  while (weekdayOf(d) === 0 || weekdayOf(d) === 6) d = addDays(d, 1);
  return d;
}

// Display formats follow the portal (DESIGN_VALIO §2.4): Finnish day.month.year with English weekday names.
const WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

function ymd(date: string): { y: number; m: number; d: number } | null {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(date);
  if (!m) return null;
  return { y: Number(m[1]), m: Number(m[2]), d: Number(m[3]) };
}

/** "16.11.2026" for a YYYY-MM-DD date. */
export function formatDate(date: string | null | undefined): string {
  if (!date) return "—";
  const p = ymd(date);
  return p ? `${p.d}.${p.m}.${p.y}` : date;
}

/** "16.11." (short date, mobile header) for a YYYY-MM-DD date. */
export function formatShortDate(date: string | null | undefined): string {
  if (!date) return "—";
  const p = ymd(date);
  return p ? `${p.d}.${p.m}.` : date;
}

/** "Mon 16.11.2026" for a YYYY-MM-DD date. */
export function formatDayDate(date: string | null | undefined): string {
  if (!date) return "—";
  const p = ymd(date);
  if (!p) return date;
  const wd = WEEKDAYS[weekdayOf(`${date.slice(0, 10)}`)];
  return `${wd} ${p.d}.${p.m}.${p.y}`;
}

/** "14.11.2026 at 06:38" for an ISO timestamp, shown in Helsinki time. */
export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return `${formatDate(helsinkiDate(d))} at ${formatTime(iso)}`;
}

/** "10:00" for an ISO timestamp in Helsinki time. */
export function formatTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return new Intl.DateTimeFormat("en-GB", { hour: "2-digit", minute: "2-digit", hour12: false, timeZone: APP_TIME_ZONE }).format(d);
}

/** "2h 14m", "14m 05s", "45s"; negative → "0s". */
export function formatDuration(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000));
  const d = Math.floor(total / 86400);
  const h = Math.floor((total % 86400) / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  if (d > 0) return `${d}d ${h}h`;
  if (h > 0) return `${h}h ${m}m`;
  if (m > 0) return `${m}m ${String(s).padStart(2, "0")}s`;
  return `${s}s`;
}

/** "5 min ago" style relative time against a given now (use server now). */
export function formatRelative(iso: string | null | undefined, nowMs: number): string {
  if (!iso) return "never";
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return iso;
  const diff = Math.round((nowMs - t) / 1000);
  if (diff < 0) return "just now";
  if (diff < 60) return `${diff}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)} min ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)} h ago`;
  return `${Math.floor(diff / 86400)} d ago`;
}

/** "+02:00" / "+03:00": Helsinki UTC offset on a YYYY-MM-DD date (around noon). */
export function helsinkiOffset(date: string): string {
  const parts = new Intl.DateTimeFormat("en-US", { timeZone: APP_TIME_ZONE, timeZoneName: "longOffset" }).formatToParts(new Date(`${date}T10:00:00Z`));
  const name = parts.find((p) => p.type === "timeZoneName")?.value ?? "GMT+02:00";
  const m = /GMT([+-]\d{2}:\d{2})/.exec(name);
  return m ? m[1]! : "+02:00";
}

/** End of a Helsinki calendar day as ISO with offset: "2026-12-31" → "2026-12-31T23:59:00+02:00" (e.g. agent expiry). */
export function helsinkiEndOfDayIso(date: string): string {
  return `${date}T23:59:00${helsinkiOffset(date)}`;
}
