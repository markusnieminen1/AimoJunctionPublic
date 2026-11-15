import { ShieldAlert } from "lucide-react";
import { useAgentAnomalies, useReviewAgentAnomaly } from "../../api/hooks";
import { useAuth } from "../../auth/AuthContext";
import { useStrings } from "../../i18n";
import { Badge } from "../Badge";
import { Button } from "../Button";
import { CopyChip } from "../CopyChip";
import { ErrorPanel } from "../ErrorPanel";
import { Stripe } from "../Stripe";
import { toast } from "../Toast";

/** Immediate Human Review banner rendered on /agents and /audit whenever purchasing-characteristics auto-quarantine triggers. */
export function AgentAnomaliesBanner({ className = "" }: { className?: string }) {
  const { isUser } = useAuth();
  const strings = useStrings();
  const t = strings.agents;

  const q = useAgentAnomalies({ enabled: isUser, silent: true });
  const review = useReviewAgentAnomaly({ silent: true });

  if (!isUser) return null;
  const items = q.data?.items ?? [];
  if (items.length === 0) return null;

  const handleReview = async (agentId: string, action: "restore" | "revoke") => {
    try {
      await review.mutateAsync({ id: agentId, body: { action } });
      toast.success(action === "restore" ? t.anomaliesRestoredToast : t.anomaliesRevokedToast);
    } catch {
      // Inline ErrorPanel renders exact API error code + request_id.
    }
  };

  return (
    <section className={`space-y-3 ${className}`} data-testid="agent-anomalies-banner">
      {review.isError && <ErrorPanel error={review.error} />}
      {items.map((alert) => {
        const chars = Object.entries(alert.purchasing_characteristics ?? {});
        return (
          <Stripe
            key={alert.agent_id}
            kind="error"
            accent
            role="alert"
            icon={<ShieldAlert className="h-5 w-5 text-red-strong" strokeWidth={1.75} aria-hidden="true" />}
            title={
              <span className="inline-flex flex-wrap items-center gap-2">
                <span>{t.anomaliesTitle}</span>
                <Badge tone="danger">{t.quarantinedBadge}</Badge>
                {alert.rule_code && (
                  <span className="font-mono-code rounded bg-white/80 px-1.5 py-0.5 text-[11px] font-medium text-danger">
                    {alert.rule_code}
                  </span>
                )}
              </span>
            }
            actions={
              <div className="flex flex-wrap gap-2">
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  loading={review.isPending}
                  data-testid={`anomaly-restore-${alert.agent_id}`}
                  onClick={() => void handleReview(alert.agent_id, "restore")}
                >
                  {t.anomaliesRestore}
                </Button>
                <Button
                  type="button"
                  size="sm"
                  variant="danger-outline"
                  loading={review.isPending}
                  data-testid={`anomaly-revoke-${alert.agent_id}`}
                  onClick={() => void handleReview(alert.agent_id, "revoke")}
                >
                  {t.anomaliesRevoke}
                </Button>
              </div>
            }
          >
            <p className="text-[13px] text-ink">
              <strong>{alert.agent_name}</strong>{" "}
              <span className="font-mono-code text-ink-secondary">({alert.agent_id})</span> — {alert.trigger_reason}
            </p>
            {chars.length > 0 && (
              <div className="mt-1.5 flex flex-wrap items-center gap-1.5 text-[12px]">
                <span className="font-semibold text-ink-secondary">{t.anomaliesEvidence}:</span>
                {chars.map(([k, v]) => (
                  <span key={k} className="font-mono-code rounded bg-white/80 px-1.5 py-0.5 text-[11px] text-ink">
                    {k}={String(v)}
                  </span>
                ))}
              </div>
            )}
            {alert.recent_request_ids && alert.recent_request_ids.length > 0 && (
              <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
                {alert.recent_request_ids.slice(0, 3).map((rid) => (
                  <CopyChip key={rid} value={rid} />
                ))}
              </div>
            )}
          </Stripe>
        );
      })}
    </section>
  );
}
