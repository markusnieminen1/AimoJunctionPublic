import { useEffect, useState } from "react";
import { useAssistantSettings, useUpdateAssistantSettings } from "../../api/hooks";
import { useAuth } from "../../auth/AuthContext";
import { useStrings } from "../../i18n";
import { Button } from "../Button";
import { Card, CardHeader } from "../Card";
import { ErrorPanel } from "../ErrorPanel";
import { Skeleton } from "../Skeleton";
import { Stripe } from "../Stripe";
import { toast } from "../Toast";

/**
 * Configures the company's Order Assurance threshold (50–100%, default 80%).
 * Controls whether the AI assistant auto-places low-risk routine orders or asks confirmation.
 * Human buyers can adjust via slider or 1-tap presets; AI agents see read-only state (403 server-side).
 */
export function OrderAssuranceSettingsCard({ className = "" }: { className?: string }) {
  const { isUser } = useAuth();
  const strings = useStrings();
  const t = strings.account;
  const settings = useAssistantSettings({ silent: true });
  const updateMut = useUpdateAssistantSettings({ silent: true });
  const saved = settings.data?.order_assurance_threshold ?? 80;
  const [draft, setDraft] = useState(saved);

  useEffect(() => {
    setDraft(saved);
  }, [saved]);

  const saveThreshold = (next: number) => {
    const clamped = Math.max(50, Math.min(100, Math.round(next)));
    setDraft(clamped);
    updateMut.mutate(
      { order_assurance_threshold: clamped },
      {
        onSuccess: () => toast.success(t.assuranceSaved),
      },
    );
  };

  return (
    <Card className={className} data-testid="assurance-settings-card">
      <CardHeader
        title={t.assuranceTitle}
        description={t.assuranceSubtitle}
        actions={
          settings.data ? (
            <span
              data-testid="assurance-threshold-pill"
              className="rounded-none bg-lime px-2.5 py-0.5 text-[12px] font-bold tabular-nums text-navy"
            >
              {draft}%
            </span>
          ) : null
        }
      />
      <div className="mt-4 space-y-3">
        {settings.isError ? (
          <ErrorPanel error={settings.error} onRetry={() => void settings.refetch()} />
        ) : !settings.data ? (
          <Skeleton className="h-14 w-full" />
        ) : (
          <>
            <div className="flex flex-wrap items-center gap-3">
              <label htmlFor="order-assurance-slider" className="text-[13px] font-bold text-ink">
                {t.assuranceLabel}
              </label>
              <input
                id="order-assurance-slider"
                type="range"
                min={settings.data.min_threshold ?? 50}
                max={settings.data.max_threshold ?? 100}
                step={1}
                value={draft}
                disabled={!isUser || updateMut.isPending}
                onChange={(e) => setDraft(Number(e.target.value))}
                className="h-2 min-w-[160px] flex-1 accent-navy disabled:opacity-50"
                data-testid="assurance-slider"
              />
              {isUser && draft !== saved && (
                <Button
                  type="button"
                  size="sm"
                  variant="primary"
                  disabled={updateMut.isPending}
                  onClick={() => saveThreshold(draft)}
                  data-testid="assurance-save-btn"
                >
                  {updateMut.isPending ? t.assuranceSaving : t.assuranceSave}
                </Button>
              )}
            </div>

            <div className="flex flex-wrap gap-2" role="group" aria-label={t.assuranceLabel}>
              {t.assurancePresets.map((p) => {
                const active = saved === p.value && draft === p.value;
                return (
                  <button
                    key={p.value}
                    type="button"
                    disabled={!isUser || updateMut.isPending}
                    aria-pressed={active}
                    onClick={() => saveThreshold(p.value)}
                    data-testid={`assurance-preset-${p.value}`}
                    className={`rounded-none border px-2.5 py-1 text-[12px] font-bold transition-colors disabled:opacity-50 ${
                      active
                        ? "border-navy bg-navy text-white"
                        : "border-line bg-surface text-navy hover:bg-primary-soft"
                    }`}
                  >
                    {p.label}
                  </button>
                );
              })}
            </div>

            {!isUser && (
              <Stripe kind="neutral" compact data-testid="assurance-readonly-note">
                {t.assuranceReadOnly}
              </Stripe>
            )}
            {updateMut.isError && <ErrorPanel error={updateMut.error} />}
          </>
        )}
      </div>
    </Card>
  );
}
