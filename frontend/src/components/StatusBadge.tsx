import { Loader2 } from "lucide-react";
import type { OrderStatus } from "../api/types";
import { useStrings } from "../i18n";
import { Dot, type DotTone } from "./Badge";

// Order status as "● label" (DESIGN_VALIO §4.4; meaning per DESIGN.md §6F). SUBMITTING spins, SUBMISSION_UNKNOWN gets a
// dashed amber ring and reads "Confirming with Valio Aimo…". Never paired with a retry button.
const MAP: Record<OrderStatus, { dot: DotTone | "spinner"; dashed?: boolean; text: string; textOnDark: string }> = {
  PLACED: { dot: "lime", text: "text-ink", textOnDark: "text-white" },
  PENDING_APPROVAL: { dot: "yellow", text: "text-ink", textOnDark: "text-white" },
  SUBMITTING: { dot: "spinner", text: "text-ink", textOnDark: "text-white" },
  SUBMISSION_UNKNOWN: { dot: "yellow", dashed: true, text: "text-ink", textOnDark: "text-white" },
  REJECTED: { dot: "red", text: "text-danger", textOnDark: "text-white" },
  FAILED: { dot: "red", text: "text-danger", textOnDark: "text-white" },
  EXPIRED: { dot: "grey", text: "text-ink-muted", textOnDark: "text-white/80" },
  CANCELLED: { dot: "grey", text: "text-ink-muted", textOnDark: "text-white/80" },
};

/** Order status dot + label for every OrderStatus. `onDark` for the petrol order header bar. */
export function StatusBadge({ status, onDark = false, className = "" }: { status: OrderStatus; onDark?: boolean; className?: string }) {
  const strings = useStrings();
  const m = MAP[status] ?? MAP.CANCELLED;
  return (
    <span className={`inline-flex items-center gap-2 whitespace-nowrap text-[14px] leading-[17px] ${onDark ? m.textOnDark : m.text} ${className}`} data-status={status}>
      {m.dot === "spinner" ? (
        <Loader2 className={`h-3 w-3 shrink-0 animate-spin ${onDark ? "text-white" : "text-navy"}`} strokeWidth={2.5} aria-hidden="true" />
      ) : (
        <Dot tone={m.dot} dashed={m.dashed} />
      )}
      {strings.orderStatus[status] ?? status}
    </span>
  );
}
