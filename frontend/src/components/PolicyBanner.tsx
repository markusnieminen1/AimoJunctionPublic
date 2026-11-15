import { AlertOctagon, AlertTriangle, CheckCircle2 } from "lucide-react";
import type { PolicyDecision } from "../api/types";
import { useStrings } from "../i18n";

// Policy outcome stripe with a 4 px left border (DESIGN.md §6D rules, DESIGN_VALIO §4.3 look).
const STYLE = {
  AUTO: { box: "bg-success-bg border-l-success", text: "text-success", Icon: CheckCircle2 },
  APPROVAL_REQUIRED: { box: "bg-yellow-soft border-l-warning", text: "text-warning", Icon: AlertTriangle },
  BLOCKED: { box: "bg-red-soft border-l-danger", text: "text-danger", Icon: AlertOctagon },
} as const;

/**
 * Policy outcome banner (AUTO / APPROVAL_REQUIRED / BLOCKED) listing every fired rule_id + message.
 * `title` overrides the default heading (e.g. "This order will be placed directly" in a preview).
 */
export function PolicyBanner({ decision, title, className = "" }: { decision: PolicyDecision; title?: string; className?: string }) {
  const strings = useStrings();
  const s = STYLE[decision.outcome] ?? STYLE.APPROVAL_REQUIRED;
  return (
    <div className={`rounded-[3px] border-l-4 px-4 py-3 ${s.box} ${className}`} data-outcome={decision.outcome}>
      <div className={`flex items-center gap-2 text-[14px] font-bold ${s.text}`}>
        <s.Icon className="h-5 w-5 shrink-0" strokeWidth={1.75} />
        {title ?? strings.policy[decision.outcome]?.title ?? decision.outcome}
      </div>
      {decision.rules.length > 0 && (
        <ul className="mt-2 space-y-1 pl-7">
          {decision.rules.map((r, i) => (
            <li key={`${r.rule_id}-${i}`} className="text-[13px] leading-5 text-ink">
              <span className="font-mono-code mr-2 text-[13px] font-bold">{r.rule_id}</span>
              <span className="tabular-nums">{r.message}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
