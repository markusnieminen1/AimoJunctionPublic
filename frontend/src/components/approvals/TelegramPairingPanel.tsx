import { useEffect, useRef } from "react";
import { Building2, ExternalLink, RefreshCw, Sparkles } from "lucide-react";
import { isEndpointMissing, toApiError } from "../../api/client";
import { useCreateTelegramLinkToken, useUpdateTelegramBranch } from "../../api/hooks";
import type { TelegramOptInOut } from "../../api/types";
import { useStrings } from "../../i18n";
import { Badge } from "../Badge";
import { Button } from "../Button";
import { Skeleton } from "../Skeleton";
import { toast } from "../Toast";

export function formatLinkedTelegram(username: string | null, chatId: string | null): string {
  if (username) return username.startsWith("@") ? username : `@${username}`;
  if (chatId) return chatId;
  return "";
}

export function svgToDataUrl(svg: string): string {
  return `data:image/svg+xml;utf8,${encodeURIComponent(svg.trim())}`;
}

export function TelegramBranchControls({
  data,
  compact = false,
}: {
  data: TelegramOptInOut;
  compact?: boolean;
}) {
  const t = useStrings().approvals;
  const branchMut = useUpdateTelegramBranch({ silent: true });
  const branches = data.branches ?? [];

  if (branches.length === 0) return null;

  const activeName =
    data.active_branch_name ??
    branches.find((b) => b.id === data.active_branch_id)?.short_name ??
    null;

  const handleChange = async (branchId: string) => {
    if (!branchId || branchId === data.active_branch_id) return;
    try {
      const updated = await branchMut.mutateAsync({ branch_id: branchId });
      const chosen =
        updated.active_branch_name ??
        branches.find((b) => b.id === branchId)?.short_name ??
        branchId;
      toast.success(t.telegramBranchUpdatedToast(chosen));
    } catch {
      // Handled inline / silent query meta
    }
  };

  return (
    <div className="flex min-w-0 flex-wrap items-center gap-2">
      <Badge
        data-testid="telegram-branch-badge"
        tone={activeName ? "info" : "warning"}
        className="max-w-full truncate"
      >
        <Building2 className="mr-1 h-3.5 w-3.5 shrink-0" strokeWidth={1.75} aria-hidden="true" />
        <span className="truncate">{activeName ?? t.telegramBranchUnset}</span>
      </Badge>

      <label className="inline-flex min-w-0 items-center gap-1.5 text-[12px] font-medium text-ink-secondary">
        {!compact && <span className="sr-only">{t.telegramBranchLabel}</span>}
        <select
          aria-label={t.telegramBranchLabel}
          data-testid="telegram-branch-select"
          value={data.active_branch_id ?? ""}
          disabled={branchMut.isPending}
          onChange={(e) => void handleChange(e.target.value)}
          className="h-8 max-w-full rounded border border-line-strong bg-white px-2.5 text-[13px] font-medium text-ink focus:outline-none focus:ring-2 focus:ring-navy disabled:opacity-60"
        >
          {!data.active_branch_id && <option value="">{t.telegramBranchSelect}</option>}
          {branches.map((b) => (
            <option key={b.id} value={b.id}>
              {b.short_name}
            </option>
          ))}
        </select>
      </label>
    </div>
  );
}

export function TelegramQrPairingBlock({
  active,
  enabled,
  compact = false,
}: {
  active: boolean;
  enabled: boolean;
  compact?: boolean;
}) {
  const t = useStrings().approvals;
  const linkMut = useCreateTelegramLinkToken({ silent: true });
  const { data: linkData, isPending: linkPending, mutate: mintLinkToken, reset: resetLinkToken } = linkMut;
  const requestedRef = useRef(false);

  useEffect(() => {
    if (active) {
      requestedRef.current = false;
      if (linkData) resetLinkToken();
      return;
    }
    if (!enabled) {
      requestedRef.current = false;
      return;
    }
    if (!requestedRef.current && !linkData && !linkPending) {
      requestedRef.current = true;
      mintLinkToken();
    }
  }, [active, enabled, linkData, linkPending, mintLinkToken, resetLinkToken]);

  if (active || !enabled) return null;

  if (linkMut.isError) {
    const err = toApiError(linkMut.error);
    if (isEndpointMissing(linkMut.error) || err.status === 404 || err.status === 403) {
      return null;
    }
  }

  return (
    <div
      data-testid="telegram-qr-pairing"
      className={`rounded border border-navy/20 bg-primary-soft ${compact ? "p-3" : "p-4"}`}
    >
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
        {linkMut.isPending && !linkMut.data ? (
          <Skeleton className="h-36 w-36 shrink-0" />
        ) : linkMut.data?.qr_svg ? (
          <img
            src={svgToDataUrl(linkMut.data.qr_svg)}
            alt={t.telegramQrAlt}
            data-testid="telegram-qr-img"
            className="h-36 w-36 shrink-0 rounded border border-line bg-white p-1.5"
            style={{ imageRendering: "pixelated" }}
          />
        ) : null}

        <div className="min-w-0 flex-1 space-y-2">
          <div className="flex items-center gap-1.5 text-[14px] font-bold text-navy">
            <Sparkles className="h-4 w-4 shrink-0" strokeWidth={1.75} aria-hidden="true" />
            <span>{t.telegramQrTitle}</span>
          </div>
          <p className="text-[13px] leading-snug text-ink-secondary">{t.telegramQrHint}</p>

          <div className="flex flex-wrap items-center gap-2 pt-0.5">
            {linkMut.data?.deep_link_url && (
              <a
                href={linkMut.data.deep_link_url}
                target="_blank"
                rel="noreferrer"
                data-testid="telegram-deeplink-btn"
                className="inline-flex min-h-[38px] items-center justify-center gap-1.5 rounded bg-navy px-3.5 py-2 text-[13px] font-bold text-white transition-opacity hover:opacity-95"
              >
                {t.telegramOpenApp}
                <ExternalLink className="h-3.5 w-3.5" aria-hidden="true" />
              </a>
            )}
            <Button
              type="button"
              size="sm"
              variant="outline"
              loading={linkMut.isPending}
              icon={<RefreshCw className="h-3.5 w-3.5" strokeWidth={1.75} />}
              onClick={() => linkMut.mutate()}
              data-testid="telegram-refresh-qr-btn"
            >
              {t.telegramRefreshQr}
            </Button>
          </div>

          <p className="flex items-center gap-1.5 text-[12px] text-ink-muted" data-testid="telegram-polling-hint">
            <span className="h-2 w-2 shrink-0 rounded-full bg-lime-strong" aria-hidden="true" />
            <span>{t.telegramPollingHint}</span>
          </p>
        </div>
      </div>
    </div>
  );
}
