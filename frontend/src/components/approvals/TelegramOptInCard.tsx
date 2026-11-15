import { useEffect, useState, type FormEvent } from "react";
import { ExternalLink, MessageCircle, ShieldCheck } from "lucide-react";
import { isEndpointMissing, toApiError } from "../../api/client";
import { useDeleteTelegramOptIn, useTelegramOptIn, useUpdateTelegramOptIn } from "../../api/hooks";
import { useAuth } from "../../auth/AuthContext";
import { useStrings } from "../../i18n";
import { Badge } from "../Badge";
import { Button } from "../Button";
import { Card, CardHeader } from "../Card";
import { ErrorPanel } from "../ErrorPanel";
import { Field, TextInput } from "../FormField";
import { Skeleton } from "../Skeleton";
import { Stripe } from "../Stripe";
import { toast } from "../Toast";
import {
  formatLinkedTelegram,
  TelegramBranchControls,
  TelegramQrPairingBlock,
} from "./TelegramPairingPanel";

const DEFAULT_BOT_USERNAME = import.meta.env.VITE_TELEGRAM_BOT_USERNAME || "your_aimo_bot";
const NUMERIC_ID_RE = /^\d{5,16}$/;
const USERNAME_RE = /^@?[A-Za-z0-9_]{3,32}$/;

