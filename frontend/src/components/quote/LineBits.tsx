// Display pieces for a QuoteLine (product check result, quote lines, substitutes). Everything shown comes from the API.
import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { Clock } from "lucide-react";
import type { LineFlag, QuoteLine } from "../../api/types";
import { useStrings } from "../../i18n";
import { formatDateTime, formatDayDate } from "../../lib/dates";
import { formatNumber } from "../../lib/format";
import { CO2Value } from "../CO2Value";
import { Countdown } from "../Countdown";
import { DeliveryStatusBadge } from "../DeliveryStatusBadge";
import { FlagChips } from "../FlagChips";
import { Money } from "../Money";
import { SkuChip } from "../product/ProductBits";

/** Flags that trigger the diet-safe substitutes callout (DESIGN.md §6D). */
export const SUPPLY_RISK_FLAGS: LineFlag[] = ["PARTIAL", "CUTOFF_PASSED", "NOT_DELIVERABLE"];

export function hasSupplyRisk(line: Pick<QuoteLine, "flags">): boolean {
  return line.flags.some((f) => SUPPLY_RISK_FLAGS.includes(f));
}

/** "Sun 15 Nov, 17:00 · Cut-off in 1d 7h" (server-time countdown). */
export function CutoffInfo({ closingTime }: { closingTime: string | null }) {
  const strings = useStrings();
  if (!closingTime) return <span className="text-xs text-ink-muted">—</span>;
  return (
    <span className="tabular-nums inline-flex flex-wrap items-center gap-x-1.5 text-xs text-ink-secondary">
      <span>{formatDateTime(closingTime)}</span>
      <span aria-hidden="true">·</span>
      <Countdown to={closingTime} prefix={strings.product.cutoffIn} expiredLabel={strings.product.cutoffPassed} />
    </span>
  );
}

/** Stock state from the API: "384 in stock" / "Unlimited stock" / "Supplier qty unknown". */
export function StockInfo({ line }: { line: Pick<QuoteLine, "available_qty_state" | "available_amount" | "sales_unit" | "logistic_model"> }) {
  const strings = useStrings();
  if (!line.available_qty_state) return <span className="text-xs text-ink-muted">—</span>;
  const text =
    line.available_qty_state === "FINITE" && line.available_amount != null
      ? `${formatNumber(line.available_amount)} ${line.sales_unit} ${strings.product.stockStates.FINITE}`
      : strings.product.stockStates[line.available_qty_state] ?? line.available_qty_state;
  return (
    <span className="tabular-nums text-xs text-ink-secondary">
      {text}
      {line.logistic_model && <span className="text-ink-muted"> · {line.logistic_model}</span>}
    </span>
  );
}

/** Key/value grid for one line: unit price, line total, delivery, available date, cut-off, stock, CO₂, flags. */
export function LineFacts({ line, className = "" }: { line: QuoteLine; className?: string }) {
  const strings = useStrings();
  const row = "flex items-start justify-between gap-3 py-1.5";
  const k = "text-xs text-ink-muted";
  return (
    <div className={`divide-y divide-[#F2F6F8] ${className}`}>
      <div className={row}>
        <span className={k}>{strings.product.unitPrice}</span>
        <span className="text-sm font-semibold text-ink">
          {line.unit_price_eur == null ? <span className="text-danger">{strings.quote.noPrice}</span> : <Money value={line.unit_price_eur} />}
          <span className="text-xs font-normal text-ink-muted"> / {line.sales_unit} {strings.common.exclVat}</span>
        </span>
      </div>
      <div className={row}>
        <span className={k}>
          {strings.product.lineTotal} <span className="tabular-nums">({formatNumber(line.quantity)} {line.sales_unit})</span>
        </span>
        <Money value={line.line_total_eur} vat="excl" className="text-base font-bold text-navy" />
      </div>
      <div className={row}>
        <span className={k}>{strings.quote.delivery}</span>
        <DeliveryStatusBadge status={line.delivery_status} probability={line.on_time_probability} />
      </div>
      <div className={row}>
        <span className={k}>{strings.product.availableDate}</span>
        <span className="tabular-nums text-xs font-medium text-ink">{formatDayDate(line.available_date)}</span>
      </div>
      <div className={row}>
        <span className={k}>{strings.product.cutoff}</span>
        <CutoffInfo closingTime={line.closing_time} />
      </div>
      <div className={row}>
        <span className={k}>{strings.product.stock}</span>
        <StockInfo line={line} />
      </div>
      <div className={row}>
        <span className={k}>CO₂e</span>
        <CO2Value kg={line.co2_kg} basis={line.co2_basis} className="text-xs" />
      </div>
      {line.flags.length > 0 && (
        <div className="pt-2">
          <FlagChips flags={line.flags} co2Basis={line.co2_basis} />
        </div>
      )}
    </div>
  );
}

/** Compact quote line block (fits a narrow sticky panel or a chat card). */
export function QuoteLineItem({ line, highlight = false, note }: { line: QuoteLine; highlight?: boolean; note?: ReactNode }) {
  const strings = useStrings();
  return (
    <li className={`px-3 py-2.5 ${highlight ? "border-l-4 border-l-warning bg-warning-bg" : ""}`} data-testid="quote-line" data-changed={highlight ? "true" : undefined}>
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <Link to={`/products/${encodeURIComponent(line.product_id)}`} className="line-clamp-2 text-[13px] font-semibold leading-5 text-ink hover:text-primary">
            {line.product_name}
          </Link>
          <div className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-xs text-ink-secondary">
            <SkuChip id={line.product_id} />
            <span className="tabular-nums">
              {formatNumber(line.quantity)} {line.sales_unit} × {line.unit_price_eur == null ? <span className="font-semibold text-danger">{strings.quote.noPrice}</span> : <Money value={line.unit_price_eur} />}
            </span>
          </div>
        </div>
        <Money value={line.line_total_eur} className="shrink-0 text-sm font-semibold text-ink" />
      </div>
      <div className="mt-1.5 flex flex-wrap items-center gap-x-2.5 gap-y-1">
        <DeliveryStatusBadge status={line.delivery_status} probability={line.on_time_probability} />
        {line.closing_time && (
          <span
            className="inline-flex items-center gap-1 text-[11.5px] text-ink-muted"
            title={`${strings.product.cutoff}: ${formatDateTime(line.closing_time)}`}
          >
            <Clock className="h-3.5 w-3.5 shrink-0" strokeWidth={1.75} aria-hidden />
            <span className="sr-only">{strings.product.cutoff}: {formatDateTime(line.closing_time)}</span>
          </span>
        )}
        {line.available_date && (
          <span className="tabular-nums text-[11.5px] text-ink-muted">
            {strings.quote.available} {formatDayDate(line.available_date)}
          </span>
        )}
        <CO2Value kg={line.co2_kg} basis={line.co2_basis} className="text-[11.5px]" />
      </div>
      {line.flags.length > 0 && <FlagChips flags={line.flags} co2Basis={line.co2_basis} className="mt-1.5" />}
      {note && <div className="mt-1.5 text-[11.5px] text-warning">{note}</div>}
    </li>
  );
}
