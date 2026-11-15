// M6 /approvals: mobile-first inbox of PENDING_APPROVAL orders (DESIGN.md §6E), polled every 10 s.
// Decided cards are removed optimistically and collapse into a live OrderResultCard (Placed / Confirming / Rejected).
import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Bot, CheckCircle2, ChevronDown, Info, MessageCircle, X } from "lucide-react";
import { invalidateOrderViews, useApprovals } from "../api/hooks";
import type { Order } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { OrdersLayout } from "../components/AccountLayout";
import { ApprovalCard } from "../components/approvals/ApprovalCard";
import { TelegramOptInCard } from "../components/approvals/TelegramOptInCard";
import { Card } from "../components/Card";
import { LiveIndicator } from "../components/LiveIndicator";
import { EmptyState } from "../components/EmptyState";
import { ErrorPanel } from "../components/ErrorPanel";
import { Skeleton } from "../components/Skeleton";
import { Stripe } from "../components/Stripe";
import { OrderResultCard } from "../components/quote/OrderResultCard";
import { useStrings } from "../i18n";

function CardSkeleton() {
  return (
    <div className="rounded border border-navy bg-white overflow-hidden" aria-busy="true" aria-label="Loading approvals">
      <div className="flex justify-between bg-navy px-4 py-3">
        <Skeleton className="h-5 w-56 opacity-25" />
        <Skeleton className="h-4 w-24 opacity-25" />
      </div>
      <div className="space-y-3 p-4">
        <Skeleton className="h-10 w-40" />
        <Skeleton className="h-5 w-64" />
        <Skeleton className="h-14 w-full" />
        <Skeleton className="h-20 w-full" />
        <Skeleton className="h-12 w-full" />
      </div>
    </div>
  );
}

export default function ApprovalsPage() {
  const { isUser } = useAuth();
  const strings = useStrings();
  const t = strings.approvals;
  const q = useApprovals({ silent: true });
  const [decided, setDecided] = useState<Order[]>([]);
  const [supersededIds, setSupersededIds] = useState<Set<string>>(() => new Set());
  const [howOpen, setHowOpen] = useState(false);
  const [telegramOpen, setTelegramOpen] = useState(false);
  const decidedIds = new Set([...decided.map((o) => o.id), ...supersededIds]);
  const items = (q.data?.items ?? []).filter((o) => !decidedIds.has(o.id));
  const refresh = () => q.refetch();

  return (
    <OrdersLayout
      breadcrumb={[
        { label: strings.portal.frontPage, to: "/catalog" },
        { label: strings.orders.title, to: "/orders" },
        { label: t.title },
      ]}
      title={t.title}
      actions={
        <div className="flex flex-wrap items-center gap-2">
          {isUser && (
            <button
              type="button"
              data-testid="telegram-optin-toggle"
              aria-expanded={telegramOpen}
              onClick={() => setTelegramOpen((v) => !v)}
              className={`inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-[13px] font-bold transition-colors ${
                telegramOpen
                  ? "border-navy bg-navy text-white"
                  : "border-navy bg-white text-navy hover:bg-primary-soft"
              }`}
            >
              <MessageCircle className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden="true" />
              {t.telegramToggle}
            </button>
          )}
          {q.data && <LiveIndicator updatedAt={q.dataUpdatedAt} fetching={q.isFetching} />}
        </div>
      }
    >
      <div className="max-w-[760px]">
        {telegramOpen && isUser && <TelegramOptInCard className="mb-4" />}
        <div className="mb-3">
          <button
            type="button"
            aria-expanded={howOpen}
            onClick={() => setHowOpen((v) => !v)}
            className="inline-flex items-center gap-1.5 text-[13px] font-semibold text-[#005670] hover:underline"
          >
            <Info className="h-4 w-4 shrink-0" strokeWidth={1.75} />
            {t.howTitle}
            <ChevronDown className={`h-3.5 w-3.5 transition-transform ${howOpen ? "rotate-180" : ""}`} strokeWidth={2} />
          </button>
          {howOpen && (
            <Stripe kind="neutral" compact className="mt-2">
              {t.subtitle}
            </Stripe>
          )}
        </div>

        {decided.length > 0 && (
          <section className="mb-4 space-y-2" aria-label={t.decidedTitle}>
            {decided.map((o) => (
              <div key={o.id} className="relative">
                <OrderResultCard order={o} className="pr-10" />
                <button
                  type="button"
                  onClick={() => setDecided((list) => list.filter((x) => x.id !== o.id))}
                  className="absolute right-2 top-2 rounded-md p-1.5 text-ink-muted hover:bg-white hover:text-ink"
                  aria-label={t.dismiss}
                >
                  <X className="h-4 w-4" strokeWidth={1.75} />
                </button>
              </div>
            ))}
          </section>
        )}

        {q.isError && !q.data ? (
          <ErrorPanel error={q.error} title="Could not load approvals" onRetry={() => void q.refetch()} />
        ) : q.isLoading ? (
          <div className="space-y-4">
            <CardSkeleton />
          </div>
        ) : items.length === 0 ? (
          <Card>
            <EmptyState icon={<CheckCircle2 className="h-5 w-5" strokeWidth={1.75} />} title={t.emptyTitle} description={t.emptyBody} />
          </Card>
        ) : (
          <>
            {q.isError && (
              <div className="mb-3">
                <ErrorPanel error={q.error} title="Could not refresh approvals" onRetry={() => void q.refetch()} />
              </div>
            )}
            <p className="mb-2 text-xs font-bold uppercase tracking-[0.04em] text-ink-muted">
              {t.pendingCount((q.data?.total ?? 0) - ((q.data?.items.length ?? 0) - items.length))}
            </p>
            <div className="space-y-5">
              {items.map((o) => (
                <ApprovalCard
                  key={o.id}
                  order={o}
                  refresh={refresh}
                  onDecided={(d, supersededId) => {
                    setDecided((list) => [d, ...list.filter((x) => x.id !== d.id)]);
                    if (supersededId) {
                      setSupersededIds((prev) => {
                        const next = new Set(prev);
                        next.add(supersededId);
                        return next;
                      });
                    }
                  }}
                />
              ))}
            </div>
          </>
        )}
        <MockAgentOrderButton onCreated={refresh} />
      </div>
    </OrdersLayout>
  );
}

/**
 * Mock mode only (VITE_USE_MOCKS=true): a phone has no console for `__mock.simulateAgentOrder()`, so the demo gets a
 * small dashed button that makes a new agent order arrive. Never rendered against a real backend.
 */
function MockAgentOrderButton({ onCreated }: { onCreated: () => unknown }) {
  const t = useStrings().approvals;
  const qc = useQueryClient();
  const mock = import.meta.env.VITE_USE_MOCKS === "true" ? window.__mock : undefined;
  if (!mock) return null;
  return (
    <div className="mt-6 flex justify-center">
      <button
        type="button"
        className="inline-flex items-center gap-1.5 rounded-md border border-dashed border-warning-border bg-white px-3 py-1.5 text-xs font-semibold text-warning hover:bg-warning-bg"
        onClick={() => {
          mock.simulateAgentOrder();
          invalidateOrderViews(qc);
          void onCreated();
        }}
      >
        <Bot className="h-3.5 w-3.5" strokeWidth={1.75} />
        {t.mockSimulate}
      </button>
    </div>
  );
}
