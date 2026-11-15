import { shortId } from "../lib/ids";
import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { Ban, Bot, Check, Copy, KeyRound, Power } from "lucide-react";
import { toApiError } from "../api/client";
import { useAgent, useAgentActivity, useOrders, useRotateAgentSecret, useUpdateAgent } from "../api/hooks";
import type { Agent, AgentWithSecret, AuditEvent } from "../api/types";
import { SettingsLayout } from "../components/AccountLayout";
import { ActorPill } from "../components/ActorPill";
import { Tag } from "../components/Badge";
import { Button, ButtonLink } from "../components/Button";
import { Card, CardHeader } from "../components/Card";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { CopyChip } from "../components/CopyChip";
import { EmptyState } from "../components/EmptyState";
import { ErrorMeta, ErrorPanel } from "../components/ErrorPanel";
import { InputPill, inputClass } from "../components/FormField";
import { Skeleton, SkeletonRows } from "../components/Skeleton";
import { Stripe } from "../components/Stripe";
import { UnderlineTabs } from "../components/Tabs";
import { toast } from "../components/Toast";
import { AgentForm, agentToForm, formToPatch } from "../components/agents/AgentForm";
import { PolicyCard, PolicyLines, QuoteOnlyBadge, SpendBar, agentDisplayState, agentPolicyLines, agentStateLabel } from "../components/agents/AgentBits";
import { SecretModal } from "../components/agents/SecretModal";
import { CollapsedOrderBlock } from "../components/orders/OrderBlock";
import { copyToClipboard } from "../lib/clipboard";
import { formatDateTime, formatDayDate, formatRelative, helsinkiDate } from "../lib/dates";
import { useServerNow } from "../lib/useServerNow";
import { useStrings } from "../i18n";

export default function AgentDetailPage() {
  const strings = useStrings();
  const t = strings.agents;
  const { id } = useParams();
  const agent = useAgent(id, { silent: true });
  const now = useServerNow(30_000);

  if (agent.isLoading) {
    return (
      <SettingsLayout
        breadcrumb={[
          { label: strings.portal.myAimo, to: "/account" },
          { label: t.title, to: "/agents" },
        ]}
        title={t.title}
      >
        <div aria-busy="true" aria-label="Loading agent" className="max-w-[700px] space-y-4">
          <Skeleton className="h-8 w-72" />
          <Skeleton className="h-80 w-full" />
        </div>
      </SettingsLayout>
    );
  }
  if (agent.isError || !agent.data) {
    const notFound = toApiError(agent.error).code === "NOT_FOUND";
    return (
      <SettingsLayout
        breadcrumb={[
          { label: strings.portal.myAimo, to: "/account" },
          { label: t.title, to: "/agents" },
          { label: id ?? "" },
        ]}
        title={t.notFound}
      >
        {notFound ? (
          <Card>
            <EmptyState
              icon={<Bot className="h-5 w-5" strokeWidth={1.75} />}
              title={t.notFound}
              description={
                <>
                  <span className="font-mono-code">{id}</span>
                  <ErrorMeta error={agent.error} />
                </>
              }
              action={
                <ButtonLink to="/agents" variant="outline">
                  {t.back}
                </ButtonLink>
              }
            />
          </Card>
        ) : (
          <ErrorPanel error={agent.error} title="Could not load agent" onRetry={() => void agent.refetch()} />
        )}
      </SettingsLayout>
    );
  }
  // key: a different :id must not inherit the previous agent's dialogs, one-time secret or mutation errors.
  return <AgentDetail key={agent.data.id} agent={agent.data} now={now} />;
}

