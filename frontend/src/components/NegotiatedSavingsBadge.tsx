import { Tag } from "lucide-react";
import { useStrings } from "../i18n";
import { Money } from "./Money";

/**
 * Sharp-edged Valio campaign / AI negotiation savings tag (DESIGN_VALIO badge grammar: 0px corners).
 * Renders nothing unless a positive negotiated saving is reported by the API.
 */
export function NegotiatedSavingsBadge({
  savingsEur,
  className = "",
}: {
  savingsEur?: number | null;
  className?: string;
}) {
  const strings = useStrings();
  if (savingsEur == null || savingsEur <= 0) return null;
  return (
    <span
      data-testid="negotiated-savings-badge"
      className={`inline-flex items-center gap-1 rounded-none bg-lime px-2 py-0.5 text-[11px] font-bold uppercase tracking-[0.04em] text-navy ${className}`}
    >
      <Tag className="h-3 w-3 shrink-0" strokeWidth={2} aria-hidden="true" />
      <span>{strings.quote.negotiatedSavings}</span>
      <span className="tabular-nums">
        -<Money value={savingsEur} />
      </span>
    </span>
  );
}
