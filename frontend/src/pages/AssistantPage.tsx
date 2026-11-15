// M8 /assistant, M10 look: the portal's "Ask A.I." band (lime-light, lime-framed composer, examples) on top and the
// conversation on white below (max 860). Desktop: the composer row sticks under the header; phones: it sticks to the
// bottom of the screen. `?prompt=` (from the dashboard band) is sent once on arrival. POST /api/assistant/chat.
// The LLM only extracts intent; quotes, policy and order state are rendered exactly as the API returns them.
// Chat errors (e.g. 503 LLM_UNAVAILABLE) show inline with code + request_id and a per-message Retry
// (safe: a chat message is not an order mutation; orders go through QuotePanel's Idempotency-Key flow).
import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { AlertTriangle, Bot, MessageSquarePlus } from "lucide-react";
import { ApiError, ENDPOINT_MISSING, toApiError } from "../api/client";
import { useAssistantChat, useAssistantSettings } from "../api/hooks";
import type { Order } from "../api/types";
import { chatStore, toChatError, useChatState, type AssistantMessage, type ChatMessage, type UserMessage } from "../assistant/chatStore";
import { useAuth } from "../auth/AuthContext";
import { Badge } from "../components/Badge";
import { ActionCard } from "../components/assistant/ActionCards";
import { TelegramConnectBanner } from "../components/approvals/TelegramConnectBanner";
import { Button } from "../components/Button";
import { AskComposer, AskDisclaimer, AskExamples, BotMark } from "../components/assistant/AskBand";
import { CopyChip } from "../components/CopyChip";
import { ErrorPanel } from "../components/ErrorPanel";
import { MarkdownText } from "../components/MarkdownText";
import { useStrings } from "../i18n";

function UserBubble({ m, busy, onRetry }: { m: UserMessage; busy: boolean; onRetry: () => void }) {
  const t = useStrings().assistant;
  const err = m.error;
  return (
    <div className="flex flex-col items-end gap-2" data-testid="user-message">
      <p className="max-w-[85%] whitespace-pre-wrap break-words rounded bg-primary-soft px-4 py-3 text-[16px] leading-[21px] text-ink">
        <span className="sr-only">{t.you}: </span>
        {m.text}
      </p>
      {m.status === "error" && err && (
        <div className="w-full sm:max-w-[85%]" data-testid="chat-error">
          <ErrorPanel
            error={new ApiError(err.status, err.code, err.message, err.request_id)}
            title={
              err.code === "LLM_UNAVAILABLE"
                ? t.unavailableTitle
                : err.code === ENDPOINT_MISSING
                  ? t.notOnServerTitle
                  : err.code === "INTERRUPTED"
                    ? t.interrupted
                    : t.errorTitle
            }
            onRetry={busy ? undefined : onRetry}
          />
        </div>
      )}
    </div>
  );
}

/** Orders the conversation already returned, by their `quote_id` (API data), so an ordered quote card stops offering Place order. */
function ordersByQuote(messages: ChatMessage[]): Map<string, Order> {
  const map = new Map<string, Order>();
  for (const m of messages) {
    if (m.role !== "assistant") continue;
    for (const o of Object.values(m.placed ?? {})) if (o?.quote_id) map.set(o.quote_id, o);
    for (const a of m.actions) {
      const order = (a as { type?: unknown; order?: Order } | null)?.type === "order" ? (a as { order?: Order }).order : undefined;
      if (order && typeof order.quote_id === "string") map.set(order.quote_id, order);
    }
  }
  return map;
}

