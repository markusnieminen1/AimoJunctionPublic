// M6 /orders/:id: status header, policy decision, lines, totals, decision info, event timeline.
// Pending orders get inline approve/reject (same logic as the approval card) and a cancel button.
// useOrder polls every 3 s while SUBMITTING / SUBMISSION_UNKNOWN; there is never a retry button in those states.
import { useState, type ReactNode } from "react";
import { Link, useParams } from "react-router-dom";
import { ArrowLeft, Ban, Loader2, SearchX } from "lucide-react";
import { toApiError } from "../api/client";
import { useCancelOrder, useOrder } from "../api/hooks";
import type { Order } from "../api/types";
import { OrdersLayout } from "../components/AccountLayout";
import { ActorPill } from "../components/ActorPill";
import { ApprovalChangedNotice, ApprovalFooter, FiredRules, useApprovalDecision } from "../components/approvals/ApprovalCard";
import { Button, ButtonLink } from "../components/Button";
import { Card, CardHeader } from "../components/Card";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { NegotiatedSavingsBadge } from "../components/NegotiatedSavingsBadge";
import { CopyChip } from "../components/CopyChip";
import { Countdown } from "../components/Countdown";
import { EmptyState } from "../components/EmptyState";
import { ErrorMeta, ErrorPanel } from "../components/ErrorPanel";
import { Money } from "../components/Money";
import { PolicyBanner } from "../components/PolicyBanner";
import { RequestIdChip } from "../components/RequestIdChip";
import { Skeleton } from "../components/Skeleton";
import { StatusBadge } from "../components/StatusBadge";
import { Stripe } from "../components/Stripe";
import { toast } from "../components/Toast";
import { OrderBlock } from "../components/orders/OrderBlock";
import { OrderTimeline } from "../components/orders/OrderTimeline";
import { OrderTraceSection } from "../components/orders/OrderTraceSection";
import { useStrings } from "../i18n";
import { formatDateTime } from "../lib/dates";

function Fact({ label, children, className = "" }: { label: string; children: ReactNode; className?: string }) {
  return (
    <div className={`min-w-0 ${className}`}>
      <dt className="text-[11px] font-semibold uppercase tracking-[0.04em] text-ink-muted">{label}</dt>
      <dd className="mt-0.5 text-sm text-ink">{children}</dd>
    </div>
  );
}

function DetailSkeleton() {
  const strings = useStrings();
  const t = strings.orders;
  return (
    <OrdersLayout breadcrumb={[{ label: strings.portal.frontPage, to: "/catalog" }, { label: t.title, to: "/orders" }]} title={t.orderId}>
      <div aria-busy="true" aria-label="Loading order" className="space-y-4">
        <Skeleton className="h-16 w-full" />
        <Skeleton className="h-72 w-full" />
        <Skeleton className="h-48 w-full" />
      </div>
    </OrdersLayout>
  );
}

export default function OrderDetailPage() {
  const strings = useStrings();
  const t = strings.orders;
  const { id } = useParams();
  const q = useOrder(id, { silent: true });

  if (q.isLoading) return <DetailSkeleton />;
  if (q.isError && !q.data) {
    const e = toApiError(q.error);
    return (
      <OrdersLayout
        breadcrumb={[{ label: strings.portal.frontPage, to: "/catalog" }, { label: t.title, to: "/orders" }, { label: id ?? "" }]}
        title={t.orderId}
      >
        <div className="space-y-4">
          <BackLink />
          {e.code === "NOT_FOUND" ? (
            <Card>
              <EmptyState
                icon={<SearchX className="h-5 w-5" strokeWidth={1.75} />}
                title={t.notFound}
                description={
                  <>
                    <span className="font-mono-code">{id}</span>
                    <ErrorMeta error={q.error} />
                  </>
                }
                action={<ButtonLink to="/orders">{t.back}</ButtonLink>}
              />
            </Card>
          ) : (
            <ErrorPanel error={q.error} title="Could not load the order" onRetry={() => void q.refetch()} />
          )}
        </div>
      </OrdersLayout>
    );
  }
  if (!q.data) return null;
  // key: a different :id must not inherit the previous order's approval baseline, note or dialogs.
  return <OrderDetail key={q.data.id} order={q.data} refresh={() => q.refetch()} />;
}

function BackLink() {
  const t = useStrings().orders;
  return (
    <Link to="/orders" className="inline-flex items-center gap-1 text-xs font-semibold text-[#005670] hover:underline">
      <ArrowLeft className="h-3.5 w-3.5" strokeWidth={2} />
      {t.back}
    </Link>
  );
}

