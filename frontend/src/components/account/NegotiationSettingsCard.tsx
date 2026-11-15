import { useState } from "react";
import { useAuth } from "../../auth/AuthContext";
import {
  useCompanyNegotiations,
  useNegotiationSettings,
  useUpdateCompanyNegotiation,
} from "../../api/hooks";
import type { CompanyNegotiationAdminOut } from "../../api/types";
import { useStrings } from "../../i18n";
import { Card, CardHeader } from "../Card";
import { ErrorPanel } from "../ErrorPanel";
import { Toggle } from "../FormField";
import { Skeleton } from "../Skeleton";
import { Stripe } from "../Stripe";
import { toast } from "../Toast";

const SANDBOX_ORG_RE = /^(QA\b|Demo Kitchen\b)/i;

/**
 * Per-company toggle for AI multi-turn price negotiation on larger quantities
 * (`GET/PUT /api/admin/company-negotiations`). Rendered ONLY in `/admin`;
 * AI agents see a read-only view (`GET /api/admin/company-negotiations` is human-only -> 403).
 */
export function NegotiationSettingsCard({ className = "" }: { className?: string }) {
  const { isUser, company, principal } = useAuth();
  const strings = useStrings();
  const t = strings.account;
  const activeCompanyId = company?.id ?? principal?.company_id ?? "";

  const adminList = useCompanyNegotiations({ enabled: isUser, silent: true });
  const fallbackSelf = useNegotiationSettings({ enabled: !isUser, silent: true });
  const updateMut = useUpdateCompanyNegotiation({ silent: true });
  const [savingCompanyId, setSavingCompanyId] = useState<string | null>(null);
  const [showSandbox, setShowSandbox] = useState(false);

  const items: CompanyNegotiationAdminOut[] = isUser
    ? (adminList.data?.items ?? [])
    : fallbackSelf.data
      ? [
          {
            company_id: fallbackSelf.data.company_id,
            company_name: company?.name ?? fallbackSelf.data.company_id,
            allow_price_negotiation: fallbackSelf.data.allow_price_negotiation,
            max_auto_discount_pct: fallbackSelf.data.max_discount_override_pct ?? 8,
            updated_at: fallbackSelf.data.updated_at,
          },
        ]
      : [];

  const activeEntry = items.find((c) => c.company_id === activeCompanyId) ?? items[0];
  const primaryItems = items.filter(
    (c) => c.company_id === activeCompanyId || !SANDBOX_ORG_RE.test(c.company_name.trim()),
  );
  const sandboxItems = items.filter(
    (c) => c.company_id !== activeCompanyId && SANDBOX_ORG_RE.test(c.company_name.trim()),
  );
  const visibleItems =
    primaryItems.length === 0
      ? items
      : showSandbox
        ? [...primaryItems, ...sandboxItems]
        : primaryItems;
  const loading = isUser ? adminList.isLoading : fallbackSelf.isLoading;
  const loadError = isUser ? adminList.error : fallbackSelf.error;
  const isError = isUser ? adminList.isError : fallbackSelf.isError;

  return (
    <Card className={className} data-testid="negotiation-settings-card">
      <CardHeader
        title={t.negotiationTitle}
        description={t.negotiationSubtitle}
        actions={
          activeEntry ? (
            <span
              data-testid="negotiation-status-pill"
              className={`rounded-none px-2 py-0.5 text-[11px] font-bold uppercase tracking-[0.04em] ${
                activeEntry.allow_price_negotiation ? "bg-lime text-navy" : "bg-muted text-ink-secondary"
              }`}
            >
              {activeEntry.allow_price_negotiation ? t.negotiationEnabledBadge : t.negotiationDisabledBadge}
            </span>
          ) : null
        }
      />
      <div className="mt-4 space-y-3">
        {isError ? (
          <ErrorPanel
            error={loadError}
            onRetry={() => void (isUser ? adminList.refetch() : fallbackSelf.refetch())}
          />
        ) : loading ? (
          <Skeleton className="h-16 w-full" />
        ) : (
          <>
            <div className="divide-y divide-line border border-line bg-surface">
              {visibleItems.map((c) => {
                const isActive = c.company_id === activeCompanyId || (activeEntry?.company_id === c.company_id && !activeCompanyId);
                const rowSaving = updateMut.isPending && savingCompanyId === c.company_id;
                return (
                  <div
                    key={c.company_id}
                    className="min-w-0 px-3 py-2.5"
                    data-testid={`company-negotiation-row-${c.company_id}`}
                  >
                    <Toggle
                      id={isActive ? "allow-price-negotiation" : `allow-price-negotiation-${c.company_id}`}
                      testId={`company-negotiation-toggle-${c.company_id}`}
                      checked={c.allow_price_negotiation}
                      disabled={!isUser || updateMut.isPending}
                      label={
                        <span className="inline-flex flex-wrap items-center gap-1.5">
                          <span>{c.company_name}</span>
                          {isActive && (
                            <span className="rounded-none bg-primary-soft px-1.5 py-0.5 text-[10.5px] font-bold uppercase tracking-[0.04em] text-navy">
                              {t.negotiationCurrentCompany}
                            </span>
                          )}
                        </span>
                      }
                      description={
                        <span className="inline-flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[12px] text-ink-secondary">
                          <span>
                            {c.allow_price_negotiation ? t.negotiationEnabledBadge : t.negotiationDisabledBadge}
                          </span>
                          <span>·</span>
                          <span>{t.negotiationDiscountCap(c.max_auto_discount_pct)}</span>
                          {rowSaving && <span>· {t.negotiationSaving}</span>}
                        </span>
                      }
                      onChange={(next) => {
                        setSavingCompanyId(c.company_id);
                        updateMut.mutate(
                          { companyId: c.company_id, body: { allow_price_negotiation: next } },
                          {
                            onSuccess: () => toast.success(t.negotiationSaved),
                            onSettled: () => setSavingCompanyId(null),
                          },
                        );
                      }}
                    />
                  </div>
                );
              })}
            </div>
            {primaryItems.length > 0 && sandboxItems.length > 0 && (
              <div>
                <button
                  type="button"
                  data-testid="toggle-sandbox-orgs"
                  aria-expanded={showSandbox}
                  onClick={() => setShowSandbox((v) => !v)}
                  className="rounded-none border border-line bg-surface px-3 py-1.5 text-[12px] font-bold text-navy hover:bg-primary-soft"
                >
                  {showSandbox
                    ? t.negotiationHideSandboxOrgs
                    : t.negotiationShowSandboxOrgs(sandboxItems.length)}
                </button>
              </div>
            )}
            {!isUser && (
              <Stripe kind="neutral" compact data-testid="negotiation-readonly-note">
                {t.negotiationReadOnly}
              </Stripe>
            )}
            {updateMut.isError && <ErrorPanel error={updateMut.error} />}
          </>
        )}
      </div>
    </Card>
  );
}