function AssistantBubble({
  m,
  busy,
  ordered,
  onChoose,
  onOrderPlaced,
}: {
  m: AssistantMessage;
  busy: boolean;
  ordered: Map<string, Order>;
  onChoose: (i: number, o: string) => void;
  onOrderPlaced: (i: number, o: Order) => void;
}) {
  const t = useStrings().assistant;
  return (
    <div className="flex items-start gap-3" data-testid="assistant-message">
      <span className="mt-0.5 hidden h-8 w-8 shrink-0 items-center justify-center rounded-full bg-navy text-white sm:flex" aria-hidden="true">
        <Bot className="h-4 w-4" strokeWidth={1.5} />
      </span>
      <div className="min-w-0 flex-1 space-y-3">
        <div>
          <div className="mb-1 flex flex-wrap items-center gap-2">
            <p className="flex items-center gap-1 text-[12px] font-medium text-ink-muted">
              <Bot className="h-3.5 w-3.5 sm:hidden" strokeWidth={1.75} />
              {t.caption}
            </p>
            {m.assuranceScore != null && (
              <Badge tone={m.autoExecuted ? "success" : "info"} data-testid="assurance-badge">
                {t.assuranceBadge(m.assuranceScore, m.assuranceThreshold ?? 80, Boolean(m.autoExecuted))}
              </Badge>
            )}
          </div>
          {m.text && <MarkdownText text={m.text} className="text-[16px] leading-[22px] text-ink" />}
        </div>
        {(m.fallbackUsed || (m.processingWarnings && m.processingWarnings.length > 0)) && (
          <div
            className="flex flex-wrap items-center gap-2 rounded border border-warning-border bg-yellow-soft px-3 py-2 text-[12px] text-ink"
            role="status"
            data-testid="assistant-processing-warning"
          >
            <AlertTriangle className="h-3.5 w-3.5 shrink-0 text-warning" strokeWidth={2} aria-hidden="true" />
            <span className="font-bold">{t.processingWarningTitle}:</span>
            {m.fallbackUsed && <span>{t.processingFallback}</span>}
            {m.processingWarnings?.map((w) => (
              <span key={w} className="font-mono-code rounded border border-warning-border bg-white px-1.5 py-0.5 text-[11px]">
                {w}
              </span>
            ))}
          </div>
        )}
        {m.actions.map((a, i) => (
          <ActionCard
            key={i}
            action={a}
            h={{
              answer: m.answers?.[i],
              placedOrder: m.placed?.[i] ?? (a?.type === "quote" && a.quote ? ordered.get(a.quote.id) : undefined),
              busy,
              onChoose: (o) => onChoose(i, o),
              onOrderPlaced: (o) => onOrderPlaced(i, o),
            }}
          />
        ))}
      </div>
    </div>
  );
}

function TypingIndicator() {
  const t = useStrings().assistant;
  return (
    <div className="flex items-center gap-3" role="status" aria-live="polite" data-testid="assistant-typing">
      <span className="hidden h-8 w-8 shrink-0 items-center justify-center rounded-full bg-navy text-white sm:flex" aria-hidden="true">
        <Bot className="h-4 w-4" strokeWidth={1.5} />
      </span>
      <div className="flex items-center gap-2">
        <span className="flex gap-1" aria-hidden="true">
          {[0, 150, 300].map((d) => (
            <span key={d} className="h-2 w-2 animate-bounce rounded-full bg-navy" style={{ animationDelay: `${d}ms` }} />
          ))}
        </span>
        <span className="text-[14px] text-ink-muted">{t.thinking}</span>
      </div>
    </div>
  );
}

