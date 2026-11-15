import { Scale } from "lucide-react";
import type { RecipeSanityWarning } from "../../api/types";
import { useStrings } from "../../i18n";
import { formatEur, formatNumber } from "../../lib/format";
import { Badge } from "../Badge";
import { Stripe } from "../Stripe";

/** Shared R-RECIPE-SANITY over-order breakdown surfaced on both ApprovalCard and Assistant chat cards. */
export function RecipeSanityNotice({
  warning,
  className = "",
}: {
  warning: RecipeSanityWarning;
  className?: string;
}) {
  const strings = useStrings();
  const a = strings.assistant;
  if (!warning?.triggered) return null;

  return (
    <Stripe
      kind="warning"
      accent
      icon={<Scale className="h-5 w-5 text-red-strong" strokeWidth={1.75} aria-hidden="true" />}
      title={a.recipeSanityTitle(warning.rule_id || "R-RECIPE-SANITY", warning.recipe_name, warning.target_pax)}
      data-testid="recipe-sanity-warning"
      className={className}
    >
      <p className="text-[13px] text-ink">{warning.message}</p>
      {Array.isArray(warning.offending_lines) && warning.offending_lines.length > 0 && (
        <ul className="mt-2 space-y-1.5">
          {warning.offending_lines.map((line) => (
            <li
              key={line.product_id}
              className="flex flex-wrap items-center justify-between gap-2 rounded border border-warning/30 bg-white/80 px-2.5 py-1.5 text-[12px]"
            >
              <div className="min-w-0">
                <span className="font-bold text-ink">{line.product_name}</span>{" "}
                <span className="font-mono-code text-ink-muted">({line.product_id})</span>
              </div>
              <div className="tabular-nums flex flex-wrap items-center gap-2">
                <span>
                  {a.recipeSanityOrdered}:{" "}
                  <strong className="text-red-strong">
                    {formatNumber(line.ordered_qty)} {line.unit}
                  </strong>
                </span>
                <span className="text-ink-secondary">
                  {a.recipeSanityExpected}: {formatNumber(line.expected_qty)} {line.unit}
                </span>
                <Badge tone="warning">{a.recipeSanityRatio(line.excess_ratio)}</Badge>
                {line.excess_cost_eur > 0 && (
                  <span className="font-semibold text-red-strong">+{formatEur(line.excess_cost_eur)}</span>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}
    </Stripe>
  );
}
