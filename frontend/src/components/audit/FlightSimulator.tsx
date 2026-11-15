// P1 Dev-Only Agent Chaos & Policy Flight Simulator panel (/audit?view=simulator).
import { useState } from "react";
import { Link } from "react-router-dom";
import { Activity, ArrowRight, Play, ShieldAlert, ShieldCheck } from "lucide-react";
import type { PolicyRule, SimulatorRunResponse, SimulatorScenario, SimulatorScenarioId } from "../../api/types";
import { useRunSimulatorScenario } from "../../api/hooks";
import { useStrings } from "../../i18n";
import { Badge, type BadgeTone } from "../Badge";
import { Button } from "../Button";
import { Card } from "../Card";
import { CopyChip } from "../CopyChip";
import { ErrorPanel } from "../ErrorPanel";
import { Stripe } from "../Stripe";

const RULE_TONE: Record<PolicyRule["outcome"], BadgeTone> = {
  AUTO: "success",
  APPROVAL_REQUIRED: "warning",
  BLOCKED: "danger",
};

export function FlightSimulator({ scenarios }: { scenarios: SimulatorScenario[] }) {
  const t = useStrings().audit;
  const run = useRunSimulatorScenario();
  const [activeId, setActiveId] = useState<SimulatorScenarioId | null>(null);
  const [results, setResults] = useState<Partial<Record<SimulatorScenarioId, SimulatorRunResponse>>>({});

  async function handleRun(id: SimulatorScenarioId) {
    setActiveId(id);
    try {
      const res = await run.mutateAsync({ scenario_id: id });
      setResults((prev) => ({ ...prev, [id]: res }));
    } finally {
      setActiveId(null);
    }
  }

  return (
    <div className="space-y-4" data-testid="flight-simulator">
      <p className="text-sm text-ink-secondary">{t.simulatorLead}</p>

      {run.isError && <ErrorPanel error={run.error} title="Simulation request failed" />}

      <ul className="space-y-3">
        {scenarios.map((sc) => {
          const result = results[sc.id];
          const busy = run.isPending && activeId === sc.id;
          return (
            <li key={sc.id} data-testid={`simulator-card-${sc.id}`}>
              <Card>
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="min-w-0 flex-1 space-y-1.5">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="inline-flex items-center gap-1.5 text-[15px] font-bold text-navy">
                        <Activity className="h-4 w-4 shrink-0" strokeWidth={1.75} aria-hidden />
                        {sc.title}
                      </span>
                      <Badge tone="neutral">{sc.expected_outcome}</Badge>
                      {result && (
                        <Badge
                          tone={result.passed ? "success" : "danger"}
                          icon={
                            result.passed ? (
                              <ShieldCheck className="h-3.5 w-3.5" strokeWidth={2} />
                            ) : (
                              <ShieldAlert className="h-3.5 w-3.5" strokeWidth={2} />
                            )
                          }
                          data-testid={`simulator-result-badge-${sc.id}`}
                        >
                          {result.passed ? t.simulatorPassed : t.simulatorFailed}
                        </Badge>
                      )}
                    </div>
                    <p className="text-sm text-ink-secondary">{sc.summary}</p>
                    <div className="flex flex-wrap items-center gap-1.5 pt-0.5 text-xs">
                      <span className="font-semibold text-ink-muted">{t.simulatorExpected}:</span>
                      {sc.expected_rules.map((r) => (
                        <span
                          key={r}
                          className="font-mono-code rounded-sm border border-line bg-primary-soft px-1.5 py-0.5 text-[11px] font-semibold text-navy"
                        >
                          {r}
                        </span>
                      ))}
                      <span className="ml-1 font-semibold text-ink-muted">{t.simulatorTargets}:</span>
                      {sc.target_product_ids.map((sku) => (
                        <span key={sku} className="font-mono-code rounded-sm bg-canvas px-1.5 py-0.5 text-[11px] text-ink">
                          {sku}
                        </span>
                      ))}
                    </div>
                  </div>

                  <Button
                    type="button"
                    variant="primary"
                    size="sm"
                    disabled={run.isPending}
                    data-testid={`simulator-run-${sc.id}`}
                    onClick={() => void handleRun(sc.id)}
                  >
                    <Play className="h-3.5 w-3.5" aria-hidden />
                    {busy ? t.simulatorRunning : t.simulatorRun}
                  </Button>
                </div>

                {result && (
                  <div
                    className="mt-4 space-y-3 border-t border-border pt-3 text-sm"
                    data-testid={`simulator-output-${sc.id}`}
                  >
                    <div className="flex flex-wrap items-center gap-2 text-xs">
                      <Badge tone={result.http_status >= 400 ? "warning" : "info"}>
                        HTTP {result.http_status}
                        {result.error_code ? ` · ${result.error_code}` : ""}
                      </Badge>

                      {(result.order_status_before_reconcile || result.order_status_after) && (
                        <span
                          className="font-mono-code inline-flex items-center gap-1 rounded-sm border border-line bg-canvas px-2 py-0.5 text-[11.5px] font-semibold text-ink"
                          data-testid={`simulator-transition-${sc.id}`}
                        >
                          {result.order_status_before_reconcile && (
                            <>
                              <span>{result.order_status_before_reconcile}</span>
                              <ArrowRight className="h-3 w-3 text-navy" aria-hidden />
                            </>
                          )}
                          <span>{result.order_status_after}</span>
                        </span>
                      )}

                      {result.gateway_reference && (
                        <span className="text-ink-muted">
                          {t.simulatorGatewayRef}:{" "}
                          <span className="font-mono-code font-medium text-ink">{result.gateway_reference}</span>
                        </span>
                      )}

                      <Badge
                        tone={result.audit_chain.valid ? "success" : "danger"}
                        data-testid={`simulator-chain-${sc.id}`}
                      >
                        {t.simulatorAuditChain}: {t.chainVerified(result.audit_chain.checked_events)}
                      </Badge>

                      <CopyChip value={result.request_id} label={t.colRequest} />

                      {result.order_id && (
                        <Link
                          to={`/orders/${result.order_id}`}
                          className="font-semibold text-navy hover:underline"
                          data-testid={`simulator-order-link-${sc.id}`}
                        >
                          {t.simulatorOpenOrder} →
                        </Link>
                      )}
                    </div>

                    {result.policy && result.policy.rules.length > 0 && (
                      <div className="space-y-1.5">
                        <div className="text-xs font-semibold uppercase tracking-wide text-ink-muted">
                          {t.simulatorRulesFired} · {result.policy.outcome}
                        </div>
                        <div className="space-y-1.5">
                          {result.policy.rules.map((rule, idx) => (
                            <Stripe
                              key={`${rule.rule_id}-${idx}`}
                              kind={rule.outcome === "BLOCKED" ? "error" : rule.outcome === "APPROVAL_REQUIRED" ? "warning" : "success"}
                              title={
                                <span className="inline-flex items-center gap-2">
                                  <Badge tone={RULE_TONE[rule.outcome]}>{rule.rule_id}</Badge>
                                  <span className="font-mono-code text-xs">{rule.outcome}</span>
                                </span>
                              }
                            >
                              <p>{rule.message}</p>
                            </Stripe>
                          ))}
                        </div>
                      </div>
                    )}

                    {result.substitutes_preview && result.substitutes_preview.length > 0 && (
                      <div className="space-y-1.5" data-testid={`simulator-subs-${sc.id}`}>
                        <div className="text-xs font-semibold uppercase tracking-wide text-ink-muted">
                          {t.simulatorSubstitutes}
                        </div>
                        <ul className="grid gap-2 sm:grid-cols-2">
                          {result.substitutes_preview.map((sub) => (
                            <li
                              key={sub.product.id}
                              className="rounded border border-line bg-canvas px-3 py-2 text-xs"
                            >
                              <div className="flex items-center justify-between gap-2 font-semibold text-ink">
                                <span>
                                  <span className="font-mono-code text-navy">{sub.product.id}</span> · {sub.product.name}
                                </span>
                                <Badge tone="success">{sub.line.delivery_status}</Badge>
                              </div>
                              <p className="mt-1 text-ink-muted">{sub.reason}</p>
                            </li>
                          ))}
                        </ul>
                      </div>
                    )}
                  </div>
                )}
              </Card>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