export default function AssistantPage() {
  const strings = useStrings();
  const t = strings.assistant;
  const { principal } = useAuth();
  const state = useChatState();
  const chat = useAssistantChat({ silent: true });
  const assistantSettings = useAssistantSettings({ silent: true });
  const threshold = assistantSettings.data?.order_assurance_threshold ?? 80;
  const [draft, setDraft] = useState("");
  const endRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    if (principal) chatStore.claim(principal.id);
  }, [principal]);

  const busy = state.messages.some((m) => m.role === "user" && m.status === "pending");

  const request = useCallback(
    (userMsgId: string, text: string) => {
      const conversation_id = chatStore.get().conversationId ?? undefined;
      chat
        .mutateAsync({ conversation_id, message: text })
        .then((r) =>
          chatStore.received(userMsgId, r.conversation_id, r.reply, r.actions, {
            assuranceScore: r.assurance_score,
            assuranceThreshold: r.assurance_threshold,
            autoExecuted: r.auto_executed,
            fallbackUsed: r.fallback_used,
            processingWarnings: r.processing_warnings,
          }),
        )
        .catch((e: unknown) => chatStore.failed(userMsgId, toChatError(toApiError(e))));
    },
    [chat],
  );

  const send = useCallback(
    (raw: string) => {
      const text = raw.trim();
      if (!text || busy) return false;
      request(chatStore.addUser(text), text);
      return true;
    },
    [busy, request],
  );

  // Prompt handed over from the dashboard's Ask band (/assistant?prompt=…): send it once, then drop the param.
  const [params, setParams] = useSearchParams();
  const prompt = params.get("prompt");
  const sentPrompt = useRef<string | null>(null);
  useEffect(() => {
    if (!prompt || !principal || sentPrompt.current === prompt) return;
    sentPrompt.current = prompt;
    send(prompt);
    setParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        next.delete("prompt");
        return next;
      },
      { replace: true },
    );
  }, [prompt, principal, send, setParams]);

  const retry = (m: UserMessage) => {
    if (busy) return;
    chatStore.retrying(m.id);
    request(m.id, m.text);
  };

  const submit = () => {
    if (send(draft)) setDraft("");
  };

  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      submit();
    }
  };

  // Auto-scroll to the newest message / typing indicator.
  const count = state.messages.length;
  useEffect(() => {
    if (count > 0) endRef.current?.scrollIntoView?.({ behavior: "smooth", block: "end" });
  }, [count, busy]);

  // Grow the composer with its content (max ~5 lines).
  useEffect(() => {
    const el = inputRef.current;
    if (!el) return;
    el.style.height = "auto";
    // Empty: keep the 48 px default (Chrome counts a wrapping placeholder in scrollHeight).
    if (!draft) return;
    el.style.height = `${Math.min(el.scrollHeight, 132)}px`;
  }, [draft]);

  const empty = count === 0;
  const ordered = useMemo(() => ordersByQuote(state.messages), [state.messages]);

  const newConversation = (
    <Button
      variant="text"
      size="md"
      icon={<MessageSquarePlus className="h-4 w-4" strokeWidth={1.5} />}
      disabled={empty && !state.conversationId}
      onClick={() => {
        chatStore.reset();
        setDraft("");
        inputRef.current?.focus();
      }}
    >
      {t.newConversation}
    </Button>
  );

  // One composer, placed by CSS order: in the band (sticky under the header) on desktop, sticky at the bottom on phones.
  return (
    <div className="flex min-h-[calc(100vh-var(--header-h))] flex-col">
      <section className="order-1 bg-lime-soft" aria-labelledby="assistant-title">
        <div className="container-portal pt-8 lg:pt-10">
          <div className="flex flex-wrap items-end justify-between gap-3">
            <div className="flex items-end gap-4">
              <BotMark />
              <h1 id="assistant-title" className="font-display text-[32px] leading-[35px] tracking-[0.01em] text-navy md:text-[40px] md:leading-[44px]">
                {strings.front.askTitle}
              </h1>
            </div>
            {newConversation}
          </div>
          <div className="mt-3 flex flex-wrap items-center gap-2">
            {principal && <p className="text-[14px] text-navy">{t.actingAs(principal.name)}</p>}
            {assistantSettings.data && (
              <Link
                to="/agents"
                data-testid="assistant-threshold-pill"
                title={t.assuranceLinkSettings}
                className="inline-flex items-center gap-1 rounded-none border border-navy/25 bg-white px-2.5 py-0.5 text-[12px] font-bold tabular-nums text-navy hover:bg-primary-soft"
              >
                <span>{t.assuranceQuickLabel}:</span>
                <span>{threshold}%</span>
              </Link>
            )}
          </div>
        </div>
      </section>

      <div
        className={
          empty
            ? "order-2 bg-lime-soft lg:sticky lg:top-[var(--header-h)] lg:z-20"
            : "sticky bottom-0 z-20 order-4 border-t border-line bg-lime-soft shadow-portal-sm"
        }
        data-testid="composer-row"
      >
        <div className="container-portal py-2 lg:py-3">
          <AskComposer
            onSubmit={(e) => {
              e.preventDefault();
              submit();
            }}
            disabled={!draft.trim() || busy}
            busy={busy}
            submitLabel={t.send}
          >
            <label htmlFor="assistant-input" className="sr-only">
              {t.placeholder}
            </label>
            <textarea
              id="assistant-input"
              ref={inputRef}
              rows={1}
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={onKeyDown}
              placeholder={t.placeholder}
              className="block max-h-[132px] min-h-[48px] w-full resize-none rounded-sm border border-line-strong bg-white py-3 pl-[54px] pr-3 text-[16px] leading-[22px] text-ink placeholder:truncate placeholder:text-ink-muted focus:outline-none focus:ring-2 focus:ring-navy"
            />
          </AskComposer>
          <p className="mt-1 hidden text-[12px] text-navy lg:block">{t.composerHint}</p>
        </div>
      </div>

      <section className={`${empty ? "order-3" : "order-2"} bg-lime-soft`}>
        <div className="container-portal pb-8 pt-3">
          <TelegramConnectBanner className="mb-3" />
          <AskDisclaimer />
          <AskExamples onPick={send} disabled={busy} className="mt-4" />
          <div className="mt-3 flex flex-wrap items-center gap-2" data-testid="judge-demo-starters">
            <span className="text-[12px] font-bold uppercase tracking-wide text-navy">{t.judgeStartersTitle}:</span>
            {t.judgeStarters.map((starter) => (
              <button
                key={starter.label}
                type="button"
                disabled={busy}
                onClick={() => send(starter.prompt)}
                className="rounded-full border border-navy/30 bg-white px-3 py-1 text-[12px] font-bold text-navy hover:bg-primary-soft disabled:opacity-50"
              >
                {starter.label}
              </button>
            ))}
          </div>
        </div>
      </section>

      <div className={`${empty ? "order-4" : "order-3"} flex-1`}>
        <div className="mx-auto w-full max-w-[892px] space-y-6 px-4 pb-10 pt-8" aria-live="polite" data-testid="chat-stream">
          {empty ? (
            <div className="text-center">
              <h2 className="h-card">{t.emptyTitle}</h2>
              <p className="mx-auto mt-2 max-w-[640px] text-[16px] leading-[21px] text-ink">{t.emptyBody}</p>
            </div>
          ) : (
            <>
              {state.conversationId && (
                <div className="flex flex-wrap items-center justify-center gap-1.5 text-[12px] text-ink-muted">
                  <span>{t.conversation}</span>
                  <CopyChip value={state.conversationId} />
                </div>
              )}
              {state.messages.map((m) =>
                m.role === "user" ? (
                  <UserBubble key={m.id} m={m} busy={busy} onRetry={() => retry(m)} />
                ) : (
                  <AssistantBubble
                    key={m.id}
                    m={m}
                    busy={busy}
                    ordered={ordered}
                    onChoose={(i, o) => {
                      if (busy) return;
                      chatStore.answer(m.id, i, o);
                      send(o);
                    }}
                    onOrderPlaced={(i, o) => chatStore.placed(m.id, i, o)}
                  />
                ),
              )}
              {busy && <TypingIndicator />}
            </>
          )}
          <div ref={endRef} className="h-px scroll-mb-40" />
        </div>
      </div>
    </div>
  );
}
