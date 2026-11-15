// / → "My Aimo" front page (DESIGN_VALIO §6, my_aimo_1280.png): petrol hero, onboarding info band (0 agents), the lime
// "Ask the ordering assistant." band, a USP strip used for the six KPI tiles (values verbatim from GET /api/dashboard),
// pending approvals as bulletin cards, recent orders as collapsed order blocks, and the active agents.
import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { Bot } from "lucide-react";
import { useAgents, useApprovals, useDashboard } from "../api/hooks";
import type { Agent, Order } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { ButtonLink } from "../components/Button";
import { AskBand } from "../components/assistant/AskBand";
import { Countdown } from "../components/Countdown";
import { ErrorPanel } from "../components/ErrorPanel";
import { Money } from "../components/Money";
import { OnboardingBannerIfNoAgents } from "../components/OnboardingBanner";
import { Skeleton } from "../components/Skeleton";
import { Tag } from "../components/Badge";
import { Tooltip } from "../components/Tooltip";
import { AgentStatusDot, SpendBar, agentDisplayState } from "../components/agents/AgentBits";
import { CollapsedOrderBlock } from "../components/orders/OrderBlock";
import { firstName } from "../components/shell/shared";
import { useStrings } from "../i18n";
import { formatDate, helsinkiDate } from "../lib/dates";
import { formatCo2, formatEur, formatNumber } from "../lib/format";
import { useServerNow } from "../lib/useServerNow";

function Hero() {
  const f = useStrings().front;
  const { principal } = useAuth();
  const name = firstName(principal?.name);
  return (
    <section className="bg-navy text-white" data-testid="hero">
      <div className="container-portal flex min-h-[260px] flex-col items-center justify-center py-10 text-center md:min-h-[360px] md:py-16 lg:min-h-[420px]">
        <h1 className="font-display text-[48px] leading-[48px] tracking-[0.01em] drop-shadow-[0_0_10px_rgba(0,0,0,0.35)] md:text-[72px] md:leading-[72px] lg:text-[88px] lg:leading-[88px]">
          {name ? f.welcome(name) : f.welcomeAnon}
        </h1>
        <p className="mt-4 max-w-[760px] text-[16px] leading-[21px] md:text-[17px] md:leading-[23px]">{f.lead}</p>
      </div>
    </section>
  );
}

function UspItem({ value, caption, sub, to, testId, highlight = false }: { value: ReactNode; caption: string; sub?: ReactNode; to?: string; testId: string; highlight?: boolean }) {
  const body = (
    <>
      <div className={`tabular-nums whitespace-nowrap font-display text-[28px] leading-[34px] sm:text-[44px] sm:leading-[48px] xl:text-[56px] xl:leading-[56px] ${highlight ? "text-warning" : "text-navy"}`} data-testid={`kpi-${testId}`}>
        {value}
      </div>
      <div className="mt-2 text-[16px] font-bold leading-[21px] text-navy">{caption}</div>
      {sub && <div className="mt-0.5 text-[13px] text-ink-muted">{sub}</div>}
    </>
  );
  const cls = "block min-w-0 px-4 py-5 text-center";
  return to ? (
    <Link to={to} className={`${cls} hover:bg-white/60`}>
      {body}
    </Link>
  ) : (
    <div className={cls}>{body}</div>
  );
}

