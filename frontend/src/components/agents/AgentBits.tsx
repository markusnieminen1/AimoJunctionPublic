// Small agent display pieces shared by /agents and /agents/:id (display only; the server enforces every limit).
import type { ReactNode } from "react";
import { Ban, Bot, CheckCircle2, Clock, ShieldAlert, ShieldCheck } from "lucide-react";
import type { Agent } from "../../api/types";
import { formatDayDate, helsinkiDate } from "../../lib/dates";
import { formatEur, formatEurCompact, formatNumber } from "../../lib/format";
import { getStrings, useStrings } from "../../i18n";

export type AgentDisplayState = "active" | "disabled" | "quarantined" | "expired";

/** Display state: disabled/quarantined win; an active agent whose `expires_at` is before server now shows as expired. */
export function agentDisplayState(a: Agent, serverNowMs: number): AgentDisplayState {
  if (a.status === "disabled") return "disabled";
  if (a.status === "quarantined") return "quarantined";
  if (a.expires_at && Date.parse(a.expires_at) < serverNowMs) return "expired";
  return "active";
}

export function agentStateLabel(state: AgentDisplayState, t = getStrings().agents): string {
  switch (state) {
    case "active":
      return t.active;
    case "disabled":
      return t.disabled;
    case "quarantined":
      return t.quarantined;
    case "expired":
      return t.expired;
  }
}

const DOT: Record<AgentDisplayState, string> = {
  active: "bg-lime",
  disabled: "bg-line-strong",
  quarantined: "bg-red",
  expired: "bg-red",
};

/** Coloured status dot with accessible label. */
export function AgentStatusDot({ state }: { state: AgentDisplayState }) {
  const label = agentStateLabel(state, useStrings().agents);
  return <span className={`inline-block h-2.5 w-2.5 shrink-0 rounded-full ${DOT[state]}`} role="img" aria-label={label} title={label} />;
}

/** Sharp square Valio tag (Active / Disabled / Quarantined / Expired). */
export function AgentStatusBadge({ state }: { state: AgentDisplayState }) {
  const t = useStrings().agents;
  const cls =
    state === "active"
      ? "border-[#9AB418] bg-lime text-navy"
      : state === "disabled"
        ? "border-line bg-primary-soft text-ink-secondary"
        : state === "expired"
          ? "border-[#D99B26] bg-[#FFF5DF] text-[#7A5200]"
          : "border-[#B41E17]/40 bg-[#FDECEB] text-[#B41E17]";
  const Icon = state === "active" ? CheckCircle2 : state === "disabled" ? Ban : state === "quarantined" ? ShieldAlert : Clock;
  return (
    <span className={`inline-flex items-center gap-1 whitespace-nowrap rounded-none border px-2 py-0.5 text-[11px] font-bold uppercase tracking-[0.04em] leading-4 ${cls}`}>
      <Icon className="h-3 w-3 shrink-0" strokeWidth={2} />
      {agentStateLabel(state, t)}
    </span>
  );
}

/** Sharp square Valio tag for agents with can_place_orders = false. */
export function QuoteOnlyBadge() {
  const t = useStrings().agents;
  return (
    <span className="inline-flex items-center whitespace-nowrap rounded-none border border-[#005670]/40 bg-primary-soft px-2 py-0.5 text-[11px] font-bold uppercase tracking-[0.04em] leading-4 text-navy">
      {t.quoteOnly}
    </span>
  );
}

/**
 * Spent today vs daily limit, both from the API. The bar is green < 75 %, amber 75–99 %, red ≥ 100 %.
 * (Fill ratio is a visual aid only; the server decides R-DAILY-LIMIT.)
 */
