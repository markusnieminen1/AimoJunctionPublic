// M11 "How we got here" unified reasoning & conversation trace on /orders/:id (GET /api/orders/:id/trace).
import { useState } from "react";
import { Link } from "react-router-dom";
import { ChevronDown, ChevronRight, MessageSquare, Network, ShieldAlert, ShieldCheck } from "lucide-react";
import { isEndpointMissing, toApiError } from "../../api/client";
import { useOrderTrace } from "../../api/hooks";
import type { OrderTraceStepSource } from "../../api/types";
import { useStrings } from "../../i18n";
import { formatDateTime } from "../../lib/dates";
import { Badge, type BadgeTone } from "../Badge";
import { Button } from "../Button";
import { Card } from "../Card";
import { CopyChip } from "../CopyChip";
import { ErrorPanel } from "../ErrorPanel";
import { ActorKindBadge, DetailChips } from "../ReasoningSteps";
import { RequestIdChip } from "../RequestIdChip";
import { Skeleton } from "../Skeleton";

const SOURCE_TONE: Record<OrderTraceStepSource, BadgeTone> = {
  assistant: "navy",
  a2a: "navy",
  reasoning: "info",
  audit: "neutral",
  order_event: "success",
};

export function OrderTraceSection({ orderId }: { orderId: string }) {
  const t = useStrings().orders;
  const q = useOrderTrace(orderId, { silent: true });
  const [expanded, setExpanded] = useState(true);

  const trace = q.data;
  const steps = trace?.steps ?? [];
  const chatTrace = trace?.chat_trace && typeof trace.chat_trace === "object" ? trace.chat_trace : null;
  const chatUser = typeof chatTrace?.user_message === "string" ? chatTrace.user_message : null;
  const chatReply = typeof chatTrace?.assistant_reply === "string" ? chatTrace.assistant_reply : null;
  const chatChannel = typeof chatTrace?.channel === "string" ? chatTrace.channel : null;
  const chatScore = typeof chatTrace?.assurance_score === "number" ? chatTrace.assurance_score : null;
  const chatWarnings = Array.isArray(chatTrace?.processing_warnings)
    ? chatTrace.processing_warnings.filter((w): w is string => typeof w === "string")
    : [];

  return (
    <section className="space-y-3" data-testid="order-trace" aria-labelledby="order-trace-heading">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h2 id="order-trace-heading" className="h-section">
            {t.traceTitle}
          </h2>
          <p className="text-xs text-ink-secondary">{t.traceLead}</p>
        </div>
        {trace && steps.length > 0 && (
          <Button
            variant="text"
            size="sm"
            aria-expanded={expanded}
            onClick={() => setExpanded((v) => !v)}
            icon={expanded ? <ChevronDown className="h-4 w-4" strokeWidth={1.75} /> : <ChevronRight className="h-4 w-4" strokeWidth={1.75} />}
          >
            {expanded ? t.traceHide : t.traceShow}
          </Button>
        )}
      </div>

      <Card>
        {q.isLoading ? (
          <div className="space-y-3" aria-busy="true" aria-label="Loading order trace">
            <Skeleton className="h-6 w-56" />
            <Skeleton className="h-20 w-full" />
            <Skeleton className="h-20 w-full" />
          </div>
        ) : q.isError && !trace ? (
          isEndpointMissing(q.error) || toApiError(q.error).status === 404 ? (
            <p className="text-sm text-ink-muted">{t.traceUnavailable}</p>
          ) : (
            <ErrorPanel error={q.error} title="Could not load order trace" onRetry={() => void q.refetch()} />
          )
        ) : !trace ? null : (
          <div className="space-y-4">
            <div className="flex flex-wrap items-center gap-2 border-b border-line pb-3 text-xs">
              <Badge
                tone={trace.chain_verified ? "success" : "danger"}
                icon={trace.chain_verified ? <ShieldCheck className="h-3.5 w-3.5" strokeWidth={2} /> : <ShieldAlert className="h-3.5 w-3.5" strokeWidth={2} />}
                data-testid="trace-chain-badge"
              >
                {trace.chain_verified ? t.traceChainVerified : t.traceChainBroken}
              </Badge>

              {trace.source_conversation_id && (
                <span className="inline-flex min-w-0 max-w-full flex-wrap items-center gap-1.5 text-ink-secondary">
                  <MessageSquare className="h-3.5 w-3.5 shrink-0 text-navy" strokeWidth={1.75} aria-hidden="true" />
                  <span className="shrink-0">{t.traceConversation}:</span>
                  <CopyChip value={trace.source_conversation_id} />
                </span>
              )}

              {trace.source_a2a_task_id && (
                <Link
                  to="/audit?view=a2a"
                  className="inline-flex min-w-0 max-w-full items-center gap-1.5 rounded border border-lavender bg-lavender/60 px-2 py-0.5 font-semibold text-navy hover:underline"
                >
                  <Network className="h-3.5 w-3.5 shrink-0" strokeWidth={1.75} aria-hidden="true" />
                  <span className="shrink-0">{t.traceA2ATask}:</span>
                  <span className="font-mono-code min-w-0 truncate">{trace.source_a2a_task_id}</span>
                </Link>
              )}
            </div>

            {chatTrace && (chatUser || chatReply) && (
              <div className="space-y-1.5 rounded border border-line bg-primary-soft/50 px-3.5 py-2.5 text-xs" data-testid="order-chat-trace">
                <div className="flex flex-wrap items-center gap-1.5">
                  <span className="font-bold uppercase tracking-[0.04em] text-navy">{t.traceChatTitle}</span>
                  {chatChannel && <Badge tone="neutral">{chatChannel}</Badge>}
                  {chatScore != null && <Badge tone="info">{chatScore}%</Badge>}
                  {chatWarnings.map((w) => (
                    <Badge key={w} tone="warning">
                      {w}
                    </Badge>
                  ))}
                </div>
                {chatUser && (
                  <p className="text-ink">
                    <span className="font-semibold text-ink-secondary">{t.traceChatUser}: </span>
                    <span className="whitespace-pre-wrap break-words">{chatUser}</span>
                  </p>
                )}
                {chatReply && (
                  <p className="text-ink-secondary">
                    <span className="font-semibold text-ink-secondary">{t.traceChatReply}: </span>
                    <span className="whitespace-pre-wrap break-words">{chatReply}</span>
                  </p>
                )}
              </div>
            )}

            {steps.length === 0 ? (
              <p className="text-sm text-ink-muted">{t.traceEmpty}</p>
            ) : expanded ? (
              <ol className="relative space-y-3" data-testid="order-trace-steps">
                {steps.map((s, i) => (
                  <li key={`${s.step}-${s.stage}-${i}`} className="relative pl-7" data-testid="order-trace-step">
                    {i < steps.length - 1 && <span className="absolute left-[10px] top-6 h-[calc(100%-8px)] w-[2px] bg-[#DEE4E6]" aria-hidden="true" />}
                    <span className="tabular-nums absolute left-0 top-1 flex h-5 w-5 items-center justify-center rounded-full bg-navy text-[10px] font-bold text-white">
                      {s.step || i + 1}
                    </span>
                    <div className="rounded border border-line bg-primary-soft/40 px-3.5 py-2.5">
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <div className="flex flex-wrap items-center gap-1.5 text-xs">
                          <Badge tone={SOURCE_TONE[s.source] ?? "neutral"}>{s.source}</Badge>
                          <span className="font-mono-code rounded bg-white px-1.5 py-0.5 text-[11px] font-semibold text-navy border border-line">
                            {s.stage}
                          </span>
                          <ActorKindBadge kind={s.actor_kind} name={s.actor_name} />
                          <time dateTime={s.at} className="tabular-nums text-[12px] text-ink-muted">
                            {formatDateTime(s.at)}
                          </time>
                        </div>
                        {s.request_id && <RequestIdChip id={s.request_id} />}
                      </div>
                      <p className="mt-1.5 text-[14px] leading-5 text-ink tabular-nums">{s.summary}</p>
                      <DetailChips details={s.details} />
                    </div>
                  </li>
                ))}
              </ol>
            ) : null}
          </div>
        )}
      </Card>
    </section>
  );
}