function AgentDetail({ agent, now }: { agent: Agent; now: number }) {
  const strings = useStrings();
  const t = strings.agents;
  const state = agentDisplayState(agent, now);
  const update = useUpdateAgent({ silent: true });
  const rotate = useRotateAgentSecret({ silent: true });
  const [confirm, setConfirm] = useState<null | "disable" | "enable" | "rotate">(null);
  const [secret, setSecret] = useState<AgentWithSecret | null>(null);
  const [copiedId, setCopiedId] = useState(false);
  const [tab, setTab] = useState<"activity" | "orders">("activity");
  const initial = agentToForm(agent);

  const openConfirm = (kind: "disable" | "enable" | "rotate") => {
    // The limits form shares `update`: never show its old save error inside the kill-switch / rotate dialog.
    update.reset();
    rotate.reset();
    setConfirm(kind);
  };

  const closeConfirm = () => {
    setConfirm(null);
    update.reset();
    rotate.reset();
  };

  const runConfirm = async () => {
    try {
      if (confirm === "rotate") {
        const r = await rotate.mutateAsync(agent.id);
        setConfirm(null);
        setSecret(r);
      } else if (confirm) {
        await update.mutateAsync({ id: agent.id, patch: { status: confirm === "disable" ? "disabled" : "active" } });
        toast.success(confirm === "disable" ? `${agent.name} disabled` : `${agent.name} re-enabled`);
        setConfirm(null);
      }
    } catch {
      /* rendered inside the dialog */
    }
  };

  const statusPillTone = state === "active" ? "lime" : state === "disabled" || state === "quarantined" ? "red" : "yellow";
  const statusLabel = agentStateLabel(state, t);

  return (
    <SettingsLayout
      breadcrumb={[
        { label: strings.portal.myAimo, to: "/account" },
        { label: t.title, to: "/agents" },
        { label: agent.name },
      ]}
      title={
        <span className="inline-flex flex-wrap items-center gap-3">
          <span>{agent.name}</span>
          <Tag kind="agent" icon={<Bot className="h-3.5 w-3.5" strokeWidth={1.75} />}>
            Agent
          </Tag>
          {!agent.can_place_orders && <QuoteOnlyBadge />}
        </span>
      }
      subtitle={agent.description ?? undefined}
    >
      {state === "disabled" && (
        <Stripe kind="error" accent role="status" className="mb-5">
          <strong>Kill switch is on.</strong> Every request from this agent is refused with <span className="font-mono-code">AGENT_DISABLED</span>.
        </Stripe>
      )}
      {state === "quarantined" && (
        <Stripe kind="error" accent role="status" className="mb-5">
          <strong>Agent quarantined.</strong> Automated orders from this agent are paused pending supervisor review.
        </Stripe>
      )}
      {state === "expired" && (
        <Stripe kind="warning" accent role="status" className="mb-5">
          <strong>Credentials expired.</strong> Update the expiry date below or rotate credentials to restore access.
        </Stripe>
      )}

      {/* Settings -> My information style form (max 700px) */}
      <div className="max-w-[700px] space-y-6">
        <div className="space-y-4">
          <div>
            <label htmlFor="agent-id-readonly" className="mb-1.5 block text-[14px] font-semibold text-ink">
              {t.agentId}
            </label>
            <div className="flex flex-col gap-2 sm:relative sm:flex-row sm:items-center">
              <input
                id="agent-id-readonly"
                readOnly
                value={agent.id}
                className={inputClass(false, "font-mono-code bg-primary-soft text-ink sm:pr-44")}
              />
              <div className="flex items-center justify-end gap-1.5 sm:absolute sm:right-2">
                <InputPill tone={statusPillTone}>{statusLabel}</InputPill>
                <button
                  type="button"
                  onClick={async () => {
                    await copyToClipboard(agent.id);
                    setCopiedId(true);
                    window.setTimeout(() => setCopiedId(false), 1500);
                  }}
                  aria-label={`Copy ${agent.id}`}
                  className="inline-flex h-7 items-center gap-1 rounded border border-line bg-white px-2 text-xs font-semibold text-[#005670] hover:bg-primary-soft"
                >
                  {copiedId ? <Check className="h-3 w-3" strokeWidth={2} /> : <Copy className="h-3 w-3" strokeWidth={1.75} />}
                  {copiedId ? strings.common.copied : strings.common.copy}
                </button>
              </div>
            </div>
          </div>

          <div className="grid gap-4 sm:grid-cols-2">
            <div>
              <label htmlFor="agent-authorized-by" className="mb-1.5 block text-[14px] font-semibold text-ink">
                {t.authorizedBy}
              </label>
              <input
                id="agent-authorized-by"
                readOnly
                value={agent.delegated_by.name}
                className={inputClass(false, "bg-primary-soft text-ink")}
              />
            </div>
            <div>
              <p className="mb-1.5 text-[14px] font-semibold text-ink">{t.colDaily}</p>
              <div className="flex h-[45px] items-center rounded-[2px] border border-line bg-primary-soft px-3">
                <SpendBar spent={agent.spent_today_eur} limit={agent.daily_spend_limit_eur} className="w-full" />
              </div>
            </div>
          </div>

          <div className="flex flex-wrap gap-x-5 gap-y-1 text-xs text-ink-muted">
            <span className="tabular-nums">
              {t.created} {formatDateTime(agent.created_at)}
            </span>
            <span className="tabular-nums">
              {t.lastUsed} {agent.last_used_at ? formatRelative(agent.last_used_at, now) : t.never}
            </span>
            {agent.expires_at && (
              <span className="tabular-nums">
                {t.expiresAt} {formatDayDate(helsinkiDate(new Date(agent.expires_at)))}
              </span>
            )}
          </div>
        </div>

        <PolicyCard title={`What ${agent.name} may do`}>
          <PolicyLines lines={agentPolicyLines(agent, now)} />
        </PolicyCard>

        <section className="border-t border-line pt-6">
          <CardHeader title={t.limitsTitle} description={t.limitsLead} className="mb-4" />
          <AgentForm
            key={JSON.stringify(initial)}
            mode="edit"
            initial={initial}
            today={helsinkiDate(now)}
            submitLabel={t.saveCta}
            onSubmit={async (v) => {
              const patch = formToPatch(v, agent);
              if (Object.keys(patch).length === 0) return;
              await update.mutateAsync({ id: agent.id, patch });
              toast.success(t.saved, agent.name);
            }}
            footerExtra={
              <div className="flex flex-wrap items-center gap-3">
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  icon={<KeyRound className="h-4 w-4" strokeWidth={1.75} />}
                  onClick={() => openConfirm("rotate")}
                >
                  {t.rotateCta}
                </Button>
                {agent.status === "active" ? (
                  <Button
                    type="button"
                    variant="danger-text"
                    size="sm"
                    icon={<Ban className="h-4 w-4" strokeWidth={1.75} />}
                    onClick={() => openConfirm("disable")}
                  >
                    {t.disableCta}
                  </Button>
                ) : (
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    icon={<Power className="h-4 w-4" strokeWidth={1.75} />}
                    onClick={() => openConfirm("enable")}
                  >
                    {t.enableCta}
                  </Button>
                )}
              </div>
            }
          />
        </section>
      </div>

      {/* Activity / Orders tabs below */}
      <div className="mt-8 space-y-4">
        <UnderlineTabs
          items={[
            { id: "activity", label: t.activityTitle },
            { id: "orders", label: t.ordersTitle },
          ]}
          active={tab}
          onChange={(next) => setTab(next as "activity" | "orders")}
          ariaLabel="Agent activity and orders"
        />
        {tab === "activity" ? <AgentActivity agentId={agent.id} now={now} /> : <AgentOrders agentId={agent.id} />}
      </div>

      <ConfirmDialog
        open={confirm != null}
        title={confirm === "disable" ? t.disableConfirmTitle : confirm === "enable" ? t.enableConfirmTitle : `${t.rotateTitle}?`}
        confirmLabel={confirm === "disable" ? t.disableCta : confirm === "enable" ? t.enableCta : t.rotateCta}
        tone={confirm === "enable" ? "primary" : "danger"}
        busy={update.isPending || rotate.isPending}
        error={update.error ?? rotate.error}
        onCancel={closeConfirm}
        onConfirm={() => void runConfirm()}
      >
        <p>
          {confirm === "disable"
            ? t.disableConfirmBody(agent.name)
            : confirm === "enable"
              ? t.enableConfirmBody(agent.name)
              : t.rotateConfirmBody(agent.name)}
        </p>
      </ConfirmDialog>
      <SecretModal data={secret} onDone={() => setSecret(null)} title="New agent credentials" />
    </SettingsLayout>
  );
}

