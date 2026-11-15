// Reusable quote view + Place-order flow (cart page and the M8 assistant quote card).
// Shows exactly what POST /api/quotes returned: lines, totals, CO₂ with basis, expiry countdown (server time), policy preview.
// Place order → POST /api/orders with one Idempotency-Key per quote (reused on double click / retry).
import { useEffect, useRef, useState, type ReactNode } from "react";
import { AlertTriangle, RefreshCw, Send, ShieldX } from "lucide-react";
import { policyFromError, toApiError } from "../../api/client";
import { useIdempotencyKey, usePlaceOrder, useQuote } from "../../api/hooks";
import type { Order, Quote, QuoteLine } from "../../api/types";
import { useStrings, type Strings } from "../../i18n";
import { formatDayDate } from "../../lib/dates";
import { useServerNow } from "../../lib/useServerNow";
import { Button, ButtonLink } from "../Button";
import { CO2Value } from "../CO2Value";
import { CopyChip } from "../CopyChip";
import { Countdown } from "../Countdown";
import { NegotiatedSavingsBadge } from "../NegotiatedSavingsBadge";
import { DeliveryStatusBadge } from "../DeliveryStatusBadge";
import { ErrorPanel } from "../ErrorPanel";
import { inputClass } from "../FormField";
import { Money } from "../Money";
import { DietRequirementBadge } from "../cart/DietRequirementPicker";
import { formatVatRates } from "../orders/OrderBlock";
import { PolicyBanner } from "../PolicyBanner";
import { QuoteLineItem } from "./LineBits";
import { OrderResultCard } from "./OrderResultCard";
import { SubstitutesPanel, type SwapHandler } from "./SubstitutesPanel";

export type QuotePanelProps = {
  quote: Quote;
  /** Diet requirement the quote was requested with (comma list). Defaults to `quote.diet` if the server echoes it. */
  diet?: string | null;
  /** The source (e.g. cart) changed since this quote: ordering is disabled until re-quoted. */
  outdated?: boolean;
  /** Re-quote action (shown on outdated / expired / stale / used). Omit to hide those buttons. */
  onRequote?: () => void;
  requoting?: boolean;
  /** Enables "Swap in cart" on substitute cards. */
  onSwap?: SwapHandler;
  /** Hide the substitutes callout (e.g. in compact chat cards). Default: shown. */
  showSubstitutes?: boolean;
  /** Called once with the created order (any status). The panel itself then shows the order result card. */
  onOrderPlaced?: (order: Order) => void;
  className?: string;
};

function policyTitle(q: Quote, qStrings: Strings["quote"]): string {
  const o = q.policy_preview.outcome;
  return o === "AUTO" ? qStrings.policyAuto : o === "APPROVAL_REQUIRED" ? qStrings.policyApproval : qStrings.policyBlocked;
}

/** Inline error block: title, body, error code + copyable request_id, actions. */
function InlineIssue({ title, body, error, tone = "warning", children }: { title: string; body?: ReactNode; error: unknown; tone?: "warning" | "danger"; children?: ReactNode }) {
  const e = toApiError(error);
  const box = tone === "danger" ? "border-danger-border border-l-danger bg-danger-bg" : "border-warning-border border-l-warning bg-warning-bg";
  const text = tone === "danger" ? "text-danger" : "text-warning";
  return (
    <div role="alert" className={`rounded-md border border-l-4 px-3 py-2.5 ${box}`}>
      <p className={`flex items-center gap-1.5 text-sm font-semibold ${text}`}>
        {tone === "danger" ? <ShieldX className="h-4 w-4 shrink-0" strokeWidth={1.75} /> : <AlertTriangle className="h-4 w-4 shrink-0" strokeWidth={1.75} />}
        {title}
      </p>
      {body && <div className="mt-1 text-xs text-ink-secondary">{body}</div>}
      <p className="mt-1 text-xs text-ink-secondary">{e.message}</p>
      <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
        <span className="font-mono-code rounded bg-white px-1.5 py-0.5 text-[11px] font-medium text-ink">{e.code}</span>
        {e.request_id && <CopyChip value={e.request_id} />}
      </div>
      {children && <div className="mt-2.5 flex flex-wrap gap-2">{children}</div>}
    </div>
  );
}

function staleLines(e: unknown): QuoteLine[] | null {
  const d = toApiError(e).details as { lines?: unknown };
  return Array.isArray(d.lines) ? (d.lines as QuoteLine[]) : null;
}

