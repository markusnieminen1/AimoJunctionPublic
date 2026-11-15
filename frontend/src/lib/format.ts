// Number formatting for values returned by the API (display only; never compute money in the UI).
// The portal shows Finnish number formats even in English (DESIGN_VALIO §2.4): "1 010,00 €", "2,53 €", "98 %".
// Group separators and the space before "€" are no-break spaces (U+00A0), so amounts never wrap.

const LOCALE = "fi-FI";
const eur = new Intl.NumberFormat(LOCALE, { style: "currency", currency: "EUR", minimumFractionDigits: 2, maximumFractionDigits: 2 });
const eurWhole = new Intl.NumberFormat(LOCALE, { style: "currency", currency: "EUR", maximumFractionDigits: 0 });
const numFmt = new Map<number, Intl.NumberFormat>();
function num(maxFractionDigits: number): Intl.NumberFormat {
  let f = numFmt.get(maxFractionDigits);
  if (!f) {
    f = new Intl.NumberFormat(LOCALE, { maximumFractionDigits: maxFractionDigits });
    numFmt.set(maxFractionDigits, f);
  }
  return f;
}

/** "1 010,00 €"; null → "—". */
export function formatEur(v: number | null | undefined): string {
  return v == null || Number.isNaN(v) ? "—" : eur.format(v);
}

/** "1 000" / "1,5". */
export function formatNumber(v: number | null | undefined, maxFractionDigits = 2): string {
  if (v == null || Number.isNaN(v)) return "—";
  return num(maxFractionDigits).format(v);
}

/** 0..1 → "98 %". */
export function formatPercent(v: number | null | undefined): string {
  return v == null || Number.isNaN(v) ? "—" : `${Math.round(v * 100)} %`;
}

/** "1 100 kg CO₂e" (rounded to 1 decimal under 10: "1,2 kg CO₂e"). */
export function formatCo2(kg: number | null | undefined): string {
  if (kg == null || Number.isNaN(kg)) return "—";
  return `${formatNumber(kg, kg < 10 ? 1 : 0)} kg CO₂e`;
}

/** "500 €" for whole euros, "499,50 €" otherwise (limits and policy text; display only). */
export function formatEurCompact(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v)) return "—";
  return Number.isInteger(v) ? eurWhole.format(v) : formatEur(v);
}
