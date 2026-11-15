import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { Bot, ChevronRight, Plus } from "lucide-react";
import { useAgents, useCreateAgent } from "../api/hooks";
import type { Agent, AgentWithSecret } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { SettingsLayout } from "../components/AccountLayout";
import { Button } from "../components/Button";
import { Card } from "../components/Card";
import { EmptyState } from "../components/EmptyState";
import { ErrorPanel } from "../components/ErrorPanel";
import { Modal } from "../components/Modal";
import { Skeleton } from "../components/Skeleton";
import { tableClass, tableWrapClass, tdClass, tdNumClass, theadClass, thClass, thNumClass, trClass } from "../components/Table";
import { AgentForm, DEFAULT_AGENT_FORM, formToRequest } from "../components/agents/AgentForm";
import {
  AgentStatusDot,
  PolicyCard,
  QuoteOnlyBadge,
  SpendBar,
  agentDisplayState,
  agentStateLabel,
  agentPolicyHeadline,
  categoriesLabel,
} from "../components/agents/AgentBits";
import { SecretModal } from "../components/agents/SecretModal";
import { AgentAnomaliesBanner } from "../components/agents/AgentAnomaliesBanner";
import { UniversalAgentDocsCard } from "../components/agents/UniversalAgentDocsCard";
import { OrderAssuranceSettingsCard } from "../components/account/OrderAssuranceSettingsCard";
import { helsinkiDate, formatRelative } from "../lib/dates";
import { formatEurCompact } from "../lib/format";
import { useServerNow } from "../lib/useServerNow";
import { useStrings } from "../i18n";

