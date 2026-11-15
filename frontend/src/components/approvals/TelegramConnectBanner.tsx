import { useEffect, useState } from "react";
import { ChevronDown, ChevronUp, MessageCircle } from "lucide-react";
import { isEndpointMissing, toApiError } from "../../api/client";
import { useTelegramOptIn } from "../../api/hooks";
import { useAuth } from "../../auth/AuthContext";
import { useStrings } from "../../i18n";
import { Badge } from "../Badge";
import { Button } from "../Button";
import {
  formatLinkedTelegram,
  TelegramBranchControls,
  TelegramQrPairingBlock,
} from "./TelegramPairingPanel";

export function TelegramConnectBanner({ className = "" }: { className?: string }) {
  const { isUser } = useAuth();
  const t = useStrings().approvals;
  const [open, setOpen] = useState(false);
  const [shouldPoll, setShouldPoll] = useState(false);

  const optIn = useTelegramOptIn({
    enabled: isUser,
    silent: true,
    refetchInterval: shouldPoll ? 5000 : false,
  });

  useEffect(() => {
    setShouldPoll(Boolean(isUser && open && optIn.data && !optIn.data.enabled && !optIn.isError));
  }, [isUser, open, optIn.data, optIn.isError]);

  if (!isUser) return null;

  const queryErr = optIn.isError ? toApiError(optIn.error) : null;
  if (
    optIn.isError &&
    (isEndpointMissing(optIn.error) || queryErr?.status === 404 || queryErr?.status === 403 || queryErr?.code === "FORBIDDEN")
  ) {
    return null;
  }

  if (!optIn.data) return null;

  const isActive = Boolean(optIn.data.enabled);
  const linkedDisplay = formatLinkedTelegram(optIn.data.telegram_username, optIn.data.telegram_chat_id);

  return (
    <div
      data-testid="telegram-connect-banner"
      className={`rounded border border-navy/20 bg-white/95 px-3.5 py-2.5 text-ink shadow-portal-sm ${className}`}
    >
      <div className="flex flex-wrap items-center justify-between gap-2.5">
        <div className="flex min-w-0 flex-wrap items-center gap-2">
          <span className="inline-flex items-center gap-1.5 text-[13px] font-bold text-navy">
            <MessageCircle className="h-4 w-4 shrink-0 text-navy" strokeWidth={1.75} aria-hidden="true" />
            <span>{t.telegramBannerTitle}</span>
          </span>
          {isActive ? (
            <Badge data-testid="telegram-optin-status" tone="success">
              {t.telegramActive}
              {linkedDisplay ? ` · ${linkedDisplay}` : ""}
            </Badge>
          ) : (
            <span className="text-[12px] text-ink-secondary">{t.telegramBannerBody}</span>
          )}
        </div>

        <div className="flex min-w-0 flex-wrap items-center gap-2">
          <TelegramBranchControls data={optIn.data} compact />
          {!isActive && (
            <Button
              type="button"
              size="sm"
              variant="outline"
              onClick={() => setOpen((v) => !v)}
              icon={open ? <ChevronUp className="h-3.5 w-3.5" /> : <ChevronDown className="h-3.5 w-3.5" />}
              data-testid="telegram-banner-connect-btn"
            >
              {open ? t.telegramBannerHide : t.telegramBannerCta}
            </Button>
          )}
        </div>
      </div>

      {!isActive && open && (
        <div className="mt-3">
          <TelegramQrPairingBlock active={isActive} enabled={open} compact />
        </div>
      )}
    </div>
  );
}
