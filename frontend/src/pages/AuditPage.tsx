// M7 /audit: Audit log (URL-synced filters: request_id, entity_type, entity_id, actor_id, page; expandable JSON details)
// and the A2A protocol trace (GET /api/a2a/tasks, additive; 404 → "not available on this server").
import { shortId } from "../lib/ids";
import { Fragment, useEffect, useState, type ReactNode } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { Bot, Check, ChevronDown, ChevronRight, FileCode2, Network, ScrollText, Search, ShieldAlert, ShieldCheck, Tag as TagIcon, Wrench, X } from "lucide-react";
import { isEndpointMissing, toApiError } from "../api/client";
import { useA2AAgentCard, useA2ASessionConnect, useA2ATasks, useAgents, useAudit, useAuditVerify, useGovernanceSummary, useSimulatorScenarios } from "../api/hooks";
import type { A2ATask, AuditEvent, AuditParams, ReasoningStepOut } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { SettingsLayout } from "../components/AccountLayout";
import { FlightSimulator } from "../components/audit/FlightSimulator";
import { RequestRouterInspector } from "../components/audit/RequestRouterInspector";
import { AgentAnomaliesBanner } from "../components/agents/AgentAnomaliesBanner";
import { ActorPill } from "../components/ActorPill";
import { Badge, type BadgeTone } from "../components/Badge";
import { Button } from "../components/Button";
import { Card } from "../components/Card";
import { CopyChip } from "../components/CopyChip";
import { EmptyState } from "../components/EmptyState";
import { ErrorPanel } from "../components/ErrorPanel";
import { GovernanceFunnelStrip } from "../components/GovernanceFunnelStrip";
import { inputClass } from "../components/FormField";
import { LiveIndicator } from "../components/LiveIndicator";
import { PaginationBar } from "../components/PaginationBar";
import { ReasoningSteps } from "../components/ReasoningSteps";
import { Skeleton } from "../components/Skeleton";
import { tableClass, tableWrapClass, theadClass, trClass } from "../components/Table";
import { UnderlineTabs } from "../components/Tabs";
import { ActorOnDark } from "../components/orders/OrderBlock";
import { useStrings } from "../i18n";
import { formatDateTime, formatRelative } from "../lib/dates";
import { useServerNow } from "../lib/useServerNow";

const PAGE_SIZE = 25;
const ENTITY_TYPES = ["order", "quote", "agent", "company", "user", "assistant_chat"];
const AUDIT_ACTIONS = [
  "assistant.chat_turn",
  "assistant.processing_error",
  "assistant.chat",
  "order.create",
  "order.approve",
  "order.reject",
  "order.cancel",
  "company.assistant_settings_updated",
];

export default function AuditPage() {
  const strings = useStrings();
  const t = strings.audit;
  const [params, setParams] = useSearchParams();
  const sim = useSimulatorScenarios({ silent: true });
  const rawView = params.get("view");
  const simAvailable = Boolean(sim.data?.scenarios?.length);
  const view: "log" | "a2a" | "simulator" =
    rawView === "a2a" ? "a2a" : rawView === "simulator" ? "simulator" : "log";
  const setView = (v: "log" | "a2a" | "simulator") =>
    setParams((p) => {
      const n = new URLSearchParams(p);
      if (v === "a2a" || v === "simulator") n.set("view", v);
      else n.delete("view");
      return n;
    });

  const tabs = [
    { id: "log", label: t.viewLog },
    { id: "a2a", label: t.viewA2A },
    ...(simAvailable || rawView === "simulator" ? [{ id: "simulator", label: t.viewSimulator }] : []),
  ];

  return (
    <SettingsLayout
      breadcrumb={[
        { label: strings.portal.myAimo, to: "/account" },
        { label: strings.portal.settings, to: "/account" },
        { label: t.breadcrumb },
      ]}
      title={t.title}
      subtitle={t.subtitle}
    >
      <AgentAnomaliesBanner className="mb-4" />
      <GovernanceIntegrityStrip />
      <RequestRouterInspector className="mb-4" />
      <UnderlineTabs
        items={tabs}
        active={view}
        onChange={(id) => setView(id as "log" | "a2a" | "simulator")}
        ariaLabel="Audit view"
        className="mb-5"
      />
      {view === "a2a" ? (
        <A2ATrace />
      ) : view === "simulator" ? (
        sim.isLoading && !sim.data ? (
          <div className="space-y-3">
            {[0, 1, 2].map((i) => (
              <Skeleton key={i} className="h-28 w-full" />
            ))}
          </div>
        ) : sim.isError || !sim.data ? (
          sim.isError && !isEndpointMissing(sim.error) && toApiError(sim.error).status !== 404 ? (
            <ErrorPanel error={sim.error} title="Could not load Flight Simulator" onRetry={() => void sim.refetch()} />
          ) : (
            <Card>
              <EmptyState
                icon={<ShieldAlert className="h-5 w-5" strokeWidth={1.75} />}
                title={t.simulatorUnavailableTitle}
                description={t.simulatorUnavailableBody}
              />
            </Card>
          )
        ) : (
          <FlightSimulator scenarios={sim.data.scenarios} />
        )
      ) : (
        <AuditLog />
      )}
    </SettingsLayout>
  );
}

// ---------------------------------------------------------------- Governance & integrity strip

