// /account: Client Company Profile & Settings — organization identity, direct Agent settings & permissions,
// Telegram identity pairing, Category Brand Preferences, and Management Promotions.
import { Link } from "react-router-dom";
import { Bot } from "lucide-react";
import { useAgents, useAssistantSettings, useMe, useNegotiationSettings } from "../api/hooks";
import type { Agent } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { SettingsLayout } from "../components/AccountLayout";
import { ButtonLink } from "../components/Button";
import { Card, CardHeader } from "../components/Card";
import { CopyChip } from "../components/CopyChip";
import { ErrorPanel } from "../components/ErrorPanel";
import { Field, InputPill, TextInput } from "../components/FormField";
import { Skeleton } from "../components/Skeleton";
import { Stripe } from "../components/Stripe";
import { BrandPreferencesPanel } from "../components/account/BrandPreferencesPanel";
import { AgentStatusDot, QuoteOnlyBadge, agentDisplayState, agentStateLabel } from "../components/agents/AgentBits";
import { TelegramOptInCard } from "../components/approvals/TelegramOptInCard";
import { PromotionsPanel } from "../components/promotions/PromotionsPanel";
import { formatEurCompact } from "../lib/format";
import { useServerNow } from "../lib/useServerNow";
import { useStrings } from "../i18n";