/** Orders created by this agent rendered as collapsed OrderBlocks. */
function AgentOrders({ agentId }: { agentId: string }) {
  const t = useStrings().agents;
  // Server-side per-agent filter (`agent_id`, backend M8); the client filter stays as a guard for servers without it.
  const orders = useOrders({ agent_id: agentId, limit: 100 }, { silent: true });
  const items = (orders.data?.items ?? []).filter((o) => o.created_by.kind === "agent" && o.created_by.id === agentId);
  if (orders.isLoading) {
    return (
      <Card>
        <SkeletonRows rows={3} />
      </Card>
    );
  }
  if (orders.isError) {
    return <ErrorPanel error={orders.error} title="Could not load orders" onRetry={() => void orders.refetch()} />;
  }
  if (items.length === 0) {
    return (
      <Card>
        <EmptyState title={t.ordersEmpty} />
      </Card>
    );
  }
  return (
    <div className="space-y-3">
      {items.map((o) => (
        <CollapsedOrderBlock key={o.id} order={o} />
      ))}
    </div>
  );
}

function AgentActivity({ agentId, now }: { agentId: string; now: number }) {
  const t = useStrings().agents;
  const activity = useAgentActivity(agentId, { silent: true });
  const items = [...(activity.data?.items ?? [])].sort((a, b) => b.at.localeCompare(a.at));
  return (
    <Card>
      <CardHeader title={t.activityTitle} description="Audit events where this agent is the actor or the subject" className="mb-3" />
      {activity.isLoading ? (
        <SkeletonRows rows={4} rowClass="h-12" />
      ) : activity.isError ? (
        <ErrorPanel error={activity.error} title="Could not load activity" onRetry={() => void activity.refetch()} />
      ) : items.length === 0 ? (
        <EmptyState title={t.activityEmpty} />
      ) : (
        <ol className="relative ml-1.5 border-l-2 border-line">
          {items.map((e) => (
            <ActivityItem key={e.id} e={e} now={now} />
          ))}
        </ol>
      )}
    </Card>
  );
}

