import { Link } from "react-router-dom";
import { ArrowRight, Gauge, Lock, ShieldAlert } from "lucide-react";
import type { GovernanceInvariantOut, PolicyRule } from "../api/types";
import { classifyRuleGate, GOVERNANCE_GATES, inferRemediation, isRuleOverridable, type GovernanceGateId } from "../domain/governanceGates";
import { useI18n } from "../i18n";
import { Badge } from "./Badge";

type RuleEntry = {
  rule_id: string;
  outcome: string;
  gate: GovernanceGateId;
  overridable: boolean;
  remediation?: ReturnType<typeof inferRemediation>;
};

/**
 * Shared 3-Gate Governance Funnel + 1-Click Remediation strip rendered on ApprovalCard and /audit.
 * Derives gate classification and remediation links client-side whenever backend metadata is omitted.
 */
export function GovernanceFunnelStrip({
  rules,
  invariants,
  agentId,
  compact = false,
  className = "",
}: {
  rules?: PolicyRule[];
  invariants?: GovernanceInvariantOut[];
  agentId?: string;
  compact?: boolean;
  className?: string;
}) {
  const { locale, strings } = useI18n();
  const g = strings.governanceFunnel;

  const entries: RuleEntry[] = rules
    ? rules.map((r) => ({
        rule_id: r.rule_id,
        outcome: r.outcome,
        gate: classifyRuleGate(r.rule_id, r.gate),
        overridable: isRuleOverridable(r.rule_id, r.outcome, r.overridable_via_approval),
        remediation: inferRemediation(r, { agentId, locale }),
      }))
    : (invariants ?? []).map((inv) => ({
        rule_id: inv.rule_id,
        outcome: inv.outcome,
        gate: classifyRuleGate(inv.rule_id, inv.gate),
        overridable: isRuleOverridable(inv.rule_id, inv.outcome, inv.overridable_via_approval),
        remediation: null,
      }));

  if (entries.length === 0) return null;

  const gateMeta: Record<
    GovernanceGateId,
    { title: string; question: string; Icon: typeof Lock; border: string; iconCls: string }
  > = {
    SAFETY_LOCK: {
      title: g.gate1Title,
      question: g.gate1Question,
      Icon: Lock,
      border: "border-danger/30 bg-red-soft/50",
      iconCls: "text-danger",
    },
    OPERATIONAL_SANITY: {
      title: g.gate2Title,
      question: g.gate2Question,
      Icon: ShieldAlert,
      border: "border-warning/40 bg-yellow-soft/60",
      iconCls: "text-red-strong",
    },
    AUTONOMY_ENVELOPE: {
      title: g.gate3Title,
      question: g.gate3Question,
      Icon: Gauge,
      border: "border-navy/25 bg-primary-soft/60",
      iconCls: "text-navy",
    },
  };

  const remediationLabel = (kind: "recipe" | "substitutes" | "limits" | "diet", custom?: string) => {
    if (custom) return custom;
    if (kind === "recipe") return g.remediateRecipe;
    if (kind === "substitutes") return g.remediateSubstitutes;
    if (kind === "diet") return g.remediateDiet;
    return g.remediateLimits;
  };

  const remediations = entries
    .map((e) => (e.remediation ? { ruleId: e.rule_id, ...e.remediation } : null))
    .filter((x): x is NonNullable<typeof x> => x !== null);

  return (
    <div className={`space-y-2 ${className}`} data-testid="governance-funnel">
      <div className={`grid gap-2 ${compact ? "md:grid-cols-3" : "sm:grid-cols-3"}`}>
        {GOVERNANCE_GATES.map((gateId) => {
          const meta = gateMeta[gateId];
          const Icon = meta.Icon;
          const gateEntries = entries.filter((e) => e.gate === gateId);
          return (
            <div
              key={gateId}
              data-testid={`governance-gate-${gateId}`}
              className={`rounded border px-3 py-2 text-[12px] ${gateEntries.length > 0 ? meta.border : "border-line bg-white opacity-75"}`}
            >
              <div className="flex items-center gap-1.5 font-bold text-ink">
                <Icon className={`h-3.5 w-3.5 shrink-0 ${meta.iconCls}`} strokeWidth={2} aria-hidden="true" />
                <span className="truncate">{meta.title}</span>
              </div>
              <p className="mt-0.5 text-[11px] text-ink-secondary">{meta.question}</p>
              <div className="mt-1.5 flex flex-wrap gap-1">
                {gateEntries.length > 0 ? (
                  gateEntries.map((entry, idx) => (
                    <span
                      key={`${entry.rule_id}-${idx}`}
                      className="inline-flex items-center gap-1 rounded bg-white/85 px-1.5 py-0.5 text-[11px]"
                    >
                      <span className="font-mono-code font-bold text-navy">{entry.rule_id}</span>
                      <Badge tone={!entry.overridable ? "danger" : entry.outcome === "AUTO" ? "success" : "warning"}>
                        {!entry.overridable ? g.hardLock : entry.outcome === "AUTO" ? g.passed : g.approvable}
                      </Badge>
                    </span>
                  ))
                ) : (
                  <span className="font-mono-code text-[11px] text-success">{g.passed}</span>
                )}
              </div>
            </div>
          );
        })}
      </div>

      {remediations.length > 0 && (
        <div className="flex flex-wrap items-center gap-1.5 text-[12px]" data-testid="governance-remediations">
          <span className="font-semibold text-ink-secondary">{g.remediationPrefix}</span>
          {remediations.map((rem, i) => (
            <Link
              key={`${rem.ruleId}-${i}`}
              to={rem.to}
              data-testid={`remediate-${rem.ruleId}`}
              className="inline-flex items-center gap-1 rounded-full border border-navy bg-white px-2.5 py-0.5 text-[12px] font-bold text-navy hover:bg-primary-soft"
            >
              <span>{remediationLabel(rem.kind, rem.customLabel)}</span>
              <ArrowRight className="h-3 w-3" aria-hidden="true" />
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}