function KpiStrip() {
  const strings = useStrings();
  const t = strings.dashboard;
  const f = strings.front;
  const dash = useDashboard({ silent: true });
  const d = dash.data;
  // Portal USP strip: 1 px #DEE4E6 left borders between items on one row (≥ 992); a plain 2/3-column grid below.
  const cell = "border-line lg:border-l lg:first:border-l-0";
  return (
    <section className="bg-primary-soft" aria-label={f.kpiTitle}>
      <div className="container-portal py-8">
        {dash.isError && (
          <div className="mb-4">
            <ErrorPanel error={dash.error} title={d ? "Could not refresh the dashboard · showing the last figures" : "Could not load the dashboard"} onRetry={() => void dash.refetch()} />
          </div>
        )}
        {!d ? (
          !dash.isError && (
            <div className="grid grid-cols-2 gap-4 md:grid-cols-3 lg:grid-cols-6" aria-busy="true" aria-label="Loading KPIs">
              {Array.from({ length: 6 }, (_, i) => (
                <div key={i} className="flex flex-col items-center gap-2 py-5">
                  <Skeleton className="h-12 w-20" />
                  <Skeleton className="h-4 w-28" />
                </div>
              ))}
            </div>
          )
        ) : (
          <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6">
            <div className={cell}>
              <UspItem testId="pending" value={formatNumber(d.pending_approvals)} caption={t.kpiPending} to="/approvals" highlight={d.pending_approvals > 0} sub={d.pending_approvals > 0 ? `${t.review} →` : undefined} />
            </div>
            <div className={cell}>
              <UspItem testId="orders-today" value={formatNumber(d.orders_today)} caption={t.kpiOrdersToday} to="/orders" />
            </div>
            <div className={cell}>
              <UspItem testId="spend-today" value={formatEur(d.spend_today_eur)} caption={t.kpiSpendToday} sub={t.exclVat} />
            </div>
            <div className={cell}>
              <UspItem testId="spend-7d" value={formatEur(d.spend_7d_eur)} caption={t.kpiSpend7d} sub={t.exclVat} />
            </div>
            <div className={cell}>
              <UspItem
                testId="co2-7d"
                value={`~${formatNumber(d.co2_7d_kg, d.co2_7d_kg < 10 ? 1 : 0)}`}
                caption={t.kpiCo2}
                sub={
                  <Tooltip content={strings.quote.co2TotalBasis}>
                    <span tabIndex={0} className="cursor-help underline decoration-dotted underline-offset-2" aria-label={formatCo2(d.co2_7d_kg)}>
                      kg CO₂e · {t.estimate}
                    </span>
                  </Tooltip>
                }
              />
            </div>
            <div className={cell}>
              <UspItem testId="agents" value={formatNumber(d.active_agents)} caption={t.kpiAgents} to="/agents" />
            </div>
          </div>
        )}
      </div>
    </section>
  );
}

function SectionTitle({ children, action }: { children: ReactNode; action?: ReactNode }) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-3">
      <h2 className="h-section">{children}</h2>
      {action}
    </div>
  );
}

/** Bulletin-style card: lavender image slot with the agent mark, APPROVAL NEEDED tag + date, bold title, facts, link. */
function PendingBulletin({ order }: { order: Order }) {
  const strings = useStrings();
  const f = strings.front;
  const agent = order.created_by.kind === "agent";
  const rules = order.policy.rules.filter((r) => r.outcome !== "AUTO");
  return (
    <li className="flex min-w-0 overflow-hidden">
      <div className="flex w-24 shrink-0 items-center justify-center bg-lavender text-navy sm:w-36" aria-hidden="true">
        <Bot className="h-12 w-12 opacity-60" strokeWidth={1} />
      </div>
      <div className="min-w-0 flex-1 px-4 py-3">
        <div className="flex flex-wrap items-center gap-2">
          <Tag kind="approval">{f.approvalNeeded}</Tag>
          <span className="tabular-nums text-[14px] text-ink-muted">{formatDate(helsinkiDate(new Date(order.created_at)))}</span>
        </div>
        <h3 className="mt-3 flex flex-wrap items-center gap-x-1.5 text-[16px] font-bold leading-[21px] text-ink">
          {agent && <Bot className="h-4 w-4 text-navy" strokeWidth={1.75} aria-label="AI agent" />}
          <span>{order.created_by.name}</span>
          <span aria-hidden="true">·</span>
          <Money value={order.total_excl_vat_eur} />
        </h3>
        {agent && order.created_by.delegated_by && (
          <p className="text-[13px] text-ink-secondary">
            {strings.actor.authorizedBy} {order.created_by.delegated_by.name}
          </p>
        )}
        <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-[13px]">
          {rules.map((r) => (
            <span key={r.rule_id} className="font-mono-code font-bold text-ink">
              {r.rule_id}
            </span>
          ))}
          <Countdown to={order.approval_expires_at} className="text-[13px]" />
        </div>
        <Link to="/approvals" className="mt-3 inline-block text-[16px] text-link hover:underline">
          {f.moreInfo}
        </Link>
      </div>
    </li>
  );
}