export default function AccountPage() {
  const { isUser } = useAuth();
  const strings = useStrings();
  const p = strings.portal;
  const t = strings.account;
  const me = useMe({ silent: true });
  const agentsQ = useAgents({ enabled: isUser, silent: true });
  const assistantSettings = useAssistantSettings({ silent: true });
  const negotiationSettings = useNegotiationSettings({ silent: true });
  const now = useServerNow(30_000);

  const d = me.data;
  const assuranceScore = assistantSettings.data?.order_assurance_threshold ?? 80;
  const negotiationAllowed = negotiationSettings.data?.allow_price_negotiation ?? false;
  const agentList: Agent[] = isUser
    ? (agentsQ.data?.items ?? [])
    : d?.agent
      ? [d.agent]
      : [];

  return (
    <SettingsLayout
      breadcrumb={[{ label: p.myAimo, to: "/" }, { label: p.settings }, { label: t.title }]}
      title={t.title}
      subtitle={t.subtitle(d?.company.name)}
    >
      <div className="max-w-[820px] space-y-6">
        {me.isError ? (
          <ErrorPanel error={me.error} onRetry={() => void me.refetch()} />
        ) : !d ? (
          <div className="space-y-6" aria-busy="true" aria-label={strings.common.loading}>
            {Array.from({ length: 4 }, (_, i) => (
              <Skeleton key={i} className="h-[70px] w-full" />
            ))}
          </div>
        ) : (
          <>
            <Card>
              <CardHeader title={t.profileCardTitle} description={t.profileCardSubtitle} />
              <div className="mt-4 grid gap-4 md:grid-cols-2">
                <Field label={t.company}>{(f) => <TextInput {...f} readOnly value={d.company.name} />}</Field>
                <Field label={t.name}>{(f) => <TextInput {...f} readOnly value={d.principal.name} />}</Field>
                <Field label={t.userId}>
                  {(f) => (
                    <div className="relative">
                      <TextInput {...f} readOnly value={d.principal.id} className="font-mono-code pr-28" />
                      <InputPill tone={d.principal.kind === "agent" ? "grey" : "lime"}>
                        {d.principal.kind === "agent" ? t.kindAgent : t.kindUser}
                      </InputPill>
                    </div>
                  )}
                </Field>
                <div className="flex min-w-0 max-w-full flex-wrap items-center gap-2 self-end pb-2 text-[14px] text-ink-secondary">
                  <span className="font-bold text-ink">{t.companyId}</span>
                  <CopyChip value={d.company.id} />
                </div>
              </div>
            </Card>

            <Card data-testid="account-agent-settings-card">
              <CardHeader
                title={t.agentCardTitle}
                description={t.agentCardSubtitle}
                actions={
                  <div className="flex flex-wrap items-center gap-2">
                    <ButtonLink to="/agents" size="sm" variant="outline" data-testid="account-manage-agents-link">
                      {t.manageAgentsAssurance}
                    </ButtonLink>
                    {isUser && (
                      <ButtonLink to="/agents/new" size="sm" variant="primary" data-testid="account-create-agent-link">
                        {t.createAgentCta}
                      </ButtonLink>
                    )}
                  </div>
                }
              />

              <div className="mt-4 flex flex-wrap items-center gap-2 border-y border-line bg-primary-soft px-3 py-2.5 text-xs">
                <Link
                  to="/agents"
                  className="inline-flex items-center rounded-none bg-navy px-2.5 py-1 font-bold tabular-nums text-white hover:opacity-90"
                >
                  {t.agentAssurancePill(assuranceScore)}
                </Link>
                <span className="inline-flex flex-wrap items-center gap-1.5 text-ink-secondary">
                  <span className="font-semibold text-ink">{t.agentNegotiationLabel}</span>
                  <span
                    className={`rounded-none px-2 py-0.5 text-[11px] font-bold uppercase tracking-[0.04em] ${
                      negotiationAllowed ? "bg-lime text-navy" : "bg-muted text-ink-secondary"
                    }`}
                  >
                    {negotiationAllowed ? t.agentNegotiationAllowed : t.agentNegotiationDisallowed}
                  </span>
                  <Link
                    to="/admin"
                    data-testid="account-negotiation-console-link"
                    className="font-bold text-navy underline underline-offset-2 hover:text-primary"
                  >
                    {t.agentNegotiationConsoleLink} →
                  </Link>
                </span>
              </div>

              <div className="mt-4 space-y-2.5">
                {!isUser && (
                  <Stripe kind="neutral" compact>
                    {t.agentSessionReadOnly}
                  </Stripe>
                )}
                {isUser && agentsQ.isError ? (
                  <ErrorPanel error={agentsQ.error} onRetry={() => void agentsQ.refetch()} />
                ) : isUser && agentsQ.isLoading ? (
                  <Skeleton className="h-20 w-full" />
                ) : agentList.length === 0 ? (
                  <p className="text-sm text-ink-secondary">{strings.agents.emptyBody}</p>
                ) : (
                  agentList.map((a) => {
                    const state = agentDisplayState(a, now);
                    return (
                      <div
                        key={a.id}
                        className="flex flex-wrap items-center justify-between gap-3 rounded border border-line bg-white px-3.5 py-3"
                      >
                        <div className="min-w-0 flex-1 space-y-1">
                          <div className="flex flex-wrap items-center gap-2">
                            <AgentStatusDot state={state} />
                            <span className="inline-flex items-center gap-1.5 font-bold text-navy">
                              <Bot className="h-3.5 w-3.5 shrink-0" strokeWidth={1.75} aria-hidden="true" />
                              <span className="truncate">{a.name}</span>
                            </span>
                            <span className="rounded-none bg-surface px-1.5 py-0.5 text-[10.5px] font-bold uppercase tracking-[0.04em] text-ink-secondary">
                              {agentStateLabel(state, strings.agents)}
                            </span>
                            {!a.can_place_orders && <QuoteOnlyBadge />}
                          </div>
                          <p className="tabular-nums text-xs text-ink-secondary">
                            {t.agentLimitsSummary(
                              formatEurCompact(a.auto_approve_limit_eur),
                              formatEurCompact(a.max_order_value_eur),
                              formatEurCompact(a.daily_spend_limit_eur),
                            )}
                          </p>
                        </div>
                        {isUser && (
                          <ButtonLink
                            to={`/agents/${encodeURIComponent(a.id)}`}
                            size="sm"
                            variant="outline"
                            data-testid={`account-edit-agent-${a.id}`}
                          >
                            {t.editAgentSettings}
                          </ButtonLink>
                        )}
                      </div>
                    );
                  })
                )}
              </div>
            </Card>

            <TelegramOptInCard />
            <BrandPreferencesPanel />
            <PromotionsPanel />
          </>
        )}
      </div>
    </SettingsLayout>
  );
}
