import { formatEur } from "../lib/format";

/** EUR amount from the API (never computed in the UI), tabular-nums. `vat` adds "excl. VAT"/"incl. VAT". */
export function Money({ value, vat, className = "" }: { value: number | null | undefined; vat?: "excl" | "incl"; className?: string }) {
  return (
    <span className={`tabular-nums whitespace-nowrap ${className}`}>
      {formatEur(value)}
      {vat && value != null && <span className="ml-1 text-[0.8em] font-normal text-ink-muted">{vat === "excl" ? "excl. VAT" : "incl. VAT"}</span>}
    </span>
  );
}
