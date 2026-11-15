// Approval decision logic + the mobile-first approval inbox card (DESIGN.md §6E).
// Raw facts only: actor, totals, delivery date, CO₂ (with basis), fired rule_ids, lines with flags, server-time countdown.
// Approve sends the content_hash this card displays + an Idempotency-Key that is stable per (order, hash, conflict round),
// so double taps and network retries reuse it, and a re-ask after 409 uses a new one.
import { useMemo, useRef, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { AlertTriangle, CalendarDays, Check, ChevronRight, Clock, ShieldCheck, X } from "lucide-react";
import { toApiError, type ApiError } from "../../api/client";
import { useApproveOrder, useIdempotencyKey, useRejectOrder } from "../../api/hooks";
import type { Order, QuoteLine, ReservationStatus } from "../../api/types";
import { useStrings, type Strings } from "../../i18n";
import { formatDayDate, formatDuration } from "../../lib/dates";
import { useServerNow } from "../../lib/useServerNow";
import { formatEur, formatNumber } from "../../lib/format";
import { Button } from "../Button";
import { NegotiatedSavingsBadge } from "../NegotiatedSavingsBadge";
import { CO2Value } from "../CO2Value";
import { ConfirmDialog } from "../ConfirmDialog";
import { CopyChip } from "../CopyChip";
import { Countdown } from "../Countdown";
import { ErrorPanel } from "../ErrorPanel";
import { inputClass } from "../FormField";
import { Money } from "../Money";
import { GovernanceFunnelStrip } from "../GovernanceFunnelStrip";
import { PolicyBanner } from "../PolicyBanner";
import { Stripe } from "../Stripe";
import { ActorOnDark, OrderBlockLineRow } from "../orders/OrderBlock";
import { DietRequirementBadge } from "../cart/DietRequirementPicker";
import { RecipeSanityNotice } from "./RecipeSanityNotice";
import { SafeRescueStrip } from "./SafeRescueStrip";
import { toast } from "../Toast";

/** 409 codes after which the card refetches, highlights what changed and asks again. */
export const APPROVAL_CONFLICT_CODES = ["APPROVAL_CONTENT_CHANGED", "QUOTE_STALE"];

type Snapshot = { hash: string; lines: QuoteLine[]; total: number };
const snapshot = (o: Order): Snapshot => ({ hash: o.content_hash, lines: o.lines, total: o.total_excl_vat_eur });

const baselineKey = (orderId: string) => `aimoannos.approval.baseline.${orderId}`;

function loadBaseline(o: Order): Snapshot {
  try {
    const raw = window.sessionStorage.getItem(baselineKey(o.id));
    if (raw) {
      const parsed = JSON.parse(raw) as Snapshot;
      if (parsed && typeof parsed.hash === "string" && Array.isArray(parsed.lines)) return parsed;
    }
  } catch {
    // ignore storage errors
  }
  const initial = snapshot(o);
  saveBaseline(o.id, initial);
  return initial;
}

function saveBaseline(orderId: string, snap: Snapshot): void {
  try {
    window.sessionStorage.setItem(baselineKey(orderId), JSON.stringify(snap));
  } catch {
    // ignore storage errors
  }
}

function clearBaseline(orderId: string): void {
  try {
    window.sessionStorage.removeItem(baselineKey(orderId));
  } catch {
    // ignore storage errors
  }
}

function lineSig(l: QuoteLine): string {
  return [l.quantity, l.sales_unit, l.unit_price_eur, l.line_total_eur, l.delivery_status, l.available_date, [...l.flags].sort().join(",")].join("|");
}

/** Product ids whose displayed facts differ between two versions of the same order (new lines count as changed). */
export function changedLineIds(before: QuoteLine[], after: QuoteLine[]): Set<string> {
  const prev = new Map(before.map((l) => [l.product_id, lineSig(l)]));
  return new Set(after.filter((l) => prev.get(l.product_id) !== lineSig(l)).map((l) => l.product_id));
}

/** "Before: 1,000 ST × €1.01 = €1,010.00 · On time" for a highlighted line. */
function previousNote(before: QuoteLine[], id: string, strings: Strings): ReactNode {
  const l = before.find((x) => x.product_id === id);
  if (!l) return "New line";
  return (
    <span className="tabular-nums">
      Before: {formatNumber(l.quantity)} {l.sales_unit} × {formatEur(l.unit_price_eur)} = {formatEur(l.line_total_eur)} · {strings.deliveryStatus[l.delivery_status] ?? l.delivery_status}
    </span>
  );
}

export type ApprovalDecision = ReturnType<typeof useApprovalDecision>;

/**
 * Approve / reject state for one pending order.
 * `refresh` refetches the source of `order` (inbox list or order detail) after a conflict or INVALID_STATE.
 */
export function useApprovalDecision(order: Order, opts: { refresh: () => Promise<unknown>; onDecided?: (o: Order) => void }) {
  const strings = useStrings();
  const t = strings.approvals;
  const [baseline, setBaseline] = useState<Snapshot>(() => loadBaseline(order));
  const [round, setRound] = useState(0);
  const [conflict, setConflict] = useState<ApiError | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [note, setNote] = useState("");
  const [rejectOpen, setRejectOpen] = useState(false);
  const approveM = useApproveOrder({ silent: true });
  const rejectM = useRejectOrder({ silent: true });
  // Key = request content (order, hash, conflict round, note): the server answers "same key + different body" with 409.
  const idem = useIdempotencyKey(`approve:${order.id}:${order.content_hash}:${round}:${note.trim()}`);
  const inFlight = useRef(false);

  const hashChanged = order.content_hash !== baseline.hash;
  const changed = hashChanged || conflict != null;
  const changedIds = useMemo(() => (changed ? changedLineIds(baseline.lines, order.lines) : new Set<string>()), [changed, baseline.lines, order.lines]);

  const refetchAfter = async () => {
    setRefreshing(true);
    try {
      await opts.refresh();
    } finally {
      setRefreshing(false);
    }
  };

  const handleInvalidState = (e: ApiError) => {
    const status = (e.details as { status?: string }).status;
    toast.error(e, t.alreadyDecided(status ? strings.orderStatus[status as Order["status"]] ?? status : "decided"));
    void refetchAfter();
  };

  const approve = () => {
    if (inFlight.current || approveM.isPending || refreshing) return;
    inFlight.current = true;
    // The approver has now seen what is displayed: that is what gets approved, and what a conflict is compared against.
    const seen = snapshot(order);
    setBaseline(seen);
    saveBaseline(order.id, seen);
    setConflict(null);
    // mutateAsync, not mutate(…, callbacks): per-call callbacks are dropped when the card unmounts, which happens when a
    // 10 s inbox poll already shows the order as decided before the (slow, gateway-bound) approve response arrives.
    approveM
      .mutateAsync({ id: order.id, content_hash: order.content_hash, note: note.trim() || undefined, idempotencyKey: idem.key })
      .then(
        (o) => {
          clearBaseline(o.id);
          opts.onDecided?.(o);
        },
        (err) => {
          const e = toApiError(err);
          if (APPROVAL_CONFLICT_CODES.includes(e.code)) {
            setConflict(e);
            setRound((r) => r + 1); // next approval = new key (and normally a new hash)
            void refetchAfter();
          } else if (e.code === "INVALID_STATE") {
            approveM.reset();
            handleInvalidState(e);
          }
        },
      )
      .finally(() => {
        inFlight.current = false;
      });
  };

  const reject = () => {
    if (rejectM.isPending) return;
    rejectM.mutateAsync({ id: order.id, note: note.trim() || undefined }).then(
      (o) => {
        setRejectOpen(false);
        opts.onDecided?.(o);
      },
      (err) => {
        const e = toApiError(err);
        if (e.code === "INVALID_STATE") {
          setRejectOpen(false);
          rejectM.reset();
          handleInvalidState(e);
        }
      },
    );
  };

  // Errors other than conflicts / INVALID_STATE stay inline (code + message + request_id); a retry reuses the same key.
  const approveError = approveM.isError && !APPROVAL_CONFLICT_CODES.includes(toApiError(approveM.error).code) ? approveM.error : null;

  return {
    order,
    baseline,
    changed,
    changedIds,
    conflict,
    refreshing,
    note,
    setNote,
    approve,
    approving: approveM.isPending,
    approveError,
    idempotencyKey: idem.key,
    reject,
    rejecting: rejectM.isPending,
    rejectError: rejectM.isError ? rejectM.error : null,
    rejectOpen,
    openReject: () => {
      rejectM.reset();
      setRejectOpen(true);
    },
    closeReject: () => setRejectOpen(false),
  };
}

/** Amber "Order details updated since you opened this card" notice (with the conflict's code + request_id). */
export function ApprovalChangedNotice({ d }: { d: ApprovalDecision }) {
  const t = useStrings().approvals;
  if (!d.changed) return null;
  const totalChanged = d.baseline.total !== d.order.total_excl_vat_eur;
  return (
    <Stripe kind="warning" accent role="alert" title={t.changedTitle} data-testid="approval-changed">
      <p>
        {t.changedBody}
        {totalChanged && (
          <>
            {" "}
            <span className="tabular-nums font-semibold text-ink">
              {formatEur(d.order.total_excl_vat_eur)} ({t.totalWas(formatEur(d.baseline.total))})
            </span>
          </>
        )}
      </p>
      {d.conflict && (
        <div className="mt-2 flex flex-wrap items-center gap-1.5">
          <span className="font-mono-code rounded bg-white px-1.5 py-0.5 text-[11px] font-medium text-ink">{d.conflict.code}</span>
          {d.conflict.request_id && <CopyChip value={d.conflict.request_id} />}
        </div>
      )}
    </Stripe>
  );
}

/** Note input + Approve / Reject (big tap targets) + the content hash being approved. */
/** Error (if any) + the optional decision note. */
export function ApprovalNote({ d, className = "" }: { d: ApprovalDecision; className?: string }) {
  const t = useStrings().approvals;
  const noteId = `approval-note-${d.order.id}`;
  return (
    <div className={`space-y-2.5 ${className}`}>
      {d.approveError != null && <ErrorPanel error={d.approveError} onRetry={d.approve} />}
      <div>
        <label htmlFor={noteId} className="mb-1 block text-xs font-semibold text-ink-secondary">
          {t.note} <span className="font-normal text-ink-muted">· optional</span>
        </label>
        <input
          id={noteId}
          value={d.note}
          onChange={(e) => d.setNote(e.target.value)}
          disabled={d.approving || d.rejecting}
          maxLength={500}
          placeholder={t.notePlaceholder}
          className={inputClass(false, "text-[15px] md:text-sm")}
        />
      </div>
    </div>
  );
}

/** Approve / Reject buttons + the content hash being approved + the reject confirm dialog. */
export function ApprovalActions({ d, className = "" }: { d: ApprovalDecision; className?: string }) {
  const t = useStrings().approvals;
  return (
    <div className={`space-y-2 ${className}`}>
      <div className="flex gap-2.5">
        <Button
          variant="lime"
          size="lg"
          className="h-12 min-w-0 flex-1 px-3"
          onClick={d.approve}
          loading={d.approving}
          disabled={d.refreshing || d.rejecting}
          icon={<Check className="h-4 w-4 shrink-0" strokeWidth={2.25} />}
        >
          <span className="truncate">{d.changed ? t.approveUpdated : t.approve}</span>
        </Button>
        <Button
          variant="danger-outline"
          size="lg"
          className="h-12 shrink-0 px-4 md:px-5"
          onClick={d.openReject}
          disabled={d.approving || d.rejecting}
          icon={<X className="h-4 w-4" strokeWidth={2.25} />}
        >
          {t.reject}
        </Button>
      </div>
      <details className="text-[11px] text-ink-muted" data-testid="approval-technical-details">
        <summary className="cursor-pointer select-none hover:underline">{t.technicalDetails}</summary>
        <p className="mt-1 flex min-w-0 items-center gap-1.5">
          <span className="shrink-0">{t.approving}</span>
          <span className="font-mono-code min-w-0 truncate" title={d.order.content_hash}>
            {d.order.content_hash}
          </span>
        </p>
      </details>
      <ConfirmDialog
        open={d.rejectOpen}
        title={t.rejectTitle}
        confirmLabel={t.rejectCta}
        tone="danger"
        busy={d.rejecting}
        error={d.rejectError}
        onConfirm={d.reject}
        onCancel={d.closeReject}
      >
        <p>{t.rejectBody}</p>
        {d.note.trim() && (
          <p className="rounded border border-line bg-primary-soft px-3 py-2 text-xs text-ink">
            {t.note}: “{d.note.trim()}”
          </p>
        )}
      </ConfirmDialog>
    </div>
  );
}

/** Note + actions in one block (order detail page). */
export function ApprovalFooter({ d, className = "" }: { d: ApprovalDecision; className?: string }) {
  return (
    <div className={`space-y-2.5 ${className}`}>
      <ApprovalNote d={d} />
      <ApprovalActions d={d} />
    </div>
  );
}

/** Lines with changed ones highlighted in amber (+ their previous figures). */
export function ApprovalLines({ d }: { d: ApprovalDecision }) {
  const strings = useStrings();
  const t = strings.approvals;
  return (
    <ul className="divide-y divide-line" aria-label={t.linesTitle}>
      {d.order.lines.map((l, i) => {
        const hl = d.changedIds.has(l.product_id);
        return <OrderBlockLineRow key={`${l.product_id}-${i}`} line={l} highlight={hl} note={hl ? previousNote(d.baseline.lines, l.product_id, strings) : undefined} />;
      })}
    </ul>
  );
}

/** Policy box listing only what fired (non-AUTO rules); falls back to all rules. */
export function FiredRules({ order, className = "" }: { order: Order; className?: string }) {
  const t = useStrings().approvals;
  const fired = order.policy.rules.filter((r) => r.outcome !== "AUTO");
  return <PolicyBanner decision={{ ...order.policy, rules: fired.length ? fired : order.policy.rules }} title={t.rulesTitle} className={className} />;
}

/** Mobile-first approval inbox card styled as an OrderBlock. */
export function ApprovalCard({
  order,
  refresh,
  onDecided,
}: {
  order: Order;
  refresh: () => Promise<unknown>;
  onDecided?: (o: Order, supersededId?: string) => void;
}) {
  const strings = useStrings();
  const t = strings.approvals;
  const d = useApprovalDecision(order, { refresh, onDecided });
  return (
    <article className="overflow-clip rounded border border-navy bg-white" data-testid="approval-card" aria-label={`Approval for ${order.id}`}>
      <header className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1.5 bg-navy px-4 py-3 text-white">
        <div className="flex min-w-0 max-w-full flex-wrap items-center gap-2.5">
          <ActorOnDark actor={order.created_by} />
          <Link to={`/orders/${encodeURIComponent(order.id)}`} className="inline-flex items-center gap-0.5 font-mono-code text-xs text-white/85 hover:text-white hover:underline">
            {order.id}
            <ChevronRight className="h-3.5 w-3.5" strokeWidth={2} />
          </Link>
        </div>
        <Countdown to={order.approval_expires_at} prefix={t.expiresIn} onDark />
      </header>

      <div className="border-b border-line bg-primary-soft px-4 py-4">
        <div className="flex flex-wrap items-end justify-between gap-x-4 gap-y-2">
          <div>
            <p className="text-[11px] font-bold uppercase tracking-[0.04em] text-ink-muted">{t.totalLabel}</p>
            <p className="mt-0.5 flex flex-wrap items-baseline gap-x-2">
              <Money value={order.total_excl_vat_eur} className="text-[32px] font-bold leading-none tracking-[-0.02em] text-navy md:text-[40px]" />
              <span className="text-xs font-medium text-ink-secondary">{strings.common.exclVat}</span>
              <NegotiatedSavingsBadge savingsEur={order.negotiated_savings_eur} />
            </p>
            <p className="tabular-nums mt-1 text-xs text-ink-muted">
              <Money value={order.total_incl_vat_eur} /> {strings.common.inclVat}
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <span className="tabular-nums inline-flex items-center gap-1.5 rounded-full border border-line bg-white px-3 py-1 text-xs font-semibold text-ink">
              <CalendarDays className="h-3.5 w-3.5 text-navy" strokeWidth={1.75} />
              {t.delivery} {formatDayDate(order.requested_delivery_date)}
            </span>
            <span className="inline-flex items-center rounded-full border border-line bg-white px-3 py-1">
              <CO2Value kg={order.total_co2_kg} basis={strings.quote.co2TotalBasis} className="text-xs" />
            </span>
            {/* The diet the server checked every line against (R-HARD-DIET), echoed on Order since backend M8. */}
            {order.diet && <DietRequirementBadge diet={order.diet} />}
            {order.reservation_status && order.reservation_status !== "NONE" && (
              <ReservationHoldPill status={order.reservation_status} expiresAt={order.reservation_expires_at} />
            )}
          </div>
        </div>
      </div>

      <div className="space-y-3 p-4">
        <FiredRules order={order} />
        <GovernanceFunnelStrip
          rules={order.policy.rules}
          agentId={order.created_by.kind === "agent" ? order.created_by.id : undefined}
          compact
        />
        {order.reservation_warning_message && (
          <Stripe
            compact
            accent
            kind={order.reservation_status === "UNRESERVED_DUE_TO_WARNINGS" ? "error" : "warning"}
            data-testid="reservation-warning-line"
          >
            <p className="text-xs font-semibold leading-5 text-ink">{order.reservation_warning_message}</p>
          </Stripe>
        )}
        {order.recipe_sanity_warning && <RecipeSanityNotice warning={order.recipe_sanity_warning} />}
        <SafeRescueStrip
          order={order}
          onRescued={(newOrder, supersededOrder) => {
            clearBaseline(supersededOrder.id);
            onDecided?.(newOrder, supersededOrder.id);
            void refresh();
          }}
          onStale={() => void refresh()}
        />
        <ApprovalChangedNotice d={d} />
      </div>

      <div className="border-t border-line">
        <ApprovalLines d={d} />
      </div>

      <div className="border-t border-line px-4 pt-4 md:bg-primary-soft/40">
        <ApprovalNote d={d} />
      </div>
      {/* Phones: the buttons stick to the bottom of the screen while the card is in view (DESIGN_VALIO §3.5).
          `overflow-clip` on the article (not `overflow-hidden`) keeps it from becoming a scroll container. */}
      <div className="sticky bottom-0 z-10 bg-white px-4 pb-4 pt-3 shadow-portal-sm md:static md:bg-primary-soft/40 md:shadow-none" data-testid="approval-actions">
        <ApprovalActions d={d} />
      </div>
    </article>
  );
}

function ReservationHoldPill({
  status,
  expiresAt,
}: {
  status: ReservationStatus;
  expiresAt?: string | null;
}) {
  const strings = useStrings();
  const t = strings.approvals;
  const now = useServerNow();
  if (status === "NONE") return null;

  const left = expiresAt ? Date.parse(expiresAt) - now : Number.NaN;
  const effectiveStatus = status === "HELD_5H" && !Number.isNaN(left) && left <= 0 ? "HOLD_EXPIRED" : status;

  if (effectiveStatus === "HELD_5H") {
    const label = !Number.isNaN(left) && left > 0 ? t.reservationHeldUntil(formatDuration(left)) : t.reservationHeld;
    return (
      <span
        data-testid="reservation-status-pill"
        className="tabular-nums inline-flex items-center gap-1.5 rounded-full border border-success/30 bg-success-bg px-3 py-1 text-xs font-semibold text-success"
      >
        <ShieldCheck className="h-3.5 w-3.5 shrink-0" strokeWidth={1.75} />
        {label}
      </span>
    );
  }
  if (effectiveStatus === "UNRESERVED_DUE_TO_WARNINGS") {
    return (
      <span
        data-testid="reservation-status-pill"
        className="inline-flex items-center gap-1.5 rounded-full border border-danger/40 bg-red-soft px-3 py-1 text-xs font-semibold text-red-strong"
      >
        <AlertTriangle className="h-3.5 w-3.5 shrink-0" strokeWidth={1.75} />
        {t.reservationUnreserved}
      </span>
    );
  }
  return (
    <span
      data-testid="reservation-status-pill"
      className="inline-flex items-center gap-1.5 rounded-full border border-warning bg-yellow-soft px-3 py-1 text-xs font-semibold text-ink"
    >
      <Clock className="h-3.5 w-3.5 shrink-0 text-warning" strokeWidth={1.75} />
      {t.reservationExpired}
    </span>
  );
}