export default function AgentsPage() {
  const strings = useStrings();
  const t = strings.agents;
  const agents = useAgents({ silent: true });
  const [params, setParams] = useSearchParams();
  const createOpen = params.get("create") === "1";
  const [secret, setSecret] = useState<AgentWithSecret | null>(null);
  const now = useServerNow(30_000);

  const openCreate = () =>
    setParams((p) => {
      const n = new URLSearchParams(p);
      n.set("create", "1");
      return n;
    });
  const closeCreate = () =>
    setParams(
      (p) => {
        const n = new URLSearchParams(p);
        n.delete("create");
        return n;
      },
      { replace: true },
    );

  const items = agents.data?.items ?? [];

  return (
    <SettingsLayout
      breadcrumb={[
        { label: strings.portal.myAimo, to: "/account" },
        { label: strings.portal.settings, to: "/account" },
        { label: t.title },
      ]}
      title={t.title}
      subtitle={t.subtitle}
    >
      <AgentAnomaliesBanner className="mb-5" />
      <UniversalAgentDocsCard agents={items} className="mb-5" />
      <OrderAssuranceSettingsCard className="mb-5" />
      <PolicyCard className="mb-5">
        {agents.isLoading ? (
          <Skeleton className="h-10 w-full" />
        ) : items.length > 0 ? (
          <ul className="divide-y divide-line">
            {items.map((a) => (
              <li key={a.id} className="py-2 first:pt-0 last:pb-0">
                <AgentPolicySentence agent={a} now={now} />
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-sm text-ink-secondary">
            Example: Orders up to €500 are placed automatically. €500–€5,000 need your approval. Over €5,000 are blocked.
          </p>
        )}
      </PolicyCard>

      <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
        <p className="text-xs font-bold uppercase tracking-[0.04em] text-ink-muted">
          {items.length} {items.length === 1 ? "agent" : "agents"}
        </p>
        <Button variant="outline" icon={<Plus className="h-4 w-4" strokeWidth={2} />} onClick={openCreate}>
          {t.newAgent}
        </Button>
      </div>

      {agents.isError ? (
        <ErrorPanel error={agents.error} title="Could not load agents" onRetry={() => void agents.refetch()} />
      ) : agents.isLoading ? (
        <Card padded={false}>
          <div className="space-y-3 p-4" aria-busy="true" aria-label="Loading agents">
            {[0, 1, 2].map((i) => (
              <Skeleton key={i} className="h-12 w-full" />
            ))}
          </div>
        </Card>
      ) : items.length === 0 ? (
        <Card>
          <EmptyState
            icon={<Bot className="h-5 w-5" strokeWidth={1.75} />}
            title={t.emptyTitle}
            description={t.emptyBody}
            action={
              <Button variant="primary" icon={<Plus className="h-4 w-4" strokeWidth={2} />} onClick={openCreate}>
                {t.createCta}
              </Button>
            }
          />
        </Card>
      ) : (
        <>
          <AgentsTable agents={items} now={now} />
          <AgentCards agents={items} now={now} />
        </>
      )}

      <CreateAgentModal
        open={createOpen}
        onClose={closeCreate}
        onCreated={(r) => {
          closeCreate();
          setSecret(r);
        }}
      />
      <SecretModal data={secret} onDone={() => setSecret(null)} />
    </SettingsLayout>
  );
}

/** One plain-English line per agent inside the neutral policy stripe. */
function AgentPolicySentence({ agent, now }: { agent: Agent; now: number }) {
  const headline = agentPolicyHeadline(agent, now);
  return (
    <p className="text-sm text-ink">
      <Link to={`/agents/${encodeURIComponent(agent.id)}`} className="mr-1.5 inline-flex items-center gap-1 font-semibold text-navy hover:underline">
        <Bot className="h-3.5 w-3.5" strokeWidth={1.75} />
        {agent.name}:
      </Link>
      <span className="tabular-nums text-ink-secondary">{headline}</span>
    </p>
  );
}

function LastUsed({ iso, now }: { iso: string | null; now: number }) {
  const t = useStrings().agents;
  return <span className="tabular-nums text-ink-secondary">{iso ? formatRelative(iso, now) : t.never}</span>;
}

function AgentsTable({ agents, now }: { agents: Agent[]; now: number }) {
  const t = useStrings().agents;
  return (
    <div className={`hidden md:block ${tableWrapClass}`}>
      <table className={tableClass}>
        <thead className={theadClass}>
          <tr>
            <th className={thClass}>{t.colAgent}</th>
            <th className={thNumClass}>{t.colAuto}</th>
            <th className={thNumClass}>{t.colMax}</th>
            <th className={thClass}>{t.colDaily}</th>
            <th className={thClass}>{t.colCategories}</th>
            <th className={thClass}>{t.colLastUsed}</th>
            <th className={`${thClass} w-8`} />
          </tr>
        </thead>
        <tbody>
          {agents.map((a) => {
            const state = agentDisplayState(a, now);
            return (
              <tr key={a.id} className={`group ${trClass} ${state !== "active" ? "bg-[#F9FBFC]" : ""}`}>
                <td className={tdClass}>
                  <Link to={`/agents/${encodeURIComponent(a.id)}`} className="flex items-center gap-2 font-semibold text-navy hover:underline">
                    <AgentStatusDot state={state} />
                    <span className="truncate">{a.name}</span>
                  </Link>
                  <div className="mt-0.5 flex flex-wrap items-center gap-1.5 pl-4">
                    <span className="font-mono-code text-[11px] text-ink-muted">{a.id}</span>
                    {state !== "active" && <span className="text-[11px] font-semibold uppercase text-danger">{agentStateLabel(state, t)}</span>}
                    {!a.can_place_orders && <QuoteOnlyBadge />}
                  </div>
                </td>
                <td className={tdNumClass}>{formatEurCompact(a.auto_approve_limit_eur)}</td>
                <td className={tdNumClass}>{formatEurCompact(a.max_order_value_eur)}</td>
                <td className={tdClass}>
                  <SpendBar spent={a.spent_today_eur} limit={a.daily_spend_limit_eur} className="max-w-[180px]" />
                </td>
                <td className={`${tdClass} text-ink-secondary`}>{categoriesLabel(a)}</td>
                <td className={tdClass}>
                  <LastUsed iso={a.last_used_at} now={now} />
                </td>
                <td className={`${tdClass} pr-3 text-right`}>
                  <ChevronRight className="inline h-4 w-4 text-ink-muted group-hover:text-navy" strokeWidth={1.75} />
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

/** Mobile (< md): one card per agent. */
function AgentCards({ agents, now }: { agents: Agent[]; now: number }) {
  const t = useStrings().agents;
  return (
    <ul className="space-y-3 md:hidden">
      {agents.map((a) => {
        const state = agentDisplayState(a, now);
        return (
          <li key={a.id}>
            <Link to={`/agents/${encodeURIComponent(a.id)}`} className="block rounded border border-line bg-white p-4 active:bg-surface-muted">
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <p className="flex items-center gap-2 text-[14px] font-semibold text-ink">
                    <AgentStatusDot state={state} />
                    <span className="truncate">{a.name}</span>
                  </p>
                  <p className="font-mono-code mt-0.5 pl-4 text-[11px] text-ink-muted">{a.id}</p>
                </div>
                <ChevronRight className="mt-0.5 h-4 w-4 shrink-0 text-ink-muted" strokeWidth={1.75} />
              </div>
              <dl className="tabular-nums mt-3 grid grid-cols-3 gap-2 text-xs">
                <div>
                  <dt className="text-ink-muted">{t.colAuto}</dt>
                  <dd className="font-semibold text-ink">{formatEurCompact(a.auto_approve_limit_eur)}</dd>
                </div>
                <div>
                  <dt className="text-ink-muted">{t.colMax}</dt>
                  <dd className="font-semibold text-ink">{formatEurCompact(a.max_order_value_eur)}</dd>
                </div>
                <div>
                  <dt className="text-ink-muted">{t.colCategories}</dt>
                  <dd className="font-semibold text-ink">{categoriesLabel(a)}</dd>
                </div>
              </dl>
              <SpendBar spent={a.spent_today_eur} limit={a.daily_spend_limit_eur} className="mt-3" />
              <div className="mt-2 flex flex-wrap items-center justify-between gap-2 text-xs">
                <span className="text-ink-muted">
                  {t.colLastUsed}: <LastUsed iso={a.last_used_at} now={now} />
                </span>
                {!a.can_place_orders && <QuoteOnlyBadge />}
              </div>
            </Link>
          </li>
        );
      })}
    </ul>
  );
}

function CreateAgentModal({ open, onClose, onCreated }: { open: boolean; onClose: () => void; onCreated: (r: AgentWithSecret) => void }) {
  const t = useStrings().agents;
  const { principal } = useAuth();
  const create = useCreateAgent({ silent: true });
  const now = useServerNow(60_000);
  return (
    <Modal open={open} onClose={() => !create.isPending && onClose()} title={t.createTitle} size="lg">
      <AgentForm
        key={open ? "open" : "closed"}
        mode="create"
        initial={DEFAULT_AGENT_FORM}
        today={helsinkiDate(now)}
        authorizedBy={principal?.name}
        submitLabel={t.createCta}
        onCancel={onClose}
        onSubmit={async (v) => {
          const r = await create.mutateAsync(formToRequest(v));
          onCreated(r);
        }}
      />
    </Modal>
  );
}