function GovernanceIntegrityStrip() {
  const t = useStrings().audit;
  const gov = useGovernanceSummary({ silent: true });
  const verify = useAuditVerify({ silent: true });
  const [open, setOpen] = useState(false);

  const missingBoth =
    (gov.isError && isEndpointMissing(gov.error)) &&
    (verify.isError && isEndpointMissing(verify.error));
  if (missingBoth) return null;

  const chain = verify.data ?? gov.data?.audit_chain;
  const invariants = gov.data?.layer1_invariants ?? [];
  const assistantTools = gov.data?.assistant_tools ?? [];
  const mcpTools = gov.data?.mcp_tools ?? [];

  if (gov.isLoading && verify.isLoading) {
    return <Skeleton className="mb-5 h-20 w-full" />;
  }
  if (!chain && !gov.data) return null;

  return (
    <section className="mb-5 rounded border border-line bg-white p-4" data-testid="governance-strip" aria-label={t.governanceTitle}>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap items-center gap-2.5">
          <span className="inline-flex items-center gap-1.5 text-[14px] font-bold text-navy">
            <ShieldCheck className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
            {t.governanceTitle}
          </span>
          {chain && (
            <Badge
              tone={chain.valid ? "success" : "danger"}
              icon={chain.valid ? <ShieldCheck className="h-3.5 w-3.5" strokeWidth={2} /> : <ShieldAlert className="h-3.5 w-3.5" strokeWidth={2} />}
              data-testid="audit-chain-status"
            >
              {chain.valid ? t.chainVerified(chain.checked_events) : t.chainBroken(chain.broken_event_id)}
            </Badge>
          )}
          {gov.data && (
            <Link
              to="/account#promotions"
              className="inline-flex items-center gap-1 rounded-sm border border-line bg-primary-soft px-2 py-0.5 text-[12px] font-semibold text-navy hover:underline"
            >
              <TagIcon className="h-3 w-3" strokeWidth={1.75} aria-hidden="true" />
              {t.activePromosLabel(gov.data.active_promotions_count)} · {t.managePromotions}
            </Link>
          )}
        </div>

        {gov.data && (
          <Button
            variant="text"
            size="sm"
            aria-expanded={open}
            onClick={() => setOpen((v) => !v)}
            icon={open ? <ChevronDown className="h-4 w-4" strokeWidth={1.75} /> : <ChevronRight className="h-4 w-4" strokeWidth={1.75} />}
          >
            {open ? t.hideGovernanceDetails : t.showGovernanceDetails}
          </Button>
        )}
      </div>

      {invariants.length > 0 && <GovernanceFunnelStrip invariants={invariants} className="mt-3" />}

      {open && gov.data && (
        <div className="mt-4 space-y-4 border-t border-line pt-4 text-xs" data-testid="governance-details">
          {invariants.length > 0 && (
            <div>
              <p className="mb-2 text-[11px] font-bold uppercase tracking-[0.04em] text-ink-muted">{t.invariantsLabel}</p>
              <ul className="grid gap-2 sm:grid-cols-2">
                {invariants.map((inv) => (
                  <li key={inv.rule_id} className="rounded border border-line bg-primary-soft/50 px-3 py-2">
                    <div className="flex items-center gap-2">
                      <span className="font-mono-code font-bold text-navy">{inv.rule_id}</span>
                      <Badge tone={inv.outcome === "BLOCKED" ? "danger" : inv.outcome === "APPROVAL_REQUIRED" ? "warning" : "success"}>
                        {inv.outcome}
                      </Badge>
                    </div>
                    <p className="mt-1 text-ink-secondary">{inv.description}</p>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {(assistantTools.length > 0 || mcpTools.length > 0) && (
            <div className="grid gap-4 lg:grid-cols-2">
              {assistantTools.length > 0 && (
                <div>
                  <p className="mb-2 flex items-center gap-1.5 text-[11px] font-bold uppercase tracking-[0.04em] text-ink-muted">
                    <Bot className="h-3.5 w-3.5 text-navy" strokeWidth={1.75} />
                    Assistant tools ({assistantTools.length})
                  </p>
                  <ul className="space-y-1.5">
                    {assistantTools.map((tool) => (
                      <li key={tool.name} className="rounded border border-line bg-white px-2.5 py-1.5">
                        <span className="font-mono-code font-semibold text-navy">{tool.name}</span>
                        <span className="ml-2 text-ink-secondary">{tool.description}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
              {mcpTools.length > 0 && (
                <div>
                  <p className="mb-2 flex items-center gap-1.5 text-[11px] font-bold uppercase tracking-[0.04em] text-ink-muted">
                    <Wrench className="h-3.5 w-3.5 text-navy" strokeWidth={1.75} />
                    MCP tools ({mcpTools.length})
                  </p>
                  <ul className="space-y-1.5">
                    {mcpTools.map((tool) => (
                      <li key={tool.name} className="rounded border border-line bg-white px-2.5 py-1.5">
                        <span className="font-mono-code font-semibold text-navy">{tool.name}</span>
                        <span className="ml-2 text-ink-secondary">{tool.description}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          )}

          <p className="flex flex-wrap items-center gap-1.5 rounded bg-primary-soft px-3 py-2 text-ink-secondary">
            <FileCode2 className="h-3.5 w-3.5 shrink-0 text-navy" strokeWidth={1.75} />
            <span>{t.postmanNote}</span>
            <code className="font-mono-code font-semibold text-navy">{t.postmanPath}</code>
          </p>
        </div>
      )}
    </section>
  );
}

// ---------------------------------------------------------------- Audit log

function useAuditFilters() {
  const [params, setParams] = useSearchParams();
  const get = (k: string) => params.get(k) ?? "";
  const filters = {
    request_id: get("request_id"),
    entity_type: get("entity_type"),
    entity_id: get("entity_id"),
    actor_id: get("actor_id"),
    action: get("action"),
    outcome: get("outcome"),
  };
  const page = Math.max(1, Number(params.get("page")) || 1);
  const update = (patch: Record<string, string | null>, opts?: { replace?: boolean }) =>
    setParams(
      (p) => {
        const n = new URLSearchParams(p);
        for (const [k, v] of Object.entries(patch)) {
          if (!v) n.delete(k);
          else n.set(k, v);
        }
        if (!("page" in patch)) n.delete("page");
        return n;
      },
      { replace: opts?.replace },
    );
  return { filters, page, update };
}

function AuditLog() {
  const t = useStrings().audit;
  const { filters, page, update } = useAuditFilters();
  const { principal } = useAuth();
  const agents = useAgents({ silent: true });
  const [rid, setRid] = useState(filters.request_id);
  const [open, setOpen] = useState<Set<string>>(new Set());

  // URL → input (back/forward, deep links); input → URL (debounced).
  useEffect(() => setRid(filters.request_id), [filters.request_id]);
  useEffect(() => {
    const v = rid.trim();
    if (v === filters.request_id) return;
    const id = window.setTimeout(() => update({ request_id: v || null }, { replace: true }), 350);
    return () => window.clearTimeout(id);
  }, [rid]);

  const params: AuditParams = { limit: PAGE_SIZE, offset: (page - 1) * PAGE_SIZE };
  for (const [k, v] of Object.entries(filters)) if (v) (params as Record<string, unknown>)[k] = v;
  const q = useAudit(params, { silent: true });
  const items = q.data?.items ?? [];
  const total = q.data?.total ?? 0;
  const anyFilter = Object.values(filters).some(Boolean);

  const toggle = (id: string) =>
    setOpen((s) => {
      const n = new Set(s);
      if (n.has(id)) n.delete(id);
      else n.add(id);
      return n;
    });

  const entityOptions = filters.entity_type && !ENTITY_TYPES.includes(filters.entity_type) ? [...ENTITY_TYPES, filters.entity_type] : ENTITY_TYPES;
  const actionOptions = filters.action && !AUDIT_ACTIONS.includes(filters.action) ? [...AUDIT_ACTIONS, filters.action] : AUDIT_ACTIONS;
  const actorOptions: { id: string; label: string }[] = [
    ...(principal ? [{ id: principal.id, label: `${t.you} (${principal.name})` }] : []),
    ...(agents.data?.items ?? []).map((a) => ({ id: a.id, label: `🤖 ${a.name}` })),
  ];
  if (filters.actor_id && !actorOptions.some((o) => o.id === filters.actor_id)) actorOptions.push({ id: filters.actor_id, label: filters.actor_id });

  const selectCls = inputClass(false, "px-3");

  return (
    <div>
      <div className="mb-4 rounded border border-line bg-white p-3">
        <form
          className="grid grid-cols-1 gap-2.5 sm:grid-cols-2 lg:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)_minmax(0,1fr)_minmax(0,1fr)_minmax(0,0.8fr)_auto]"
          onSubmit={(e) => {
            e.preventDefault();
            update({ request_id: rid.trim() || null });
          }}
          role="search"
        >
          <label className="relative block">
            <span className="sr-only">{t.requestId}</span>
            <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-ink-muted" strokeWidth={1.75} />
            <input
              value={rid}
              onChange={(e) => setRid(e.target.value)}
              placeholder={`${t.requestId}: ${t.requestIdPlaceholder}`}
              aria-label={t.requestId}
              className={inputClass(false, "font-mono-code pl-9 pr-3 text-[13px] placeholder:font-sans")}
            />
          </label>
          <select aria-label={t.entityType} value={filters.entity_type} onChange={(e) => update({ entity_type: e.target.value || null })} className={selectCls}>
            <option value="">{t.allEntities}</option>
            {entityOptions.map((x) => (
              <option key={x} value={x}>
                {x}
              </option>
            ))}
          </select>
          <select aria-label={t.actor} value={filters.actor_id} onChange={(e) => update({ actor_id: e.target.value || null })} className={selectCls}>
            <option value="">{t.allActors}</option>
            {actorOptions.map((o) => (
              <option key={o.id} value={o.id}>
                {o.label}
              </option>
            ))}
          </select>
          <select
            aria-label={t.actionFilter}
            value={filters.action}
            onChange={(e) => update({ action: e.target.value || null })}
            className={selectCls}
            data-testid="audit-action-filter"
          >
            <option value="">{t.allActions}</option>
            {actionOptions.map((a) => (
              <option key={a} value={a}>
                {a}
              </option>
            ))}
          </select>
          <select
            aria-label={t.outcomeFilter}
            value={filters.outcome}
            onChange={(e) => update({ outcome: e.target.value || null })}
            className={selectCls}
            data-testid="audit-outcome-filter"
          >
            <option value="">{t.allOutcomes}</option>
            <option value="ok">ok</option>
            <option value="error">error</option>
          </select>
          <Button
            variant="text"
            disabled={!anyFilter}
            onClick={() => update({ request_id: null, entity_type: null, entity_id: null, actor_id: null, action: null, outcome: null })}
            icon={<X className="h-3.5 w-3.5" strokeWidth={2} />}
          >
            {t.clear}
          </Button>
        </form>
        {filters.entity_id && (
          <div className="mt-2.5 flex items-center gap-1.5 text-xs">
            <span className="text-ink-muted">{t.entityId}:</span>
            <span className="font-mono-code inline-flex items-center gap-1 rounded-none border border-[#005670]/40 bg-primary-soft px-2 py-0.5 text-[11px] font-bold text-navy">
              {filters.entity_id}
              <button type="button" onClick={() => update({ entity_id: null })} aria-label={`Remove ${t.entityId} filter`} className="hover:text-danger">
                <X className="h-3 w-3" strokeWidth={2} />
              </button>
            </span>
          </div>
        )}
      </div>

      {q.isError && q.data && (
        <div className="mb-3">
          <ErrorPanel error={q.error} title="Could not refresh the audit log · showing earlier results" onRetry={() => void q.refetch()} />
        </div>
      )}
      {q.isError && !q.data ? (
        <ErrorPanel error={q.error} title="Could not load the audit log" onRetry={() => void q.refetch()} />
      ) : q.isLoading ? (
        <Card padded={false}>
          <div className="space-y-2 p-4" aria-busy="true" aria-label="Loading audit events">
            {Array.from({ length: 8 }, (_, i) => (
              <Skeleton key={i} className="h-9 w-full" />
            ))}
          </div>
        </Card>
      ) : items.length === 0 ? (
        <Card>
          <EmptyState icon={<ScrollText className="h-5 w-5" strokeWidth={1.75} />} title={t.emptyTitle} description={t.emptyBody} />
        </Card>
      ) : (
        <>
          <PaginationBar
            noun="Events"
            page={page}
            pageSize={PAGE_SIZE}
            total={total}
            onPage={(next) => update({ page: String(next) })}
            className="mb-3"
          />
          <div className={`hidden md:block ${tableWrapClass}`}>
            <table className={tableClass} data-testid="audit-table">
              <thead className={theadClass}>
                <tr>
                  <th className="w-8 py-2.5 pl-2 pr-1" />
                  <th className="px-2.5 py-2.5 text-[14px] font-bold leading-[17px] text-ink">{t.colTime}</th>
                  <th className="px-2.5 py-2.5 text-[14px] font-bold leading-[17px] text-ink">{t.colActor}</th>
                  <th className="px-2.5 py-2.5 text-[14px] font-bold leading-[17px] text-ink">{t.colAction}</th>
                  <th className="px-2.5 py-2.5 text-[14px] font-bold leading-[17px] text-ink">{t.colEntity}</th>
                  <th className="px-2.5 py-2.5 text-[14px] font-bold leading-[17px] text-ink">{t.colOutcome}</th>
                  <th className="px-2.5 py-2.5 text-[14px] font-bold leading-[17px] text-ink">{t.colRequest}</th>
                </tr>
              </thead>
              <tbody>
                {items.map((e) => {
                  const isOpen = open.has(e.id);
                  const auditTd = "px-2.5 py-2.5 align-top text-[14px] leading-5 text-ink";
                  return (
                    <Fragment key={e.id}>
                      <tr className={`${trClass} ${isOpen ? "bg-primary-soft/60" : ""}`}>
                        <td className="py-2.5 pl-2 pr-1 align-top">
                          <ExpandButton open={isOpen} onClick={() => toggle(e.id)} />
                        </td>
                        <td className={`${auditTd} tabular-nums whitespace-nowrap text-xs text-ink-secondary`}>{formatDateTime(e.at)}</td>
                        <td className={`${auditTd} max-w-[200px] xl:max-w-[240px]`}>
                          <ActorPill actor={e.actor} stacked />
                        </td>
                        <td className={`${auditTd} font-mono-code whitespace-nowrap text-[12px] text-ink`}>{e.action}</td>
                        <td className={auditTd}>
                          <EntityRef e={e} onFilter={(type, id) => update({ entity_type: type, entity_id: id })} />
                        </td>
                        <td className={auditTd}>
                          <OutcomeBadge outcome={e.outcome} />
                        </td>
                        <td className={`${auditTd} whitespace-nowrap`}>
                          <RequestCell id={e.request_id} active={filters.request_id === e.request_id} onFilter={() => update({ request_id: e.request_id })} />
                        </td>
                      </tr>
                      {isOpen && (
                        <tr className="border-b border-line bg-primary-soft/50">
                          <td />
                          <td colSpan={6} className="px-4 py-3">
                            <JsonBlock event={e} />
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
          <div className="overflow-hidden rounded border border-line bg-white md:hidden">
            <ul className="divide-y divide-line">
              {items.map((e) => {
                const isOpen = open.has(e.id);
                return (
                  <li key={e.id} className="px-4 py-3">
                    <div className="flex items-start justify-between gap-2">
                      <div className="min-w-0">
                        <p className="font-mono-code text-[12px] font-semibold text-ink">{e.action}</p>
                        <p className="tabular-nums text-[11.5px] text-ink-muted">{formatDateTime(e.at)}</p>
                      </div>
                      <OutcomeBadge outcome={e.outcome} />
                    </div>
                    <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
                      <ActorPill actor={e.actor} stacked />
                      <EntityRef e={e} onFilter={(type, id) => update({ entity_type: type, entity_id: id })} />
                    </div>
                    <div className="mt-1.5 flex items-center justify-between gap-2">
                      <RequestCell id={e.request_id} active={filters.request_id === e.request_id} onFilter={() => update({ request_id: e.request_id })} />
                      <button type="button" onClick={() => toggle(e.id)} className="text-xs font-semibold text-[#005670]" aria-expanded={isOpen}>
                        {isOpen ? t.hideDetails : t.showDetails}
                      </button>
                    </div>
                    {isOpen && <JsonBlock event={e} className="mt-2" />}
                  </li>
                );
              })}
            </ul>
          </div>
        </>
      )}
    </div>
  );
}

function ExpandButton({ open, onClick }: { open: boolean; onClick: () => void }) {
  const t = useStrings().audit;
  const Icon = open ? ChevronDown : ChevronRight;
  return (
    <button type="button" onClick={onClick} aria-expanded={open} aria-label={open ? t.hideDetails : t.showDetails} className="rounded p-1 text-ink-muted hover:bg-white hover:text-ink">
      <Icon className="h-4 w-4" strokeWidth={1.75} />
    </button>
  );
}

function OutcomeBadge({ outcome }: { outcome: AuditEvent["outcome"] }) {
  return <Badge tone={outcome === "ok" ? "success" : "danger"}>{outcome}</Badge>;
}

/** Entity type + id; orders and agents link to their pages, the funnel narrows the log to that entity. */
function EntityRef({ e, onFilter }: { e: AuditEvent; onFilter: (type: string, id: string) => void }) {
  const href = e.entity_id ? (e.entity_type === "order" ? `/orders/${encodeURIComponent(e.entity_id)}` : e.entity_type === "agent" ? `/agents/${encodeURIComponent(e.entity_id)}` : null) : null;
  return (
    <span className="inline-flex max-w-full flex-wrap items-center gap-1 text-xs">
      <span className="font-semibold text-ink-secondary">{e.entity_type}</span>
      {e.entity_id &&
        (href ? (
          <Link to={href} title={e.entity_id} className="font-mono-code truncate text-[11.5px] text-primary hover:underline">
            {shortId(e.entity_id)}
          </Link>
        ) : (
          <span className="font-mono-code truncate text-[11.5px] text-ink-muted" title={e.entity_id}>
            {shortId(e.entity_id)}
          </span>
        ))}
      {e.entity_id && (
        <button type="button" onClick={() => onFilter(e.entity_type, e.entity_id!)} className="rounded px-1 text-[11px] font-semibold text-ink-muted hover:bg-primary-soft hover:text-primary" title="Show only this entity">
          filter
        </button>
      )}
    </span>
  );
}

function RequestCell({ id, active, onFilter }: { id: string; active: boolean; onFilter: () => void }) {
  return (
    <span className="inline-flex items-center gap-0.5">
      <CopyChip value={id} label={shortId(id)} className={active ? "border-primary text-primary" : ""} />
      {!active && (
        <button type="button" onClick={onFilter} className="rounded p-1 text-ink-muted hover:bg-primary-soft hover:text-primary" title={`Filter by ${id}`} aria-label={`Filter by ${id}`}>
          <Search className="h-3.5 w-3.5" strokeWidth={1.75} />
        </button>
      )}
    </span>
  );
}

function parseReasoningSteps(details: AuditEvent["details"]): ReasoningStepOut[] {
  const raw = details?.reasoning_steps;
  if (!Array.isArray(raw)) return [];
  return raw.filter(
    (s): s is ReasoningStepOut =>
      !!s && typeof s === "object" && typeof (s as ReasoningStepOut).stage === "string" && typeof (s as ReasoningStepOut).summary === "string",
  );
}

function formatErrorDetail(raw: unknown): string | null {
  if (typeof raw === "string") return raw;
  if (raw && typeof raw === "object") {
    const o = raw as Record<string, unknown>;
    const code = typeof o.code === "string" ? o.code : null;
    const status = typeof o.status_code === "number" ? String(o.status_code) : null;
    const msg = typeof o.message === "string" ? o.message : null;
    const prefix = [code, status].filter(Boolean).join(" ");
    if (prefix && msg) return `${prefix}: ${msg}`;
    if (msg) return msg;
    if (prefix) return prefix;
    return JSON.stringify(raw);
  }
  return null;
}

function ChatTraceCard({ event }: { event: AuditEvent }) {
  const t = useStrings().audit;
  const d = event.details ?? {};
  const userMessage = typeof d.user_message === "string" ? d.user_message : null;
  const reply = typeof d.assistant_reply === "string" ? d.assistant_reply : null;
  const errorDetail = formatErrorDetail(d.error_detail);
  const warnings = Array.isArray(d.processing_warnings) ? d.processing_warnings.filter((x): x is string => typeof x === "string") : [];
  const score = typeof d.assurance_score === "number" ? d.assurance_score : null;
  const threshold = typeof d.assurance_threshold === "number" ? d.assurance_threshold : null;
  const autoExecuted = d.auto_executed === true;
  const channel = typeof d.channel === "string" ? d.channel : null;
  const branchName = typeof d.branch_name === "string" ? d.branch_name : null;

  if (!userMessage && !reply && !errorDetail && warnings.length === 0) return null;

  return (
    <div className="rounded border border-line bg-white p-3 space-y-2 text-xs" data-testid="audit-chat-trace-card">
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="text-[11px] font-bold uppercase tracking-[0.04em] text-navy">{t.chatTraceTitle}</span>
        {channel && <Badge tone="neutral">{channel}</Badge>}
        {branchName && <Badge tone="info">{branchName}</Badge>}
        {score != null && (
          <Badge tone={autoExecuted ? "success" : "info"}>
            {score}%{threshold != null ? ` / ${threshold}%` : ""}
            {autoExecuted ? " · auto" : ""}
          </Badge>
        )}
        {warnings.map((w) => (
          <Badge key={w} tone="warning">
            {w}
          </Badge>
        ))}
      </div>
      {errorDetail && (
        <div className="rounded border border-danger-border bg-red-soft px-2.5 py-1.5 text-ink">
          <span className="font-bold text-danger">{t.chatErrorDetail}: </span>
          <span className="font-mono-code break-words">{errorDetail}</span>
        </div>
      )}
      {userMessage && (
        <p className="text-ink">
          <span className="font-semibold text-ink-secondary">{t.chatUserMsg}: </span>
          <span className="whitespace-pre-wrap break-words">{userMessage}</span>
        </p>
      )}
      {reply && (
        <p className="text-ink-secondary">
          <span className="font-semibold text-ink-secondary">{t.chatAssistantReply}: </span>
          <span className="whitespace-pre-wrap break-words">{reply}</span>
        </p>
      )}
    </div>
  );
}

function JsonBlock({ event, className = "" }: { event: AuditEvent; className?: string }) {
  const t = useStrings().audit;
  const { details, ...meta } = event;
  const reasoningSteps = parseReasoningSteps(details);
  const isChatEvent =
    event.entity_type === "assistant_chat" ||
    typeof details?.user_message === "string" ||
    formatErrorDetail(details?.error_detail) !== null;

  const jsonGrid = (
    <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
      <div className="min-w-0">
        <p className="mb-1 text-[11px] font-bold uppercase tracking-[0.04em] text-ink-muted">{t.details}</p>
        <pre className="font-mono-code max-h-80 overflow-auto rounded border border-line bg-primary-soft p-3 text-[11.5px] leading-[1.5] text-ink" data-testid="audit-json">
          {JSON.stringify(details, null, 2)}
        </pre>
      </div>
      <div className="min-w-0">
        <p className="mb-1 text-[11px] font-bold uppercase tracking-[0.04em] text-ink-muted">Event</p>
        <pre className="font-mono-code max-h-80 overflow-auto rounded border border-line bg-primary-soft p-3 text-[11.5px] leading-[1.5] text-ink-secondary">{JSON.stringify(meta, null, 2)}</pre>
      </div>
    </div>
  );

  if (reasoningSteps.length === 0 && !isChatEvent) {
    return <div className={className}>{jsonGrid}</div>;
  }

  return (
    <div className={`space-y-4 ${className}`}>
      {isChatEvent && <ChatTraceCard event={event} />}
      {reasoningSteps.length > 0 && (
        <div>
          <p className="mb-2 text-[11px] font-bold uppercase tracking-[0.04em] text-navy">{t.reasoningTitle}</p>
          <ReasoningSteps steps={reasoningSteps} testId="audit-reasoning-steps" />
        </div>
      )}
      <details className="rounded border border-line bg-white px-3 py-2">
        <summary className="cursor-pointer text-xs font-semibold text-navy hover:underline">{t.rawJson}</summary>
        <div className="mt-3">{jsonGrid}</div>
      </details>
    </div>
  );
}

// ---------------------------------------------------------------- A2A trace

const STEP_TONE: Record<string, BadgeTone> = {
  working: "info",
  "input-required": "warning",
  completed: "success",
  failed: "danger",
  canceled: "neutral",
  PENDING_APPROVAL: "warning",
  SUBMITTING: "info",
  SUBMISSION_UNKNOWN: "warning",
  PLACED: "success",
  REJECTED: "danger",
  FAILED: "danger",
  CANCELLED: "neutral",
  EXPIRED: "neutral",
};

/**
 * Step label per history entry. The real backend keeps the A2A states (working / input-required / completed / failed /
 * canceled) and says *why* in `data.reason`: input-required + reason PENDING_APPROVAL is the human-approval wait;
 * working turns may carry the linked `order_status` (SUBMITTING, …). Shown as those order steps, else the task state.
 */
function stepLabel(h: A2ATask["history"][number]): string {
  const d = h.data ?? {};
  if (d.reason === "PENDING_APPROVAL") return "PENDING_APPROVAL";
  const s = typeof d.order_status === "string" ? d.order_status : null;
  return s && h.state === "working" ? s : h.state;
}

/** Consecutive duplicates collapsed: working → input-required → working → PENDING_APPROVAL → completed. */
export function a2aSteps(task: A2ATask): string[] {
  const out: string[] = [];
  for (const h of task.history) {
    const l = stepLabel(h);
    if (out[out.length - 1] !== l) out.push(l);
  }
  if (out[out.length - 1] !== task.state && ["completed", "failed", "canceled"].includes(task.state)) out.push(task.state);
  return out;
}

function A2ATrace() {
  const t = useStrings().audit;
  const q = useA2ATasks({ limit: 20 }, { silent: true });
  const sessionQ = useA2ASessionConnect({ silent: true });
  const cardQ = useA2AAgentCard({ silent: true });
  const now = useServerNow(15_000);

  if (q.isLoading) {
    return (
      <div className="space-y-3" aria-busy="true" aria-label="Loading agent tasks">
        {[0, 1].map((i) => (
          <Skeleton key={i} className="h-40 w-full" />
        ))}
      </div>
    );
  }
  if (q.isError && !q.data) {
    const e = toApiError(q.error);
    if (e.status === 404 || e.code === "NOT_FOUND") {
      return (
        <Card>
          <EmptyState icon={<Network className="h-5 w-5" strokeWidth={1.75} />} title={t.a2aUnavailableTitle} description={t.a2aUnavailableBody} />
        </Card>
      );
    }
    return <ErrorPanel error={q.error} title="Could not load the A2A trace" onRetry={() => void q.refetch()} />;
  }
  const tasks = q.data?.items ?? [];
  const handshake = sessionQ.data?.result;
  const matters = handshake?.supported_matters ?? cardQ.data?.supported_matters ?? [];

  return (
    <div className="space-y-4">
      {matters.length > 0 && (
        <Card data-testid="a2a-session-capabilities">
          <div className="flex flex-wrap items-center justify-between gap-2 border-b border-line pb-2.5">
            <div>
              <h2 className="text-[14px] font-bold text-navy">{t.a2aSessionTitle}</h2>
              {handshake?.session_greeting && (
                <p className="mt-0.5 text-[12.5px] text-ink-secondary">{handshake.session_greeting}</p>
              )}
            </div>
            <Badge tone="neutral">{handshake?.protocol ?? "A2A/JSON-RPC-2.0"}</Badge>
          </div>
          <p className="mt-2.5 text-[11.5px] font-bold uppercase tracking-wide text-ink-secondary">{t.a2aMattersLabel}</p>
          <div className="mt-2 grid gap-2.5 sm:grid-cols-2 lg:grid-cols-4">
            {matters.map((m) => (
              <div key={m.id} className="rounded border border-line bg-surface p-2.5">
                <div className="flex items-center justify-between gap-2">
                  <span className="text-[13px] font-bold text-navy">{m.title}</span>
                  <Badge tone={m.enabled ? "success" : "neutral"}>
                    {m.enabled ? t.a2aEnabled : t.a2aDisabled}
                  </Badge>
                </div>
                <p className="mt-1 text-[12px] leading-snug text-ink-secondary">{m.summary}</p>
                {m.id === "preliminary_tendering" && (
                  <Link
                    to="/admin"
                    data-testid="audit-negotiation-admin-link"
                    className="mt-1.5 inline-block text-[11.5px] font-bold text-navy underline underline-offset-2 hover:text-primary"
                  >
                    {t.a2aManageAdmin} →
                  </Link>
                )}
              </div>
            ))}
          </div>
        </Card>
      )}
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <p className="text-sm text-ink-secondary">{t.a2aLead}</p>
        {q.data && <LiveIndicator updatedAt={q.dataUpdatedAt} fetching={q.isFetching} />}
      </div>
      {tasks.length === 0 ? (
        <Card>
          <EmptyState icon={<Bot className="h-5 w-5" strokeWidth={1.75} />} title={t.a2aEmptyTitle} description={t.a2aEmptyBody} />
        </Card>
      ) : (
        <ul className="space-y-3" data-testid="a2a-tasks">
          {tasks.map((task) => (
            <A2ATaskCard key={task.id} task={task} now={now} />
          ))}
        </ul>
      )}
    </div>
  );
}

function A2ATaskCard({ task, now }: { task: A2ATask; now: number }) {
  const t = useStrings().audit;
  const [showAll, setShowAll] = useState(false);
  const steps = a2aSteps(task);
  const terminal = ["completed", "failed", "canceled"].includes(task.state);
  const displaySteps = terminal
    ? steps.map((s, i) => ({ label: s, status: "done" as const, index: i }))
    : [
        ...steps.map((s, i) => ({
          label: s,
          status: i < steps.length - 1 ? ("done" as const) : ("current" as const),
          index: i,
        })),
        { label: "completed", status: "future" as const, index: steps.length },
      ];
  const hiddenCount = Math.max(0, task.history.length - 4);
  const visibleHistory = hiddenCount > 0 && !showAll ? task.history.slice(-4) : task.history;

  return (
    <li className="overflow-hidden rounded border border-navy bg-white" data-testid="a2a-task">
      <div className="flex flex-wrap items-center justify-between gap-2 bg-navy px-4 py-3 text-white">
        <div className="flex min-w-0 flex-wrap items-center gap-2.5">
          <ActorOnDark actor={task.principal} />
          <span className="font-mono-code text-xs text-white/90">{task.id}</span>
        </div>
        <div className="flex items-center gap-2.5 text-[11.5px]">
          <span className="tabular-nums text-white/80">
            {t.a2aUpdated} {formatRelative(task.updated_at, now)}
          </span>
          <Badge tone={STEP_TONE[task.state] ?? "neutral"}>{task.state}</Badge>
        </div>
      </div>

      <div className="p-4">
        <ol className="flex flex-wrap items-center gap-2" aria-label="Task states" data-testid="a2a-stepper">
          {displaySteps.map((step, i) => {
            const circleClass =
              step.status === "done"
                ? "bg-navy text-white"
                : step.status === "current"
                  ? "bg-lime text-navy"
                  : "border-2 border-[#DEE4E6] bg-white text-ink-muted";
            return (
              <li key={`${step.label}-${i}`} className="inline-flex items-center gap-2">
                <span className={`tabular-nums inline-flex h-6 w-6 items-center justify-center rounded-full text-[11px] font-bold ${circleClass}`}>
                  {step.status === "done" ? <Check className="h-3.5 w-3.5" strokeWidth={2.5} /> : i + 1}
                </span>
                <span className={`font-mono-code text-xs font-semibold ${step.status === "future" ? "text-ink-muted" : "text-ink"}`}>
                  {step.label}
                </span>
                {i < displaySteps.length - 1 && (
                  <span className={`h-[2px] w-6 ${step.status === "done" ? "bg-navy" : "bg-[#DEE4E6]"}`} aria-hidden="true" />
                )}
              </li>
            );
          })}
        </ol>

        {hiddenCount > 0 && (
          <div className="mt-3">
            <button
              type="button"
              onClick={() => setShowAll((v) => !v)}
              data-testid={`a2a-toggle-history-${task.id}`}
              className="text-xs font-semibold text-navy hover:underline"
            >
              {showAll ? t.a2aHideEarlier : t.a2aShowEarlier(hiddenCount)}
            </button>
          </div>
        )}

        <ol className="mt-4 space-y-2.5 border-l-2 border-[#DEE4E6] pl-3.5">
          {visibleHistory.map((h, i) => (
            <li key={`${h.at}-${i}`} className="text-sm">
              <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[11.5px]">
                <time dateTime={h.at} className="tabular-nums font-semibold text-ink">
                  {formatDateTime(h.at)}
                </time>
                <span className={`rounded-none border px-2 py-0.5 text-[10.5px] font-bold uppercase tracking-[0.04em] ${h.role === "agent" ? "border-[#C5B3DF] bg-[#EEE8F5] text-[#4B3570]" : "border-[#005670]/40 bg-primary-soft text-navy"}`}>{t.a2aRoles[h.role] ?? h.role}</span>
                <span className="font-mono-code text-ink-muted">{stepLabel(h)}</span>
              </div>
              <p className="mt-0.5 leading-5 text-ink tabular-nums">{h.message}</p>
              <DataChips data={h.data} />
            </li>
          ))}
        </ol>

        {task.linked_order_id && (
          <div className="mt-3 flex items-center gap-2 border-t border-line pt-2.5 text-xs">
            <span className="text-ink-muted">{t.a2aOrder}</span>
            <Link to={`/orders/${encodeURIComponent(task.linked_order_id)}`} className="font-mono-code inline-flex items-center gap-0.5 font-semibold text-[#005670] hover:underline">
              {task.linked_order_id}
              <ChevronRight className="h-3.5 w-3.5" strokeWidth={2} />
            </Link>
          </div>
        )}
      </div>
    </li>
  );
}

/** Big nested payloads (the full quote / order / policy JSON) live in the audit log; the trace shows their key facts. */
const HIDDEN_DATA_KEYS = new Set(["order_status", "quote", "order", "fired_rules"]);
const chipBase = "font-mono-code max-w-full break-all rounded-none px-2 py-0.5 text-[11px] font-semibold";

/** One chip text for an element of an array: strings as is, options as `product_id (storage)`, rules by id. */
function itemLabel(x: unknown): { text: string; title?: string; productId?: string } | null {
  if (x == null) return null;
  if (typeof x !== "object") return { text: String(x) };
  const o = x as Record<string, unknown>;
  if (typeof o.product_id === "string") {
    const storage = typeof o.storage === "string" ? ` (${o.storage})` : "";
    return { text: `${o.product_id}${storage}`, title: typeof o.name === "string" ? o.name : undefined, productId: o.product_id };
  }
  if (typeof o.rule_id === "string") return { text: o.rule_id };
  if (typeof o.option_id === "string") return { text: o.option_id };
  return null;
}

/** Structured data of a turn: options / rule ids as chips, scalar fields as key=value (no prose invented). */
function DataChips({ data }: { data?: Record<string, unknown> | null }) {
  if (!data) return null;
  const chips: ReactNode[] = [];
  for (const [k, v] of Object.entries(data)) {
    if (HIDDEN_DATA_KEYS.has(k)) continue;
    if (k === "linked_order_id" && v === data.order_id) continue; // same id twice
    if (Array.isArray(v)) {
      v.forEach((x, i) => {
        const l = itemLabel(x);
        if (!l) return;
        const cls = `${chipBase} border border-[#D99B26] bg-[#FFF5DF] text-[#7A5200]`;
        chips.push(
          l.productId ? (
            <Link key={`${k}-${i}`} to={`/products/${encodeURIComponent(l.productId)}`} title={l.title} className={`${cls} hover:underline`}>
              {l.text}
            </Link>
          ) : (
            <span key={`${k}-${i}`} title={l.title} className={cls}>
              {l.text}
            </span>
          ),
        );
      });
    } else if (v != null && typeof v === "object") {
      const outcome = (v as Record<string, unknown>).outcome;
      if (typeof outcome === "string") chips.push(<span key={k} className={`${chipBase} border border-line bg-surface-muted text-ink-secondary`}>{k}={outcome}</span>);
    } else if (v != null) {
      chips.push(<span key={k} className={`${chipBase} border border-line bg-surface-muted text-ink-secondary`}>{k}={String(v)}</span>);
    }
  }
  return chips.length ? <div className="mt-1 flex min-w-0 flex-wrap gap-1">{chips}</div> : null;
}
