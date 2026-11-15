// Diet-safe substitutes recovery panel (DESIGN.md §6D). Shown when a quote line is PARTIAL / CUTOFF_PASSED / NOT_DELIVERABLE.
// Calls the ADDITIVE POST /api/products/substitutes; a 404 (endpoint missing on the backend) shows a muted note instead.
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { ArrowRightLeft, LifeBuoy, Search } from "lucide-react";
import { isEndpointMissing, toApiError } from "../../api/client";
import { useSubstitutes } from "../../api/hooks";
import type { QuoteLine, SubstituteItem } from "../../api/types";
import { useStrings } from "../../i18n";
import { formatDayDate } from "../../lib/dates";
import { formatNumber } from "../../lib/format";
import { Button } from "../Button";
import { CO2Value } from "../CO2Value";
import { DeliveryStatusBadge } from "../DeliveryStatusBadge";
import { DietChips } from "../DietChips";
import { ErrorPanel } from "../ErrorPanel";
import { Money } from "../Money";
import { ProductImage } from "../ProductImage";
import { Skeleton } from "../Skeleton";
import { Stripe } from "../Stripe";
import { TemperatureBadge } from "../TemperatureBadge";
import { SkuChip } from "../product/ProductBits";
import { UnknownAllergenMarker } from "../catalog/ProductCard";
import { allergenBuckets } from "../../domain/allergens";
import { hasSupplyRisk } from "./LineBits";

export type SwapHandler = (fromProductId: string, substitute: SubstituteItem) => void;

function SubstituteCard({ item, onSwap, from }: { item: SubstituteItem; from: QuoteLine; onSwap?: SwapHandler }) {
  const strings = useStrings();
  const { product: p, line } = item;
  return (
    <div className="flex flex-col rounded border border-line bg-white p-4" data-testid="substitute-card">
      <div className="flex items-start gap-3">
        <ProductImage src={p.image_url} alt="" category={p.category} className="h-12 w-12 shrink-0 [&_svg]:h-7 [&_svg]:w-7" />
        <div className="min-w-0 flex-1">
          <Link to={`/products/${encodeURIComponent(p.id)}`} className="line-clamp-2 text-[14px] font-semibold leading-5 text-ink hover:text-navy hover:underline">
            {p.name}
          </Link>
          <div className="mt-1">
            <SkuChip id={p.id} />
          </div>
        </div>
      </div>
      <div className="mt-2 flex min-w-0 flex-wrap items-center gap-1.5">
        <TemperatureBadge code={p.temperature_condition} />
        <DietChips product={p} />
        {/* "Diet-safe" is the server's diet match; missing allergen data is still flagged, never implied safe. */}
        {allergenBuckets(p).unknown && <UnknownAllergenMarker />}
      </div>
      <div className="mt-3 flex flex-wrap items-baseline justify-between gap-2 border-t border-line/70 pt-2.5 text-xs text-ink-secondary">
        <span className="tabular-nums">
          {formatNumber(line.quantity)} {line.sales_unit} × <Money value={line.unit_price_eur} />
        </span>
        <Money value={line.line_total_eur} vat="excl" className="text-sm font-bold text-navy" />
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-x-2.5 gap-y-1 text-xs text-ink-secondary">
        <DeliveryStatusBadge status={line.delivery_status} probability={line.on_time_probability} />
        {line.available_date && <span className="tabular-nums text-ink-muted">{strings.quote.available} {formatDayDate(line.available_date)}</span>}
        <CO2Value kg={line.co2_kg} basis={line.co2_basis} className="text-xs" />
      </div>
      {item.trade_offs.length > 0 && (
        <ul className="mt-2 flex flex-wrap gap-1">
          {item.trade_offs.map((t) => (
            <li key={t} className="rounded border border-line bg-primary-soft px-1.5 py-0.5 text-[11px] font-medium text-navy">
              {t}
            </li>
          ))}
        </ul>
      )}
      <p className="mt-2 text-[11.5px] text-ink-muted">
        <span className="font-semibold text-ink-secondary">{strings.quote.subsWhy}:</span> {item.reason}
      </p>
      {onSwap && (
        <Button size="sm" variant="outline" className="mt-3 w-full" icon={<ArrowRightLeft className="h-3.5 w-3.5" strokeWidth={1.75} />} onClick={() => onSwap(from.product_id, item)} aria-label={`${strings.quote.subsSwap}: ${p.name}`}>
          {strings.quote.subsSwap}
        </Button>
      )}
    </div>
  );
}

