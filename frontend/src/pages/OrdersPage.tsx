// M6 /orders: status pills, created-by pills, search/date filter, PaginationBar above list, expandable OrderBlocks.
// Grouped tabs (e.g. Confirming = SUBMITTING + SUBMISSION_UNKNOWN) query each status and merge the results for display,
// because the contract's `status` filter takes a single value.
import { keepPreviousData, useQueries, type UseQueryResult } from "@tanstack/react-query";
import { useMemo } from "react";
import { useSearchParams } from "react-router-dom";
import { Bot, ClipboardList, Search, User } from "lucide-react";
import { endpoints } from "../api/endpoints";
import { qk, useOrders } from "../api/hooks";
import type { Order, OrderListParams, OrderStatus, Page } from "../api/types";
import { OrdersLayout } from "../components/AccountLayout";
import { Button, ButtonLink } from "../components/Button";
import { Card } from "../components/Card";
import { EmptyState } from "../components/EmptyState";
import { ErrorPanel } from "../components/ErrorPanel";
import { TextInput } from "../components/FormField";
import { PaginationBar } from "../components/PaginationBar";
import { FilterPill } from "../components/Pills";
import { Skeleton } from "../components/Skeleton";
import { Stripe } from "../components/Stripe";
import { OrderBlock } from "../components/orders/OrderBlock";
import { useStrings, type Strings } from "../i18n";

const DEFAULT_PAGE_SIZE = 20;
const PAGE_SIZE_OPTIONS = [10, 20, 50] as const;
const MERGE_LIMIT = 100;

type TabKey = keyof Strings["orders"]["tabs"];
const TABS: { key: TabKey; statuses: OrderStatus[] | null }[] = [
  { key: "all", statuses: null },
  { key: "pending", statuses: ["PENDING_APPROVAL"] },
  { key: "placed", statuses: ["PLACED"] },
  { key: "confirming", statuses: ["SUBMITTING", "SUBMISSION_UNKNOWN"] },
  { key: "problem", statuses: ["REJECTED", "FAILED"] },
  { key: "closed", statuses: ["CANCELLED", "EXPIRED"] },
];
type By = "all" | "agents" | "humans";

function useOrderList(statuses: OrderStatus[] | null, by: By, page: number, pageSize: number) {
  const created_by_agent = by === "all" ? undefined : by === "agents";
  const merged = statuses != null && statuses.length > 1;
  const paramSets: OrderListParams[] = merged
    ? statuses.map((status) => ({ status, created_by_agent, limit: MERGE_LIMIT, offset: 0 }))
    : [{ status: statuses?.[0], created_by_agent, limit: pageSize, offset: (page - 1) * pageSize }];
  const results = useQueries({
    queries: paramSets.map((p) => ({
      queryKey: qk.orders(p),
      queryFn: () => endpoints.listOrders(p),
      placeholderData: keepPreviousData,
      refetchInterval: 15_000,
      meta: { silent: true },
    })),
  }) as UseQueryResult<Page<Order>, Error>[];
  const error = results.find((r) => r.isError)?.error ?? null;
  const loading = results.some((r) => r.isLoading);
  const refetch = () => results.forEach((r) => void r.refetch());
  if (!merged) {
    const d = results[0]?.data;
    return { items: d?.items ?? [], total: d?.total ?? 0, loading, error, refetch, truncated: false, ready: !!d };
  }
  const all: Order[] = results.flatMap((r) => r.data?.items ?? []).sort((a, b) => b.created_at.localeCompare(a.created_at));
  const total = results.reduce((s, r) => s + (r.data?.total ?? 0), 0);
  return {
    items: all.slice((page - 1) * pageSize, page * pageSize),
    total: Math.min(total, all.length),
    loading,
    error,
    refetch,
    truncated: results.some((r) => (r.data?.total ?? 0) > MERGE_LIMIT),
    ready: results.every((r) => !!r.data),
  };
}

