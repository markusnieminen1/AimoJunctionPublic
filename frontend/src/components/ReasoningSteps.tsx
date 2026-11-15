// Reusable step-by-step reasoning timeline for AuditPage (details.reasoning_steps) and MenuPlanActionCard.
import { Bot, ShieldCheck, User } from "lucide-react";
import type { ReasoningStepOut } from "../api/types";
import { formatDateTime } from "../lib/dates";
import { Badge } from "./Badge";

export function formatDetailValue(v: unknown): string {
  if (v == null) return "null";
  if (typeof v === "string" || typeof v === "number" || typeof v === "boolean") return String(v);
  if (Array.isArray(v)) return v.map(formatDetailValue).join(", ");
  try {
    return JSON.stringify(v);
  } catch {
    return String(v);
  }
}

export function DetailChips({ details }: { details?: Record<string, unknown> }) {
  if (!details) return null;
  const entries = Object.entries(details).filter(([, v]) => v !== undefined && v !== null && v !== "");
  if (entries.length === 0) return null;
  return (
    <div className="mt-1.5 flex flex-wrap gap-1.5">
      {entries.map(([k, v]) => (
        <span key={k} className="font-mono-code max-w-full break-all rounded border border-line bg-primary-soft px-2 py-0.5 text-[11px] text-ink-secondary">
          <span className="font-semibold text-navy">{k}:</span> {formatDetailValue(v)}
        </span>
      ))}
    </div>
  );
}

export function ActorKindBadge({ kind, name }: { kind?: string | null; name?: string | null }) {
  const k = (kind ?? "system").toLowerCase();
  if (k === "agent" || k === "assistant") {
    return (
      <span className="inline-flex items-center gap-1 rounded-sm border border-lavender bg-lavender px-1.5 py-0.5 text-[11.5px] font-bold text-navy">
        <Bot className="h-3.5 w-3.5 shrink-0" strokeWidth={1.75} aria-hidden="true" />
        <span>{name || (k === "agent" ? "Agent" : "Assistant")}</span>
      </span>
    );
  }
  if (k === "user") {
    return (
      <span className="inline-flex items-center gap-1 rounded-sm border border-line bg-primary-soft px-1.5 py-0.5 text-[11.5px] font-semibold text-ink">
        <User className="h-3.5 w-3.5 shrink-0 text-navy" strokeWidth={1.75} aria-hidden="true" />
        <span>{name || "User"}</span>
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1 rounded-sm border border-line bg-surface-muted px-1.5 py-0.5 text-[11.5px] font-semibold text-ink-secondary">
      <ShieldCheck className="h-3.5 w-3.5 shrink-0 text-navy" strokeWidth={1.75} aria-hidden="true" />
      <span>{name || "Gateway"}</span>
    </span>
  );
}

export function ReasoningSteps({
  steps,
  testId = "reasoning-steps",
}: {
  steps: ReasoningStepOut[];
  testId?: string;
}) {
  if (!steps.length) return null;
  return (
    <ol className="relative space-y-3" data-testid={testId}>
      {steps.map((s, i) => (
        <li key={`${s.step}-${s.stage}-${i}`} className="relative pl-7">
          {i < steps.length - 1 && <span className="absolute left-[10px] top-6 h-[calc(100%-8px)] w-[2px] bg-[#DEE4E6]" aria-hidden="true" />}
          <span className="tabular-nums absolute left-0 top-0.5 flex h-5 w-5 items-center justify-center rounded-full bg-navy text-[10px] font-bold text-white">
            {s.step || i + 1}
          </span>
          <div className="rounded border border-line bg-white px-3 py-2.5">
            <div className="flex flex-wrap items-center gap-2 text-xs">
              <Badge tone="info" className="font-mono-code">
                {s.stage}
              </Badge>
              <ActorKindBadge kind={s.actor_kind} />
              <time dateTime={s.at} className="tabular-nums text-[12px] text-ink-muted">
                {formatDateTime(s.at)}
              </time>
            </div>
            <p className="mt-1 text-[13.5px] leading-5 text-ink tabular-nums">{s.summary}</p>
            <DetailChips details={s.details} />
          </div>
        </li>
      ))}
    </ol>
  );
}