export function SpendBar({ spent, limit, className = "" }: { spent: number; limit: number; className?: string }) {
  const ratio = limit > 0 ? spent / limit : spent > 0 ? 1 : 0;
  const pct = Math.min(100, Math.max(0, ratio * 100));
  const color = ratio >= 1 ? "bg-red" : ratio >= 0.75 ? "bg-yellow" : "bg-lime";
  const text = ratio >= 1 ? "text-danger" : ratio >= 0.75 ? "text-warning" : "text-ink-secondary";
  return (
    <div className={`min-w-[120px] ${className}`}>
      <div className="tabular-nums flex items-baseline justify-between gap-2 text-xs">
        <span className={`font-semibold ${text}`}>{formatEur(spent)}</span>
        <span className="text-ink-muted">/ {formatEurCompact(limit)}</span>
      </div>
      <div
        className="mt-1 h-1.5 w-full overflow-hidden rounded-full bg-[#DEE4E6]"
        role="progressbar"
        aria-label="Spent today of daily limit"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={Math.round(pct)}
      >
        <div className={`h-full rounded-full ${color}`} style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

/** "All" or "3 categories" (allowed_categories empty = all). */
export function categoriesLabel(a: Pick<Agent, "allowed_categories">): string {
  const t = getStrings().agents;
  return a.allowed_categories.length ? t.categoriesCount(a.allowed_categories.length) : t.allCategories;
}

export type PolicyLine = { tone: "auto" | "approval" | "blocked" | "info"; text: string };

/**
 * Plain-English description of an agent's configured limits (PLAN.md M3 "Policy card").
 * This only restates the stored limits; it never evaluates an order.
 */
export function agentPolicyLines(a: Agent, serverNowMs?: number): PolicyLine[] {
  const t = getStrings().agents;
  const auto = formatEurCompact(a.auto_approve_limit_eur);
  const max = formatEurCompact(a.max_order_value_eur);
  const lines: PolicyLine[] = [];
  if (a.status === "disabled") lines.push({ tone: "blocked", text: "Disabled: every request from this agent is refused." });
  if (a.status === "quarantined")
    lines.push({ tone: "blocked", text: "Quarantined: orders from this agent are paused pending supervisor review." });
  if (!a.can_place_orders) {
    lines.push({ tone: "info", text: "May search products and request quotes, but cannot place orders." });
    return lines;
  }
  if (a.auto_approve_limit_eur > 0) lines.push({ tone: "auto", text: `Orders up to ${auto} are placed automatically.` });
  if (a.max_order_value_eur > a.auto_approve_limit_eur)
    lines.push({ tone: "approval", text: a.auto_approve_limit_eur > 0 ? `${auto}–${max} need your approval.` : `Orders up to ${max} need your approval.` });
  lines.push({ tone: "blocked", text: `Over ${max} are blocked.` });
  lines.push({
    tone: "approval",
    text: `Once today's orders would pass ${formatEurCompact(a.daily_spend_limit_eur)}, the next one needs your approval.`,
  });
  if (a.allowed_categories.length)
    lines.push({ tone: "approval", text: `Products outside ${t.categoriesCount(a.allowed_categories.length)} need your approval.` });
  if (a.max_quantity_per_line != null)
    lines.push({ tone: "approval", text: `Lines above ${formatNumber(a.max_quantity_per_line)} sales units need your approval.` });
  if (a.expires_at) {
    const past = serverNowMs != null && Date.parse(a.expires_at) < serverNowMs;
    lines.push({ tone: past ? "blocked" : "info", text: `${past ? "Credentials expired" : "Credentials expire"} ${formatDayDate(helsinkiDate(new Date(a.expires_at)))}.` });
  }
  return lines;
}

/** One-sentence summary for the /agents policy card: "Orders up to €500 are placed automatically. €500–€5,000 need your approval. Over €5,000 are blocked." */
export function agentPolicyHeadline(a: Agent, serverNowMs?: number): string {
  const lines = agentPolicyLines({ ...a, status: "active" }, serverNowMs);
  const core = a.can_place_orders ? lines.filter((l) => l.tone !== "info").slice(0, 3) : lines;
  const prefix =
    a.status === "disabled"
      ? "Disabled now. "
      : a.status === "quarantined"
        ? "Quarantined now. "
        : agentDisplayState(a, serverNowMs ?? 0) === "expired" && serverNowMs != null
          ? "Expired. "
          : "";
  return prefix + core.map((l) => l.text).join(" ");
}

const LINE_ICON = {
  auto: <ShieldCheck className="h-4 w-4 text-success" strokeWidth={1.75} />,
  approval: <Clock className="h-4 w-4 text-warning" strokeWidth={1.75} />,
  blocked: <ShieldAlert className="h-4 w-4 text-danger" strokeWidth={1.75} />,
  info: <Bot className="h-4 w-4 text-ink-muted" strokeWidth={1.75} />,
};

/** Bullet list of policy lines with outcome icons. */
export function PolicyLines({ lines, className = "" }: { lines: PolicyLine[]; className?: string }) {
  return (
    <ul className={`space-y-1.5 text-sm text-ink ${className}`}>
      {lines.map((l) => (
        <li key={l.text} className="tabular-nums flex items-start gap-2">
          <span className="mt-0.5 shrink-0">{LINE_ICON[l.tone]}</span>
          <span>{l.text}</span>
        </li>
      ))}
    </ul>
  );
}

/** Neutral #F2F6F8 policy stripe (DESIGN_VALIO §6). `children` goes below the generic flow line. */
export function PolicyCard({ title, children, className = "" }: { title?: string; children?: ReactNode; className?: string }) {
  const t = useStrings().agents;
  const heading = title ?? t.policyTitle;
  return (
    <section className={`rounded-[3px] border border-line bg-primary-soft p-4 ${className}`}>
      <div className="flex items-start gap-3">
        <ShieldCheck className="mt-0.5 h-5 w-5 shrink-0 text-navy" strokeWidth={1.75} aria-hidden />
        <div className="min-w-0 flex-1">
          <h2 className="text-[15px] font-bold leading-5 text-navy">{heading}</h2>
          <div className="mt-2 flex flex-wrap items-center gap-1.5 text-[11px] font-bold uppercase tracking-[0.04em]">
            <span className="rounded-none border border-[#9AB418] bg-lime px-2 py-0.5 text-navy">AUTO · ≤ auto limit</span>
            {/* Each arrow wraps together with the chip it points to (no dangling arrow at a line end on phones). */}
            <span className="inline-flex items-center gap-1.5">
              <span className="text-ink-muted" aria-hidden>→</span>
              <span className="rounded-none border border-[#D99B26] bg-[#FFF5DF] px-2 py-0.5 text-[#7A5200]">APPROVAL · above auto limit</span>
            </span>
            <span className="inline-flex items-center gap-1.5">
              <span className="text-ink-muted" aria-hidden>→</span>
              <span className="rounded-none border border-[#B41E17]/40 bg-[#FDECEB] px-2 py-0.5 text-[#B41E17]">BLOCKED · above max order</span>
            </span>
          </div>
          {children && <div className="mt-3">{children}</div>}
        </div>
      </div>
    </section>
  );
}