function OrderDetail({ order, refresh }: { order: Order; refresh: () => Promise<unknown> }) {
  const strings = useStrings();
  const t = strings.orders;
  const pending = order.status === "PENDING_APPROVAL";
  const requestIds = Array.from(new Set(order.events.map((e) => e.request_id).filter((x): x is string => !!x)));

  return (
    <OrdersLayout
      breadcrumb={[
        { label: strings.portal.frontPage, to: "/catalog" },
        { label: t.title, to: "/orders" },
        { label: order.id },
      ]}
      title={
        <>
          {t.orderId}{" "}
          <span className="font-mono-code break-all text-[0.6em] font-semibold tracking-normal">{order.id}</span>
        </>
      }
      actions={
        <ButtonLink to={`/audit?entity_type=order&entity_id=${encodeURIComponent(order.id)}`} size="sm" variant="outline">
          {t.viewAudit}
        </ButtonLink>
      }
    >
      <div className="mb-5 flex flex-wrap items-center gap-2 text-xs text-ink-secondary">
        <StatusBadge status={order.status} />
        <ActorPill actor={order.created_by} />
        <span className="tabular-nums">{formatDateTime(order.created_at)}</span>
        <CopyChip value={order.id} />
        {order.gateway_reference && (
          <span className="inline-flex min-w-0 max-w-full flex-wrap items-center gap-1">
            <span className="shrink-0 text-ink-muted">{t.gatewayRef}</span>
            <CopyChip value={order.gateway_reference} />
          </span>
        )}
      </div>

      <div className="space-y-6">
        <StatusCallout order={order} />

        {pending ? (
          <PendingPanel order={order} refresh={refresh} />
        ) : (
          <OrderBlock
            order={order}
            defaultExpanded
            collapsible={false}
            showOpenLink={false}
            policyBanner={
              <div className="space-y-2.5">
                <PolicyBanner decision={order.policy} title={order.policy.outcome === "AUTO" ? t.policyAutoTitle : undefined} />
                {requestIds.length > 0 && (
                  <div className="flex flex-wrap items-center gap-2 text-xs">
                    <span className="font-semibold text-ink-muted">Request IDs:</span>
                    {requestIds.map((rid) => (
                      <RequestIdChip key={rid} id={rid} />
                    ))}
                  </div>
                )}
              </div>
            }
          />
        )}

        <Card>
          <CardHeader title={t.decisionTitle} />
          {order.decided_by || order.decided_at || order.decision_note ? (
            <dl className="mt-3 grid gap-3 sm:grid-cols-3">
              <Fact label={t.decidedBy}>
                <ActorPill actor={order.decided_by} />
              </Fact>
              <Fact label={t.decidedAt}>
                <span className="tabular-nums">{formatDateTime(order.decided_at)}</span>
              </Fact>
              <Fact label={t.decisionNote}>{order.decision_note ? `“${order.decision_note}”` : <span className="text-ink-muted">—</span>}</Fact>
            </dl>
          ) : (
            <p className="mt-2 text-sm text-ink-muted">{t.noDecision}</p>
          )}
        </Card>

        <section className="space-y-3">
          <h2 className="h-section">{t.eventsHeading}</h2>
          <Card>
            <OrderTimeline events={order.events} />
          </Card>
        </section>

        <OrderTraceSection orderId={order.id} />
      </div>
    </OrdersLayout>
  );
}

/** Settling / failed status explanation. Never a retry button. */
function StatusCallout({ order }: { order: Order }) {
  const t = useStrings().orders;
  if (order.status === "SUBMITTING" || order.status === "SUBMISSION_UNKNOWN") {
    return (
      <Stripe
        kind="warning"
        accent
        role="status"
        icon={<Loader2 className="h-4 w-4 animate-spin text-warning" strokeWidth={1.75} />}
        title={t.confirmingTitle}
        className={order.status === "SUBMISSION_UNKNOWN" ? "border-dashed" : ""}
      >
        <p>{t.confirmingBody}</p>
      </Stripe>
    );
  }
  if (order.status === "FAILED") {
    return (
      <Stripe kind="error" accent role="alert" title={t.failedTitle}>
        <p>{t.failedBody}</p>
      </Stripe>
    );
  }
  return null;
}

/** Inline approval for a pending order: fired rules, changed-content notice, lines (highlighted), approve/reject, cancel. */
function PendingPanel({ order, refresh }: { order: Order; refresh: () => Promise<unknown> }) {
  const strings = useStrings();
  const t = strings.orders;
  const d = useApprovalDecision(order, {
    refresh,
    onDecided: (o) => toast.success(o.status === "REJECTED" ? strings.approvals.rejectedToast : strings.approvals.approvedToast),
  });
  const cancel = useCancelOrder({ silent: true });
  const [cancelOpen, setCancelOpen] = useState(false);

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-1 gap-5 xl:grid-cols-[minmax(0,1fr)_360px]">
        <div className="min-w-0 space-y-3">
          <FiredRules order={order} />
          <ApprovalChangedNotice d={d} />
          <OrderBlock order={order} compact defaultExpanded collapsible={false} showOpenLink={false} highlightIds={d.changedIds} />
        </div>
        <Card className="h-fit space-y-3 border-warning-border" data-testid="pending-panel">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h2 className="text-[15px] font-semibold leading-5 text-ink">{strings.approvals.panelTitle}</h2>
            <Countdown to={order.approval_expires_at} prefix={strings.approvals.expiresIn} />
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Money value={order.total_excl_vat_eur} className="text-2xl font-bold text-navy" />
            <span className="text-xs text-ink-muted">{strings.common.exclVat}</span>
            <NegotiatedSavingsBadge savingsEur={order.negotiated_savings_eur} />
          </div>
          <ApprovalFooter d={d} />
          <div className="border-t border-line pt-3">
            <Button variant="danger-text" size="sm" icon={<Ban className="h-3.5 w-3.5" strokeWidth={1.75} />} onClick={() => setCancelOpen(true)}>
              {t.cancelCta}
            </Button>
          </div>
        </Card>
      </div>
      <ConfirmDialog
        open={cancelOpen}
        title={t.cancelTitle}
        confirmLabel={t.cancelCta}
        tone="danger"
        busy={cancel.isPending}
        error={cancel.isError ? cancel.error : null}
        onConfirm={() =>
          cancel.mutate(
            { id: order.id },
            {
              onSuccess: () => {
                setCancelOpen(false);
                toast.success(t.cancelled);
              },
              onError: (e) => {
                if (toApiError(e).code === "INVALID_STATE") void refresh();
              },
            },
          )
        }
        onCancel={() => {
          cancel.reset();
          setCancelOpen(false);
        }}
      >
        <p>{t.cancelBody}</p>
      </ConfirmDialog>
    </div>
  );
}