function matchesSearchAndDates(o: Order, q: string, from: string, until: string): boolean {
  if (from && o.requested_delivery_date < from) return false;
  if (until && o.requested_delivery_date > until) return false;
  if (!q) return true;
  const needle = q.toLowerCase();
  if (o.id.toLowerCase().includes(needle)) return true;
  if (o.gateway_reference?.toLowerCase().includes(needle)) return true;
  return o.lines.some((l) => l.product_id.toLowerCase().includes(needle) || l.product_name.toLowerCase().includes(needle));
}

export default function OrdersPage() {
  const strings = useStrings();
  const t = strings.orders;
  const [params, setParams] = useSearchParams();
  const tabKey = (TABS.some((x) => x.key === params.get("status")) ? params.get("status") : "all") as TabKey;
  const by = (["agents", "humans"].includes(params.get("by") ?? "") ? params.get("by") : "all") as By;
  const q = params.get("q") ?? "";
  const deliveryFrom = params.get("from") ?? "";
  const deliveryUntil = params.get("until") ?? "";
  const page = Math.max(1, Number(params.get("page")) || 1);
  const rawSize = Number(params.get("size"));
  const pageSize = (PAGE_SIZE_OPTIONS as readonly number[]).includes(rawSize) ? rawSize : DEFAULT_PAGE_SIZE;
  const tab = TABS.find((x) => x.key === tabKey)!;
  const list = useOrderList(tab.statuses, by, page, pageSize);
  const pendingCount = useOrders({ status: "PENDING_APPROVAL", limit: 1 }, { silent: true }).data?.total;
  const filtered = tabKey !== "all" || by !== "all" || !!q || !!deliveryFrom || !!deliveryUntil;

  const visible = useMemo(
    () => list.items.filter((o) => matchesSearchAndDates(o, q.trim(), deliveryFrom, deliveryUntil)),
    [list.items, q, deliveryFrom, deliveryUntil],
  );
  const failedOrder = visible.find((o) => o.status === "FAILED");

  const update = (patch: Record<string, string | null>) =>
    setParams((p) => {
      const n = new URLSearchParams(p);
      for (const [k, v] of Object.entries(patch)) {
        if (!v || v === "all" || (k === "page" && v === "1") || (k === "size" && v === String(DEFAULT_PAGE_SIZE))) n.delete(k);
        else n.set(k, v);
      }
      return n;
    });

  return (
    <OrdersLayout
      breadcrumb={[{ label: strings.portal.frontPage, to: "/catalog" }, { label: t.title }]}
      title={t.title}
      subtitle={t.subtitle}
    >
      {failedOrder && (
        <Stripe kind="error" role="alert" className="mb-5">
          {t.failedBanner(failedOrder.id)}
        </Stripe>
      )}

      {/* Filter row: search left, delivery dates right */}
      <div className="mb-3 flex flex-col gap-3 lg:flex-row lg:items-end lg:justify-between">
        <div className="w-full lg:max-w-md">
          <TextInput
            value={q}
            onChange={(e) => update({ q: e.target.value, page: null })}
            placeholder={t.searchPlaceholder}
            aria-label={t.searchPlaceholder}
            prefix={<Search className="h-4 w-4" strokeWidth={1.75} />}
          />
        </div>
        <div className="flex flex-wrap items-end gap-2">
          <label className="block w-full sm:w-auto">
            <span className="mb-1 block text-[12px] font-semibold text-ink-muted">{t.deliveryFrom}</span>
            <TextInput
              type="date"
              value={deliveryFrom}
              onChange={(e) => update({ from: e.target.value, page: null })}
              aria-label={t.deliveryFrom}
              className="tabular-nums w-full sm:w-[160px]"
            />
          </label>
          <span className="hidden pb-2.5 text-ink-muted sm:inline" aria-hidden>
            –
          </span>
          <label className="block w-full sm:w-auto">
            <span className="mb-1 block text-[12px] font-semibold text-ink-muted">{t.deliveryUntil}</span>
            <TextInput
              type="date"
              value={deliveryUntil}
              onChange={(e) => update({ until: e.target.value, page: null })}
              aria-label={t.deliveryUntil}
              className="tabular-nums w-full sm:w-[160px]"
            />
          </label>
        </div>
      </div>

      {/* Status + Created-by filter pills */}
      <div className="mb-4 flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
        <div className="flex flex-wrap gap-2" role="group" aria-label="Order status">
          {TABS.map((x) => (
            <FilterPill
              key={x.key}
              active={x.key === tabKey}
              count={x.key === "pending" && pendingCount ? pendingCount : undefined}
              onClick={() => update({ status: x.key, page: null })}
            >
              {t.tabs[x.key]}
            </FilterPill>
          ))}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-xs font-semibold text-ink-muted">{t.createdBy}:</span>
          <div className="flex flex-wrap gap-1.5" role="group" aria-label={t.createdBy}>
            {(
              [
                ["all", t.byAll, null],
                ["agents", t.byAgents, <Bot key="b" className="h-3.5 w-3.5" strokeWidth={1.75} />],
                ["humans", t.byHumans, <User key="u" className="h-3.5 w-3.5" strokeWidth={1.75} />],
              ] as const
            ).map(([key, label, icon]) => (
              <FilterPill key={key} active={by === key} onClick={() => update({ by: key, page: null })} className="gap-1.5">
                {icon}
                {label}
              </FilterPill>
            ))}
          </div>
        </div>
      </div>

      {list.error && !list.ready ? (
        <ErrorPanel error={list.error} title="Could not load orders" onRetry={list.refetch} />
      ) : list.loading && !list.ready ? (
        <div className="space-y-4" aria-busy="true" aria-label="Loading orders">
          {Array.from({ length: 4 }, (_, i) => (
            <Skeleton key={i} className="h-36 w-full" />
          ))}
        </div>
      ) : visible.length === 0 ? (
        <Card>
          <EmptyState
            icon={<ClipboardList className="h-5 w-5" strokeWidth={1.75} />}
            title={t.emptyTitle}
            description={filtered ? t.emptyFiltered : t.emptyBody}
            action={
              filtered ? (
                <Button variant="outline" onClick={() => update({ status: null, by: null, q: null, from: null, until: null, page: null })}>
                  {t.clearFilters}
                </Button>
              ) : (
                <ButtonLink to="/catalog">{t.goCatalog}</ButtonLink>
              )
            }
          />
        </Card>
      ) : (
        <>
          {list.error && (
            <div className="mb-3">
              <ErrorPanel error={list.error} title="Could not refresh orders" onRetry={list.refetch} />
            </div>
          )}
          <PaginationBar
            noun={t.title}
            page={page}
            pageSize={pageSize}
            total={list.total}
            onPage={(next) => update({ page: String(next) })}
            right={
              <label className="inline-flex items-center gap-1.5 text-[13px] text-ink-secondary">
                <span>{t.showingLabel}</span>
                <select
                  aria-label={t.showingLabel}
                  data-testid="orders-page-size"
                  value={pageSize}
                  onChange={(e) => update({ size: e.target.value, page: null })}
                  className="rounded border border-border bg-white px-2 py-1 text-[13px] font-semibold text-ink focus:border-navy focus:outline-none"
                >
                  {PAGE_SIZE_OPTIONS.map((n) => (
                    <option key={n} value={n}>
                      {n}
                    </option>
                  ))}
                </select>
              </label>
            }
            className="mb-3"
          />
          <div className="space-y-4" data-testid="orders-list">
            {visible.map((o) => (
              <OrderBlock key={o.id} order={o} />
            ))}
          </div>
          {list.truncated && <p className="mt-2 text-xs text-ink-muted">{t.mergedNote}</p>}
        </>
      )}
    </OrdersLayout>
  );
}