function PendingSection() {
  const f = useStrings().front;
  const q = useApprovals({ silent: true });
  const items = q.data?.items ?? [];
  return (
    <section className="container-portal pt-12 md:pt-16" aria-labelledby="pending-title">
      <SectionTitle>
        <span id="pending-title">{f.pendingTitle}</span>
      </SectionTitle>
      {q.isLoading ? (
        <div className="grid gap-6 md:grid-cols-2">
          <Skeleton className="h-40 w-full" />
          <Skeleton className="h-40 w-full" />
        </div>
      ) : q.isError && !q.data ? (
        <ErrorPanel error={q.error} onRetry={() => void q.refetch()} />
      ) : items.length === 0 ? (
        <p className="text-[16px] text-ink-secondary">{f.pendingEmpty}</p>
      ) : (
        <ul className="grid gap-6 md:grid-cols-2" data-testid="pending-preview">
          {items.slice(0, 4).map((o) => (
            <PendingBulletin key={o.id} order={o} />
          ))}
        </ul>
      )}
    </section>
  );
}

function RecentSection() {
  const f = useStrings().front;
  const dash = useDashboard({ silent: true });
  const d = dash.data;
  return (
    <section className="container-portal pt-12 md:pt-16" aria-labelledby="recent-title">
      <SectionTitle>
        <span id="recent-title">{f.recentTitle}</span>
      </SectionTitle>
      {!d ? (
        <div className="space-y-6">
          {Array.from({ length: 3 }, (_, i) => (
            <Skeleton key={i} className="h-14 w-full" />
          ))}
        </div>
      ) : d.recent_orders.length === 0 ? (
        <p className="text-[16px] text-ink-secondary">{f.recentEmpty}</p>
      ) : (
        <div className="space-y-6" data-testid="recent-orders">
          {d.recent_orders.map((o) => (
            <CollapsedOrderBlock key={o.id} order={o} />
          ))}
        </div>
      )}
      <div className="mt-8 flex justify-center">
        <ButtonLink to="/orders" variant="primary">
          {f.showMore}
        </ButtonLink>
      </div>
    </section>
  );
}

function AgentsSection() {
  const t = useStrings().dashboard;
  const q = useAgents({ silent: true });
  const now = useServerNow(30_000);
  const active = (q.data?.items ?? []).filter((a: Agent) => agentDisplayState(a, now) === "active");
  if (q.isLoading || active.length === 0) return null;
  return (
    <section className="container-portal pt-12 md:pt-16" aria-labelledby="agents-title">
      <SectionTitle
        action={
          <Link to="/agents" className="text-[16px] font-bold text-link hover:underline">
            {t.manageAgents}
          </Link>
        }
      >
        <span id="agents-title">{t.agentsTitle}</span>
      </SectionTitle>
      <ul className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {active.slice(0, 4).map((a) => (
          <li key={a.id}>
            <Link to={`/agents/${encodeURIComponent(a.id)}`} className="block rounded border border-line p-4 hover:border-navy">
              <div className="flex items-center gap-2">
                <AgentStatusDot state="active" />
                <span className="truncate text-[16px] font-bold text-navy">{a.name}</span>
              </div>
              <SpendBar spent={a.spent_today_eur} limit={a.daily_spend_limit_eur} className="mt-3" />
            </Link>
          </li>
        ))}
      </ul>
    </section>
  );
}

export default function DashboardPage() {
  return (
    <div className="pb-16">
      <Hero />
      <OnboardingBannerIfNoAgents className="border-t border-white/20" />
      <AskBand />
      <KpiStrip />
      <PendingSection />
      <RecentSection />
      <AgentsSection />
    </div>
  );
}