function ActivityItem({ e, now }: { e: AuditEvent; now: number }) {
  const err = e.outcome === "error";
  return (
    <li className="relative min-w-0 pb-3 pl-4 last:pb-0">
      <span className={`absolute -left-[5px] top-1.5 h-2 w-2 rounded-full ${err ? "bg-danger" : "bg-navy"}`} aria-hidden="true" />
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex min-w-0 flex-wrap items-center gap-2">
          <span className={`font-mono-code text-[12px] font-medium ${err ? "text-danger" : "text-ink"}`}>{e.action}</span>
          {err && <span className="rounded-none border border-[#B41E17]/40 bg-[#FDECEB] px-1.5 py-0.5 text-[10.5px] font-bold uppercase tracking-[0.04em] text-[#B41E17]">error</span>}
          <ActorPill actor={e.actor} compact />
        </div>
        <CopyChip value={e.request_id} className="min-w-0" />
      </div>
      <p className="tabular-nums mt-0.5 break-words text-xs text-ink-muted" title={e.at}>
        {formatDateTime(e.at)} · {formatRelative(e.at, now)}
        {e.entity_type && e.entity_id ? (
          <>
            {" · "}
            {e.entity_type === "order" ? (
              <Link to={`/orders/${encodeURIComponent(e.entity_id)}`} title={e.entity_id} className="font-mono-code text-primary hover:underline">
                {shortId(e.entity_id)}
              </Link>
            ) : (
              <span className="font-mono-code" title={e.entity_id}>
                {e.entity_type}:{shortId(e.entity_id)}
              </span>
            )}
          </>
        ) : null}
      </p>
    </li>
  );
}