export function QuotePanel({ quote, diet, outdated = false, onRequote, requoting = false, onSwap, showSubstitutes = true, onOrderPlaced, className = "" }: QuotePanelProps) {
  const strings = useStrings();
  const place = usePlaceOrder({ silent: true });
  const [placed, setPlaced] = useState<Order | null>(null);
  const [note, setNote] = useState("");
  // One key per request content (quote + note): double clicks, retries and remounts reuse it; the server answers
  // "same key + different body" with 409 CONFLICT, so a different note must get a different key.
  const idem = useIdempotencyKey(`place:${quote.id}:${note.trim()}`);
  const nowMs = useServerNow();

  // A new quote starts a new place-order attempt.
  const { reset } = place;
  useEffect(() => {
    setPlaced(null);
    reset();
  }, [quote.id, reset]);

  const outcome = quote.policy_preview.outcome;
  const err = place.isError ? toApiError(place.error) : null;
  // Definitive answers for this quote: placing it again cannot succeed, only a new quote can.
  const definitive = err != null && ["POLICY_BLOCKED", "QUOTE_STALE", "QUOTE_EXPIRED", "QUOTE_ALREADY_USED"].includes(err.code);
  // No answer / 5xx: the order may exist anyway (response lost). Re-read the quote: USED means it does, and a retry with
  // the same key + body replays that order instead of creating a second one.
  const ambiguous = err != null && !definitive && (err.code === "NETWORK_ERROR" || err.status >= 500);
  const live = useQuote(quote.id, { silent: true, enabled: ambiguous, refetchIntervalMs: 5_000 });
  const status = ambiguous && live.data?.id === quote.id ? live.data.status : quote.status;
  const expired = status === "EXPIRED" || nowMs >= Date.parse(quote.expires_at);
  const used = status === "USED";
  const recoverable = ambiguous && used; // the order exists: replaying the same request returns it
  const canPlace = !placed && !outdated && !expired && (!used || recoverable) && outcome !== "BLOCKED" && !definitive;

  // Synchronous guard: a double click fires twice before React re-renders with isPending.
  const inFlight = useRef(false);
  const submit = () => {
    if (inFlight.current || place.isPending || !canPlace) return;
    inFlight.current = true;
    place.mutate(
      { quote_id: quote.id, note: note.trim() || undefined, idempotencyKey: idem.key },
      {
        onSettled: () => {
          inFlight.current = false;
        },
        onSuccess: (order) => {
          setPlaced(order);
          onOrderPlaced?.(order);
        },
      },
    );
  };

  const requoteBtn = onRequote ? (
    <Button size="sm" onClick={onRequote} loading={requoting} icon={<RefreshCw className="h-3.5 w-3.5" strokeWidth={1.75} />}>
      {strings.cart.requote}
    </Button>
  ) : null;

  const fresh = err?.code === "QUOTE_STALE" ? staleLines(place.error) : null;
  const errorBlock = !err ? null : recoverable ? (
    <InlineIssue title={strings.quote.errLostTitle} body={strings.quote.errLostBody} error={place.error}>
      <Button size="sm" variant="secondary" onClick={submit} loading={place.isPending} icon={<RefreshCw className="h-3.5 w-3.5" strokeWidth={1.75} />}>
        {strings.quote.showOrder}
      </Button>
    </InlineIssue>
  ) : err.code === "POLICY_BLOCKED" ? (
    <InlineIssue title={strings.quote.errBlockedTitle} error={place.error} tone="danger">
      {policyFromError(place.error) && <PolicyBanner decision={policyFromError(place.error)!} className="w-full" />}
    </InlineIssue>
  ) : err.code === "QUOTE_STALE" ? (
    <InlineIssue
      title={strings.quote.errStaleTitle}
      error={place.error}
      body={
        <>
          <p>{strings.quote.errStaleBody}</p>
          {fresh && fresh.length > 0 && (
            <div className="mt-2 rounded-md border border-warning-border bg-white">
              <p className="px-3 pt-2 text-[11px] font-semibold uppercase tracking-[0.04em] text-ink-muted">{strings.quote.freshLines}</p>
              <ul className="divide-y divide-line">
                {fresh.map((l) => {
                  const old = quote.lines.find((x) => x.product_id === l.product_id);
                  const changed = !old || old.unit_price_eur !== l.unit_price_eur || old.delivery_status !== l.delivery_status || old.quantity !== l.quantity;
                  return <QuoteLineItem key={l.product_id} line={l} highlight={changed} />;
                })}
              </ul>
            </div>
          )}
        </>
      }
    >
      {requoteBtn}
    </InlineIssue>
  ) : err.code === "QUOTE_EXPIRED" ? (
    <InlineIssue title={strings.quote.errExpiredTitle} body={strings.quote.errExpiredBody} error={place.error}>
      {requoteBtn}
    </InlineIssue>
  ) : err.code === "QUOTE_ALREADY_USED" ? (
    <InlineIssue title={strings.quote.errUsedTitle} body={strings.quote.errUsedBody} error={place.error}>
      <ButtonLink to="/orders" size="sm" variant="secondary">
        {strings.quote.viewOrders}
      </ButtonLink>
      {requoteBtn}
    </InlineIssue>
  ) : err.code === "UPSTREAM_UNAVAILABLE" ? (
    <InlineIssue title={strings.quote.errUpstreamTitle} body={strings.quote.errUpstreamBody} error={place.error}>
      <Button size="sm" variant="secondary" onClick={submit} loading={place.isPending} icon={<RefreshCw className="h-3.5 w-3.5" strokeWidth={1.75} />}>
        {strings.quote.tryAgain}
      </Button>
    </InlineIssue>
  ) : (
    <ErrorPanel error={place.error} onRetry={ambiguous ? submit : undefined} />
  );

  return (
    <div className={`rounded border border-line bg-primary-soft ${className}`} data-testid="quote-panel">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-line px-6 py-4">
        <div className="min-w-0">
          <h2 className="text-[16px] font-bold leading-5 text-navy">{strings.quote.title}</h2>
          <div className="mt-1 flex items-center gap-1.5">
            <CopyChip value={quote.id} />
          </div>
        </div>
        {!placed && (expired ? <span className="text-xs font-semibold text-danger">{strings.quote.expired}</span> : <Countdown to={quote.expires_at} prefix={strings.quote.expires} warnBelowMs={5 * 60_000} />)}
      </div>

      {outdated && !placed && (
        <div className="flex flex-wrap items-center justify-between gap-2 border-b border-warning-border bg-yellow-soft px-6 py-2.5" role="status">
          <p className="flex items-center gap-1.5 text-xs font-medium text-warning">
            <AlertTriangle className="h-4 w-4 shrink-0" strokeWidth={1.75} />
            {strings.quote.outdated}
          </p>
          {requoteBtn}
        </div>
      )}

      <ul className={`divide-y divide-line bg-white ${outdated && !placed ? "opacity-60" : ""}`} aria-label="Quote lines">
        {quote.lines.map((l) => (
          <QuoteLineItem key={l.product_id} line={l} />
        ))}
      </ul>

      {showSubstitutes && !placed && !outdated && (
        <div className="border-t border-line bg-white px-4 py-3">
          <SubstitutesPanel lines={quote.lines} requestedDeliveryDate={quote.requested_delivery_date} diet={diet ?? quote.diet} onSwap={onSwap} />
        </div>
      )}

      <dl className="space-y-2 border-t border-line px-6 py-4 text-sm">
        {(diet ?? quote.diet) && (
          <div className="flex flex-wrap items-center justify-between gap-2">
            <dt className="text-xs text-ink-muted">{strings.quote.dietRequirement}</dt>
            <dd>
              <DietRequirementBadge diet={diet ?? quote.diet} />
            </dd>
          </div>
        )}
        <div className="flex items-center justify-between gap-2">
          <dt className="text-xs text-ink-muted">{strings.quote.deliveryDate}</dt>
          <dd className="flex items-center gap-2">
            <span className="tabular-nums text-xs font-semibold text-ink">{formatDayDate(quote.requested_delivery_date)}</span>
            <DeliveryStatusBadge status={quote.overall_delivery_status} />
          </dd>
        </div>
        <div className="flex items-center justify-between gap-2">
          <dt className="text-xs text-ink-muted">{strings.quote.co2Total}</dt>
          <dd>
            <CO2Value kg={quote.total_co2_kg} basis={strings.quote.co2TotalBasis} className="text-xs" />
          </dd>
        </div>
        <div className="flex items-baseline justify-between gap-2 border-t border-line/70 pt-2">
          <dt className="text-xs font-semibold text-ink-secondary">{strings.quote.totalExcl}</dt>
          <dd className="flex flex-wrap items-center justify-end gap-2">
            <NegotiatedSavingsBadge savingsEur={quote.negotiated_savings_eur} />
            <Money value={quote.total_excl_vat_eur} className="text-xl font-bold text-navy" />
          </dd>
        </div>
        <div className="flex items-baseline justify-between gap-2">
          <dt className="text-xs text-ink-muted">{strings.orders.summaryVat}</dt>
          <dd className="tabular-nums text-xs font-medium text-ink-secondary">{formatVatRates(quote.lines)}</dd>
        </div>
        <div className="flex items-baseline justify-between gap-2">
          <dt className="text-xs text-ink-muted">{strings.quote.totalIncl}</dt>
          <dd>
            <Money value={quote.total_incl_vat_eur} className="text-sm text-ink-secondary" />
          </dd>
        </div>
      </dl>

      <div className="space-y-3 border-t border-line px-6 py-4">
        <PolicyBanner decision={quote.policy_preview} title={policyTitle(quote, strings.quote)} />

        {placed ? (
          <OrderResultCard order={placed} />
        ) : (
          <>
            {errorBlock}
            {used && !expired && !err && !recoverable && (
              <div className="flex flex-wrap items-center justify-between gap-2 rounded-[3px] border border-line-strong bg-white px-3 py-2 text-xs text-ink-secondary" role="status" data-testid="quote-used">
                <span>
                  <span className="font-semibold text-ink">{strings.quote.errUsedTitle}.</span> {strings.quote.errUsedBody}
                </span>
                <ButtonLink to="/orders" size="sm" variant="outline">
                  {strings.quote.viewOrders}
                </ButtonLink>
              </div>
            )}
            {expired && !err && (
              <div className="flex flex-wrap items-center justify-between gap-2 rounded-[3px] border border-warning-border bg-yellow-soft px-3 py-2 text-xs font-medium text-warning" role="status">
                {strings.quote.errExpiredBody}
                {requoteBtn}
              </div>
            )}
            {canPlace && !recoverable && (
              <div>
                <label className="mb-1.5 block text-[14px] font-semibold text-ink" htmlFor={`note-${quote.id}`}>
                  {strings.quote.note}
                </label>
                <input
                  id={`note-${quote.id}`}
                  value={note}
                  onChange={(e) => setNote(e.target.value)}
                  // Frozen while a request is open or its outcome is unknown, so a retry sends the identical body.
                  disabled={place.isPending || ambiguous}
                  maxLength={500}
                  placeholder={strings.quote.notePlaceholder}
                  className={inputClass()}
                />
              </div>
            )}
            {!recoverable && (
            <>
            {quote.lines.every((l) => l.delivery_status !== "NOT_DELIVERABLE") && (() => {
              const effectiveDate = quote.lines.reduce(
                (maxD, l) => (l.available_date && l.available_date > maxD ? l.available_date : maxD),
                quote.requested_delivery_date,
              );
              return (
                <div
                  data-testid="quote-effective-delivery"
                  className="flex flex-wrap items-center justify-between gap-2 rounded-none border border-line bg-white px-3 py-2 text-xs"
                >
                  <div className="min-w-0">
                    <span className="font-semibold text-navy">
                      {strings.quote.effectiveDeliveryLabel(formatDayDate(effectiveDate))}
                    </span>
                    <p className="mt-0.5 text-[11.5px] text-ink-secondary">
                      {strings.quote.effectiveDeliveryHint}
                    </p>
                  </div>
                  <DeliveryStatusBadge status="ON_TIME" />
                </div>
              );
            })()}
            <Button
              size="lg"
              variant={outcome === "BLOCKED" ? "danger" : "lime"}
              className="w-full"
              disabled={!canPlace}
              loading={place.isPending}
              onClick={submit}
              icon={outcome === "BLOCKED" ? <ShieldX className="h-4 w-4" strokeWidth={1.75} /> : <Send className="h-4 w-4" strokeWidth={1.75} />}
            >
              {outcome === "BLOCKED" ? strings.quote.blocked : outcome === "APPROVAL_REQUIRED" ? strings.quote.sendForApproval : strings.quote.placeOrder}
            </Button>
            </>
            )}
          </>
        )}
      </div>
    </div>
  );
}
