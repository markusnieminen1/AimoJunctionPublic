// Order confirmation after POST /api/orders. Polls GET /api/orders/{id} every 3 s while SUBMITTING / SUBMISSION_UNKNOWN
// (useOrder does that). Never offers a retry in those states: the order may already exist at Valio Aimo.
import { CheckCircle2, Clock, Loader2, XCircle } from "lucide-react";
import { useOrder } from "../../api/hooks";
import type { Order } from "../../api/types";
import { useStrings } from "../../i18n";
import { ButtonLink } from "../Button";
import { CopyChip } from "../CopyChip";
import { Countdown } from "../Countdown";
import { Money } from "../Money";
import { NegotiatedSavingsBadge } from "../NegotiatedSavingsBadge";
import { StatusBadge } from "../StatusBadge";

export function OrderResultCard({ order: initial, className = "" }: { order: Order; className?: string }) {
  const strings = useStrings();
  const q = useOrder(initial.id, { silent: true });
  const o = q.data ?? initial;

  let tone = "border-line bg-white";
  let icon = <CheckCircle2 className="h-5 w-5 text-navy" strokeWidth={1.75} />;
  let title: string = strings.quote.otherTitle;
  let body: string | null = null;
  switch (o.status) {
    case "PLACED":
      tone = "border-line border-l-4 border-l-navy bg-primary-soft";
      title = strings.quote.placedTitle;
      body = strings.quote.placedBody(o.gateway_reference);
      break;
    case "SUBMITTING":
    case "SUBMISSION_UNKNOWN":
      tone = `border-warning-border border-l-4 border-l-warning bg-yellow-soft ${o.status === "SUBMISSION_UNKNOWN" ? "border-dashed" : ""}`;
      icon = <Loader2 className="h-5 w-5 animate-spin text-warning" strokeWidth={1.75} />;
      title = strings.quote.confirmingTitle;
      body = strings.quote.confirmingBody;
      break;
    case "PENDING_APPROVAL":
      tone = "border-warning-border border-l-4 border-l-warning bg-yellow-soft";
      icon = <Clock className="h-5 w-5 text-warning" strokeWidth={1.75} />;
      title = strings.quote.pendingTitle;
      body = strings.quote.pendingBody;
      break;
    case "FAILED":
      tone = "border-danger-border border-l-4 border-l-danger bg-red-soft";
      icon = <XCircle className="h-5 w-5 text-danger" strokeWidth={1.75} />;
      title = strings.quote.failedTitle;
      body = strings.quote.failedBody;
      break;
    case "REJECTED":
      tone = "border-danger-border border-l-4 border-l-danger bg-white";
      icon = <XCircle className="h-5 w-5 text-danger" strokeWidth={1.75} />;
      title = strings.quote.rejectedTitle;
      body = strings.quote.rejectedBody(o.decision_note);
      break;
    case "CANCELLED":
    case "EXPIRED":
      tone = "border-line bg-primary-soft";
      icon = <Clock className="h-5 w-5 text-ink-muted" strokeWidth={1.75} />;
      title = o.status === "CANCELLED" ? strings.quote.cancelledTitle : strings.quote.expiredTitle;
      break;
    default:
      icon = <Clock className="h-5 w-5 text-ink-muted" strokeWidth={1.75} />;
  }

  return (
    <div className={`rounded-[3px] border px-4 py-3.5 ${tone} ${className}`} role="status" aria-live="polite" data-testid="order-result">
      <div className="flex items-start gap-2.5">
        <span className="mt-0.5 shrink-0">{icon}</span>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <p className="text-sm font-semibold text-ink">{title}</p>
            <StatusBadge status={o.status} />
          </div>
          {body && <p className="mt-1 text-xs text-ink-secondary">{body}</p>}
          <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1.5 text-xs">
            <Money value={o.total_excl_vat_eur} vat="excl" className="font-semibold text-ink" />
            <NegotiatedSavingsBadge savingsEur={o.negotiated_savings_eur} />
            <CopyChip value={o.id} />
            {o.status === "PENDING_APPROVAL" && o.approval_expires_at && <Countdown to={o.approval_expires_at} prefix={strings.quote.approvalExpires} />}
          </div>
          <div className="mt-3 flex flex-wrap gap-2">
            <ButtonLink to={`/orders/${encodeURIComponent(o.id)}`} size="sm" variant="outline">
              {strings.quote.viewOrder}
            </ButtonLink>
            {o.status === "PENDING_APPROVAL" && (
              <ButtonLink to="/approvals" size="sm" variant="text">
                {strings.nav.approvals}
              </ButtonLink>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