export function TelegramOptInCard({ className = "" }: { className?: string }) {
  const { isUser } = useAuth();
  const strings = useStrings();
  const t = strings.approvals;

  const [shouldPoll, setShouldPoll] = useState(false);
  const optIn = useTelegramOptIn({
    enabled: isUser,
    silent: true,
    refetchInterval: shouldPoll ? 5000 : false,
  });
  const updateMut = useUpdateTelegramOptIn({ silent: true });
  const deleteMut = useDeleteTelegramOptIn({ silent: true });

  const [telegramId, setTelegramId] = useState("");
  const [consentConfirmed, setConsentConfirmed] = useState(false);
  const [validationError, setValidationError] = useState<string | null>(null);

  useEffect(() => {
    if (!optIn.data) {
      setShouldPoll(false);
      return;
    }
    setTelegramId(formatLinkedTelegram(optIn.data.telegram_username, optIn.data.telegram_chat_id));
    setConsentConfirmed(Boolean(optIn.data.enabled || optIn.data.consented_at));
    setValidationError(null);
    setShouldPoll(Boolean(isUser && !optIn.data.enabled && !optIn.isError));
  }, [isUser, optIn.data, optIn.isError]);

  // Telegram 1-tap approval opt-in is strictly human-only; agents cannot configure it.
  if (!isUser) return null;

  const queryErr = optIn.isError ? toApiError(optIn.error) : null;
  if (queryErr?.status === 403 || queryErr?.code === "FORBIDDEN") return null;

  const endpointMissing = Boolean(
    (optIn.isError && (isEndpointMissing(optIn.error) || queryErr?.status === 404)) ||
      (updateMut.isError && (isEndpointMissing(updateMut.error) || toApiError(updateMut.error).status === 404)) ||
      (deleteMut.isError && (isEndpointMissing(deleteMut.error) || toApiError(deleteMut.error).status === 404)),
  );

  const botUsername = optIn.data?.bot_username || DEFAULT_BOT_USERNAME;
  const botHref = `https://t.me/${botUsername.replace(/^@/, "")}`;
  const isActive = Boolean(optIn.data?.enabled);
  const linkedDisplay = optIn.data ? formatLinkedTelegram(optIn.data.telegram_username, optIn.data.telegram_chat_id) : "";

  const handleSave = async (e: FormEvent) => {
    e.preventDefault();
    const raw = telegramId.trim();

    if (raw.startsWith("-")) {
      setValidationError(t.telegramErrorNegativeGroup);
      return;
    }
    const valid = /^\d+$/.test(raw) ? NUMERIC_ID_RE.test(raw) : USERNAME_RE.test(raw);
    if (!raw || !valid) {
      setValidationError(t.telegramErrorInvalidId);
      return;
    }
    if (!consentConfirmed) {
      setValidationError(t.telegramErrorConsent);
      return;
    }

    setValidationError(null);
    updateMut.reset();
    deleteMut.reset();
    try {
      await updateMut.mutateAsync({
        telegram_id: raw,
        enabled: true,
        consent_confirmed: true,
      });
      toast.success(t.telegramSavedToast);
    } catch {
      // Inline ErrorPanel renders structured API error details.
    }
  };

  const handleRevoke = async () => {
    setValidationError(null);
    updateMut.reset();
    deleteMut.reset();
    try {
      await deleteMut.mutateAsync();
      setTelegramId("");
      setConsentConfirmed(false);
      toast.success(t.telegramRevokedToast);
    } catch {
      // Inline ErrorPanel renders structured API error details.
    }
  };

  return (
    <Card data-testid="telegram-optin-card" className={className}>
      <CardHeader
        title={
          <span className="inline-flex items-center gap-2">
            <MessageCircle className="h-4 w-4 text-navy" strokeWidth={1.75} aria-hidden="true" />
            {t.telegramTitle}
          </span>
        }
        description={t.telegramSubtitle}
        actions={
          optIn.data ? (
            <div className="flex flex-wrap items-center gap-2">
              <Badge data-testid="telegram-optin-status" tone={isActive ? "success" : "neutral"}>
                {isActive ? `${t.telegramActive}${linkedDisplay ? ` · ${linkedDisplay}` : ""}` : t.telegramInactive}
              </Badge>
              {optIn.data.linked_via_qr && isActive && (
                <Badge tone="info" className="rounded-none">
                  {t.telegramLinkedViaQr}
                </Badge>
              )}
            </div>
          ) : null
        }
      />

      {optIn.isLoading && !optIn.data ? (
        <div className="mt-4 space-y-3" aria-busy="true" aria-label={strings.common.loading}>
          <Skeleton className="h-11 w-full" />
          <Skeleton className="h-10 w-64" />
        </div>
      ) : endpointMissing ? (
        <Stripe kind="neutral" className="mt-4">
          {t.telegramUnavailable}
        </Stripe>
      ) : optIn.isError && optIn.error ? (
        <div className="mt-4">
          <ErrorPanel error={optIn.error} onRetry={() => void optIn.refetch()} />
        </div>
      ) : (
        <div className="mt-4 space-y-4">
          {optIn.data && (optIn.data.branches?.length ?? 0) > 0 && (
            <div className="rounded border border-line bg-surface px-3 py-2.5">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <span className="text-[13px] font-bold text-navy">{t.telegramBranchLabel}</span>
                <TelegramBranchControls data={optIn.data} />
              </div>
            </div>
          )}

          {!isActive && optIn.data && (
            <TelegramQrPairingBlock active={false} enabled />
          )}

          <form onSubmit={handleSave} className="space-y-4" noValidate>
            {!isActive && (
              <p className="text-[13px] font-bold text-ink-secondary">{t.telegramManualTitle}</p>
            )}

            <Field label={t.telegramInputLabel} hint={t.telegramInputHint}>
              {(f) => (
                <TextInput
                  {...f}
                  data-testid="telegram-id-input"
                  value={telegramId}
                  onChange={(ev) => {
                    setTelegramId(ev.target.value);
                    if (validationError) setValidationError(null);
                  }}
                  placeholder={t.telegramInputPlaceholder}
                  autoComplete="off"
                  invalid={Boolean(validationError)}
                />
              )}
            </Field>

            <label className="flex cursor-pointer items-start gap-2.5 rounded border border-line bg-surface px-3 py-2.5 text-[14px] text-ink">
              <input
                type="checkbox"
                data-testid="telegram-consent-checkbox"
                className="mt-1 h-4 w-4 shrink-0 accent-navy"
                checked={consentConfirmed}
                onChange={(ev) => {
                  setConsentConfirmed(ev.target.checked);
                  if (validationError) setValidationError(null);
                }}
              />
              <span>{t.telegramConsentLabel}</span>
            </label>

            {validationError && (
              <Stripe kind="error" role="alert" compact data-testid="telegram-validation-error">
                {validationError}
              </Stripe>
            )}

            {updateMut.isError && <ErrorPanel error={updateMut.error} />}
            {deleteMut.isError && <ErrorPanel error={deleteMut.error} />}

            <div className="flex flex-wrap items-center gap-2">
              <Button
                type="submit"
                variant="primary"
                size="sm"
                loading={updateMut.isPending}
                data-testid="telegram-save-btn"
              >
                {t.telegramSave}
              </Button>
              {(isActive || Boolean(linkedDisplay)) && (
                <Button
                  type="button"
                  variant="danger-outline"
                  size="sm"
                  loading={deleteMut.isPending}
                  onClick={handleRevoke}
                  data-testid="telegram-revoke-btn"
                >
                  {t.telegramRevoke}
                </Button>
              )}
            </div>

            <Stripe
              kind="neutral"
              compact
              icon={<ShieldCheck className="h-4 w-4 text-navy" strokeWidth={1.75} aria-hidden="true" />}
              title={t.telegramStepTitle}
              actions={
                <a
                  href={botHref}
                  target="_blank"
                  rel="noreferrer"
                  data-testid="telegram-bot-link"
                  className="inline-flex items-center gap-1 rounded border border-navy bg-white px-2.5 py-1 text-[13px] font-bold text-navy hover:bg-primary-soft"
                >
                  {t.telegramOpenBot(botUsername)}
                  <ExternalLink className="h-3.5 w-3.5" aria-hidden="true" />
                </a>
              }
            >
              <ol className="mt-1 space-y-0.5 text-[13px] text-ink-secondary">
                <li>{t.telegramStep1(botUsername)}</li>
                <li>{t.telegramStep2}</li>
              </ol>
            </Stripe>
          </form>
        </div>
      )}
    </Card>
  );
}
