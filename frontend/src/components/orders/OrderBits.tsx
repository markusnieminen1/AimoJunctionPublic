// Order list table / mobile cards and the order-lines table. All figures exactly as returned by the API.
import { shortId } from "../../lib/ids";
import { Link, useNavigate } from "react-router-dom";
import { ChevronRight } from "lucide-react";
import type { Order, QuoteLine } from "../../api/types";
import { useStrings } from "../../i18n";
import { formatDateTime, formatDayDate } from "../../lib/dates";
import { formatNumber } from "../../lib/format";
import { ActorPill } from "../ActorPill";
import { CO2Value } from "../CO2Value";
import { DeliveryStatusBadge } from "../DeliveryStatusBadge";
import { FlagChips } from "../FlagChips";
import { Money } from "../Money";
import { StatusBadge } from "../StatusBadge";
import { SkuChip } from "../product/ProductBits";
import { QuoteLineItem } from "../quote/LineBits";

export const thClass = "px-3 py-2 text-left text-[11.5px] font-semibold uppercase tracking-[0.04em] text-ink-muted";

export const orderHref = (id: string) => `/orders/${encodeURIComponent(id)}`;

/** Desktop order table (md+). `compact` drops the delivery and CO₂ columns and folds "created" into the order cell (dashboard). */
export function OrdersTable({ orders, compact = false }: { orders: Order[]; compact?: boolean }) {
  const strings = useStrings();
  const t = strings.orders;
  const navigate = useNavigate();
  return (
    <table className="w-full text-sm" data-testid="orders-table">
      <thead className="border-b border-line bg-surface-muted">
        <tr>
          <th className={thClass}>{t.colOrder}</th>
          {!compact && <th className={thClass}>{t.colCreated}</th>}
          <th className={thClass}>{t.colCreatedBy}</th>
          <th className={thClass}>{t.colStatus}</th>
          {!compact && <th className={thClass}>{t.colDelivery}</th>}
          <th className={`${thClass} text-right`}>{t.colTotal}</th>
          {!compact && <th className={`${thClass} text-right`}>{t.colCo2}</th>}
          <th className="w-6" />
        </tr>
      </thead>
      <tbody className="divide-y divide-line">
        {orders.map((o) => (
          <tr key={o.id} className="group cursor-pointer hover:bg-surface-muted" onClick={() => navigate(orderHref(o.id))}>
            <td className="px-3 py-2.5">
              <Link to={orderHref(o.id)} title={o.id} className="font-mono-code whitespace-nowrap text-[12px] font-semibold text-ink hover:text-primary" onClick={(e) => e.stopPropagation()}>
                {shortId(o.id)}
              </Link>
              <div className="tabular-nums whitespace-nowrap text-[11.5px] text-ink-muted">
                {compact ? `${formatDateTime(o.created_at)} · ${t.lines(o.lines.length)}` : t.lines(o.lines.length)}
              </div>
            </td>
            {!compact && <td className="tabular-nums whitespace-nowrap px-3 py-2.5 text-xs text-ink-secondary">{formatDateTime(o.created_at)}</td>}
            <td className="max-w-[260px] px-3 py-2.5">
              <ActorPill actor={o.created_by} compact={compact} />
            </td>
            <td className="px-3 py-2.5">
              <StatusBadge status={o.status} />
            </td>
            {!compact && <td className="tabular-nums whitespace-nowrap px-3 py-2.5 text-xs text-ink-secondary">{formatDayDate(o.requested_delivery_date)}</td>}
            <td className="px-3 py-2.5 text-right">
              <Money value={o.total_excl_vat_eur} className="font-semibold text-ink" />
            </td>
            {!compact && (
              <td className="px-3 py-2.5 text-right">
                <CO2Value kg={o.total_co2_kg} basis={strings.quote.co2TotalBasis} className="text-xs" />
              </td>
            )}
            <td className="pr-3">
              <ChevronRight className="h-4 w-4 text-ink-muted group-hover:text-primary" strokeWidth={1.75} />
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

/** Mobile (< md) order cards. */
export function OrderCards({ orders }: { orders: Order[] }) {
  const strings = useStrings();
  const t = strings.orders;
  return (
    <ul className="divide-y divide-line" data-testid="order-cards">
      {orders.map((o) => (
        <li key={o.id}>
          <Link to={orderHref(o.id)} className="block px-4 py-3 active:bg-surface-muted">
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <StatusBadge status={o.status} />
                <div className="mt-1.5 max-w-full">
                  <ActorPill actor={o.created_by} stacked />
                </div>
              </div>
              <div className="shrink-0 text-right">
                <Money value={o.total_excl_vat_eur} className="text-[15px] font-bold text-navy" />
                <div className="text-[11px] text-ink-muted">{strings.common.exclVat}</div>
              </div>
            </div>
            <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-ink-secondary">
              <span className="font-mono-code text-[11px] text-ink-muted" title={o.id}>
                {shortId(o.id)}
              </span>
              <span className="tabular-nums">{formatDateTime(o.created_at)}</span>
              <span className="tabular-nums">
                {t.colDelivery} {formatDayDate(o.requested_delivery_date)}
              </span>
              <CO2Value kg={o.total_co2_kg} basis={strings.quote.co2TotalBasis} className="text-xs" />
            </div>
          </Link>
        </li>
      ))}
    </ul>
  );
}

/** Order lines: a table on md+, compact line blocks on phones. `highlight` = product ids to mark amber. */
export function OrderLinesTable({ lines, highlight }: { lines: QuoteLine[]; highlight?: Set<string> }) {
  const strings = useStrings();
  const t = strings.orders;
  return (
    <>
      <div className="hidden overflow-x-auto md:block">
        <table className="w-full text-sm" data-testid="order-lines">
          <thead className="border-b border-line bg-surface-muted">
            <tr>
              <th className={thClass}>{t.colProduct}</th>
              <th className={`${thClass} text-right`}>{t.colQty}</th>
              <th className={`${thClass} whitespace-nowrap text-right`}>{t.colUnit}</th>
              <th className={`${thClass} whitespace-nowrap text-right`}>{t.colLine}</th>
              <th className={thClass}>{t.colDelivery}</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-line">
            {lines.map((l, i) => {
              const hl = highlight?.has(l.product_id);
              return (
                <tr key={`${l.product_id}-${i}`} className={hl ? "bg-warning-bg" : ""} data-changed={hl ? "true" : undefined}>
                  <td className={`min-w-[200px] px-3 py-2.5 align-top ${hl ? "border-l-4 border-l-warning" : ""}`}>
                    <Link to={`/products/${encodeURIComponent(l.product_id)}`} className="line-clamp-2 font-semibold text-ink hover:text-primary">
                      {l.product_name}
                    </Link>
                    <div className="mt-1 flex flex-wrap items-center gap-1.5">
                      <SkuChip id={l.product_id} />
                      <FlagChips flags={l.flags} co2Basis={l.co2_basis} />
                    </div>
                  </td>
                  <td className="tabular-nums whitespace-nowrap px-3 py-2.5 text-right align-top">
                    {formatNumber(l.quantity)} {l.sales_unit}
                  </td>
                  <td className="whitespace-nowrap px-3 py-2.5 text-right align-top">
                    {l.unit_price_eur == null ? <span className="text-xs font-semibold text-danger">{strings.quote.noPrice}</span> : <Money value={l.unit_price_eur} />}
                  </td>
                  <td className="px-3 py-2.5 text-right align-top">
                    <Money value={l.line_total_eur} className="font-semibold text-ink" />
                  </td>
                  <td className="px-3 py-2.5 align-top">
                    <DeliveryStatusBadge status={l.delivery_status} probability={l.on_time_probability} />
                    {l.available_date && <div className="tabular-nums mt-1 whitespace-nowrap text-[11.5px] text-ink-muted">{strings.quote.available} {formatDayDate(l.available_date)}</div>}
                    <div className="mt-1">
                      <CO2Value kg={l.co2_kg} basis={l.co2_basis} className="text-[11.5px]" />
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <ul className="divide-y divide-line md:hidden">
        {lines.map((l, i) => (
          <QuoteLineItem key={`${l.product_id}-${i}`} line={l} highlight={highlight?.has(l.product_id)} />
        ))}
      </ul>
    </>
  );
}