function LineSubstitutes({ line, date, diet, onSwap }: { line: QuoteLine; date: string; diet?: string | null; onSwap?: SwapHandler }) {
  const strings = useStrings();
  const subs = useSubstitutes({ silent: true });
  const { mutate } = subs;
  const body = { product_id: line.product_id, quantity: line.quantity, requested_delivery_date: date, ...(diet ? { diet } : {}) };
  useEffect(() => {
    mutate({ product_id: line.product_id, quantity: line.quantity, requested_delivery_date: date, ...(diet ? { diet } : {}) });
  }, [mutate, line.product_id, line.quantity, date, diet]);

  const err = subs.isError ? toApiError(subs.error) : null;
  return (
    <div className="mt-3">
      <p className="mb-1.5 text-xs font-semibold text-ink">{strings.quote.subsFor(line.product_name)}</p>
      {subs.isPending || subs.isIdle ? (
        <div className="grid gap-2 sm:grid-cols-2">
          <Skeleton className="h-36 w-full" />
          <Skeleton className="h-36 w-full" />
        </div>
      ) : err ? (
        isEndpointMissing(err) || (err.status === 404 && err.code !== "UNKNOWN_PRODUCT" && err.code !== "NOT_FOUND") ? (
          <p className="text-xs text-ink-muted">{strings.quote.subsUnavailable}</p>
        ) : (
          <ErrorPanel error={subs.error} onRetry={() => mutate(body)} />
        )
      ) : (subs.data?.items.length ?? 0) === 0 ? (
        <p className="rounded-md border border-dashed border-line-strong bg-white px-3 py-2 text-xs text-ink-secondary">{strings.quote.subsNone}</p>
      ) : (
        <div className="grid gap-3 sm:grid-cols-2 2xl:grid-cols-3">
          {(subs.data?.items ?? []).map((it) => (
            <SubstituteCard key={it.product.id} item={it} from={line} onSwap={onSwap} />
          ))}
        </div>
      )}
    </div>
  );
}

/** Callout + per-line substitute comparison cards. Renders nothing when no line has a supply-risk flag. */
export function SubstitutesPanel({ lines, requestedDeliveryDate, diet, onSwap, className = "" }: {
  lines: QuoteLine[];
  requestedDeliveryDate: string;
  /** Cart diet requirement (comma list); substitutes must meet it on top of the original's diets. */
  diet?: string | null;
  onSwap?: SwapHandler;
  className?: string;
}) {
  const strings = useStrings();
  const risky = lines.filter(hasSupplyRisk);
  const [open, setOpen] = useState(false);
  if (risky.length === 0) return null;
  return (
    <section aria-label={strings.quote.subsTitle} className={className}>
      <Stripe
        kind="neutral"
        title={strings.quote.subsTitle}
        icon={<LifeBuoy className="h-4 w-4 text-navy" strokeWidth={1.75} />}
      >
        <p>{strings.quote.subsBody}</p>
        {!open && (
          <Button size="sm" variant="outline" className="mt-3" icon={<Search className="h-3.5 w-3.5" strokeWidth={1.75} />} onClick={() => setOpen(true)}>
            {strings.quote.subsFind}
          </Button>
        )}
        {open && risky.map((l) => <LineSubstitutes key={`${l.product_id}-${l.quantity}`} line={l} date={requestedDeliveryDate} diet={diet} onSwap={onSwap} />)}
      </Stripe>
    </section>
  );
}
