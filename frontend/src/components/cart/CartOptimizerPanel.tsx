// P2 Pre-Flight Cart Optimizer panel on /cart: objective pills, default-OFF plant-based swap toggle,
// Before -> After deltas (EUR, reliability, comparable CO2e + co2_basis), and opt-in line swap checkboxes.
import { useEffect, useState } from "react";
import { ArrowRight, Sparkles } from "lucide-react";
import { isEndpointMissing, toApiError } from "../../api/client";
import { useOptimizeQuote } from "../../api/hooks";
import type {
  LineOptimizationSwap,
  OptimizeObjective,
  QuoteOptimizeResponse,
} from "../../api/types";
import type { CartLine } from "../../cart/CartContext";
import { useStrings } from "../../i18n";
import { formatEur } from "../../lib/format";
import { Badge, type BadgeTone } from "../Badge";
import { Button } from "../Button";
import { Card } from "../Card";
import { CO2Value } from "../CO2Value";
import { ErrorPanel } from "../ErrorPanel";

const OBJECTIVES: OptimizeObjective[] = ["BALANCED", "SAVE_MONEY", "MAX_RELIABILITY", "LOW_CARBON"];

const POLICY_TONE: Record<string, BadgeTone> = {
  AUTO: "success",
  APPROVAL_REQUIRED: "warning",
  BLOCKED: "danger",
};

interface CartOptimizerPanelProps {
  lines: CartLine[];
  requestedDeliveryDate: string;
  diet: string | null;
  signature: string;
  onApplySwaps: (swaps: LineOptimizationSwap[]) => void;
}

export function CartOptimizerPanel({
  lines,
  requestedDeliveryDate,
  diet,
  signature,
  onApplySwaps,
}: CartOptimizerPanelProps) {
  const t = useStrings().cart;
  const opt = useOptimizeQuote({ silent: true });
  const [objective, setObjective] = useState<OptimizeObjective>("BALANCED");
  const [allowPlantBased, setAllowPlantBased] = useState(false);
  const [result, setResult] = useState<QuoteOptimizeResponse | null>(null);
  const [resultSig, setResultSig] = useState<string>("");
  const [selected, setSelected] = useState<Record<number, boolean>>({});

  // Drop stale preview when cart contents or date/diet change.
  useEffect(() => {
    if (result && resultSig !== signature) {
      setResult(null);
      setSelected({});
    }
  }, [signature, result, resultSig]);

  if (lines.length === 0) return null;

  async function runOptimize(nextObj = objective, nextPlant = allowPlantBased) {
    try {
      const res = await opt.mutateAsync({
        requested_delivery_date: requestedDeliveryDate,
        lines: lines.map((l) => ({ product_id: l.product_id, quantity: l.quantity })),
        ...(diet ? { diet } : {}),
        objective: nextObj,
        allow_plant_based_swaps: nextPlant,
      });
      setResult(res);
      setResultSig(signature);
      const initSel: Record<number, boolean> = {};
      for (const sw of res.swaps) initSel[sw.line_index] = true;
      setSelected(initSel);
    } catch {
      // Error surfaced cleanly via opt.error
    }
  }

  function toggleLine(idx: number) {
    setSelected((prev) => ({ ...prev, [idx]: !prev[idx] }));
  }

  const chosenSwaps = result ? result.swaps.filter((s) => selected[s.line_index]) : [];

  return (
    <Card className="space-y-4" data-testid="cart-optimizer-panel">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="space-y-1">
          <h2 className="inline-flex items-center gap-1.5 text-[15px] font-bold text-navy">
            <Sparkles className="h-4 w-4" strokeWidth={1.75} aria-hidden />
            {t.optTitle}
          </h2>
          <p className="text-xs text-ink-secondary">{t.optLead}</p>
        </div>

        <Button
          type="button"
          variant="secondary"
          size="sm"
          disabled={opt.isPending}
          data-testid="optimize-cart-btn"
          onClick={() => void runOptimize()}
        >
          <Sparkles className="h-3.5 w-3.5" aria-hidden />
          {opt.isPending ? t.optRunning : t.optRun}
        </Button>
      </div>

      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap gap-1.5" role="group" aria-label={t.optTitle}>
          {OBJECTIVES.map((id) => {
            const on = objective === id;
            return (
              <button
                key={id}
                type="button"
                aria-pressed={on}
                onClick={() => {
                  setObjective(id);
                  if (result) void runOptimize(id, allowPlantBased);
                }}
                className={
                  on
                    ? "rounded-full bg-navy px-3 py-1 text-xs font-semibold text-white transition"
                    : "rounded-full border border-line bg-white px-3 py-1 text-xs font-semibold text-ink hover:border-navy hover:text-navy transition"
                }
              >
                {t.optObjectives[id] ?? id}
              </button>
            );
          })}
        </div>

        <label className="inline-flex cursor-pointer items-center gap-2 text-xs text-ink">
          <input
            type="checkbox"
            checked={allowPlantBased}
            onChange={(e) => {
              const checked = e.target.checked;
              setAllowPlantBased(checked);
              if (result) void runOptimize(objective, checked);
            }}
            data-testid="optimize-allow-plant-swaps"
            className="h-4 w-4 rounded border-border text-navy focus:ring-navy"
          />
          <span className="font-medium">{t.optAllowPlant}</span>
        </label>
      </div>
      <p className="-mt-2 text-[11.5px] text-ink-muted">{t.optAllowPlantHint}</p>

      {opt.isError && (
        isEndpointMissing(opt.error) || toApiError(opt.error).status === 404 ? (
          <p className="text-xs text-ink-muted">{t.optUnavailable}</p>
        ) : (
          <ErrorPanel error={opt.error} onRetry={() => void runOptimize()} />
        )
      )}

      {result && (
        <div className="space-y-3 border-t border-border pt-3" data-testid="optimize-results">
          <div className="grid grid-cols-1 gap-2 rounded border border-line bg-canvas p-3 text-xs sm:grid-cols-2 lg:grid-cols-4">
            <div>
              <div className="text-[11px] font-semibold uppercase tracking-wide text-ink-muted">{t.optTotalDelta}</div>
              <div className="mt-0.5 flex items-center gap-1 font-semibold tabular-nums text-ink" data-testid="optimize-eur-delta">
                <span>{formatEur(result.before_summary.total_excl_vat_eur)}</span>
                <ArrowRight className="h-3 w-3 text-navy" aria-hidden />
                <span>{formatEur(result.after_summary.total_excl_vat_eur)}</span>
                <span className="text-navy">
                  ({result.deltas.total_excl_vat_delta_eur > 0 ? "+" : ""}
                  {formatEur(result.deltas.total_excl_vat_delta_eur)})
                </span>
              </div>
            </div>

            <div>
              <div className="text-[11px] font-semibold uppercase tracking-wide text-ink-muted">{t.optReliabilityDelta}</div>
              <div className="mt-0.5 flex items-center gap-1 font-semibold tabular-nums text-ink">
                <span>{result.before_summary.overall_delivery_status}</span>
                <ArrowRight className="h-3 w-3 text-navy" aria-hidden />
                <span>{result.after_summary.overall_delivery_status}</span>
                {result.deltas.min_reliability_delta != null && (
                  <span className="text-navy">
                    ({result.deltas.min_reliability_delta >= 0 ? "+" : ""}
                    {Math.round(result.deltas.min_reliability_delta * 100)}%)
                  </span>
                )}
              </div>
            </div>

            <div>
              <div className="text-[11px] font-semibold uppercase tracking-wide text-ink-muted">{t.optCo2Delta}</div>
              <div className="mt-0.5 font-semibold tabular-nums text-ink" data-testid="optimize-co2-delta">
                {result.deltas.comparable_co2_delta_kg != null ? (
                  <>
                    <span>
                      {result.deltas.comparable_co2_delta_kg > 0 ? "+" : ""}
                      {result.deltas.comparable_co2_delta_kg.toFixed(1)} kg
                    </span>{" "}
                    <span className="font-normal text-ink-muted">
                      ({t.optCo2Coverage(result.deltas.comparable_co2_lines, result.before_summary.total_lines)})
                    </span>
                  </>
                ) : (
                  <span className="font-normal text-ink-muted">{t.optCo2None}</span>
                )}
              </div>
            </div>

            <div>
              <div className="text-[11px] font-semibold uppercase tracking-wide text-ink-muted">{t.optPolicy}</div>
              <div className="mt-0.5 flex flex-wrap items-center gap-1">
                <Badge tone={POLICY_TONE[result.before_summary.policy_preview.outcome] ?? "neutral"}>
                  {result.before_summary.policy_preview.outcome}
                </Badge>
                <ArrowRight className="h-3 w-3 text-navy" aria-hidden />
                <Badge tone={POLICY_TONE[result.after_summary.policy_preview.outcome] ?? "neutral"}>
                  {result.after_summary.policy_preview.outcome}
                </Badge>
              </div>
            </div>
          </div>

          {result.swaps.length === 0 ? (
            <p className="text-xs text-ink-muted" data-testid="optimize-no-swaps">{t.optNoSwaps}</p>
          ) : (
            <div className="space-y-2.5">
              <ul className="space-y-2">
                {result.swaps.map((sw) => {
                  const checked = Boolean(selected[sw.line_index]);
                  return (
                    <li
                      key={`${sw.line_index}-${sw.recommended_product.id}`}
                      className="rounded border border-line bg-white p-3 text-xs"
                      data-testid={`optimize-swap-${sw.original_line.product_id}`}
                    >
                      <label className="flex cursor-pointer items-start gap-2.5">
                        <input
                          type="checkbox"
                          checked={checked}
                          onChange={() => toggleLine(sw.line_index)}
                          className="mt-0.5 h-4 w-4 rounded border-border text-navy focus:ring-navy"
                        />
                        <div className="min-w-0 flex-1 space-y-1">
                          <div className="flex flex-wrap items-center justify-between gap-2">
                            <span className="font-semibold text-ink">
                              <span className="line-through text-ink-muted">
                                {sw.original_line.quantity} × {sw.original_line.product_id}
                              </span>{" "}
                              <ArrowRight className="inline h-3 w-3 text-navy" aria-hidden />{" "}
                              <span className="text-navy">
                                {sw.suggested_quantity} × {sw.recommended_product.id} · {sw.recommended_product.name}
                              </span>
                            </span>
                            <span className="font-semibold tabular-nums text-navy">
                              {sw.line_total_delta_eur > 0 ? "+" : ""}
                              {formatEur(sw.line_total_delta_eur)}
                            </span>
                          </div>
                          <p className="text-ink-secondary">{sw.reason}</p>
                          <div className="flex flex-wrap items-center gap-1.5 pt-0.5">
                            {sw.tradeoffs.map((tr) => (
                              <span key={tr} className="rounded-sm bg-canvas px-1.5 py-0.5 text-[11px] text-ink-muted">
                                {tr}
                              </span>
                            ))}
                            <CO2Value kg={sw.recommended_line.co2_kg} basis={sw.recommended_line.co2_basis ?? sw.co2_basis_note} />
                          </div>
                        </div>
                      </label>
                    </li>
                  );
                })}
              </ul>

              <div className="flex justify-end">
                <Button
                  type="button"
                  variant="primary"
                  size="sm"
                  disabled={chosenSwaps.length === 0}
                  data-testid="optimize-apply-btn"
                  onClick={() => onApplySwaps(chosenSwaps)}
                >
                  {t.optApply(chosenSwaps.length)}
                </Button>
              </div>
            </div>
          )}
        </div>
      )}
    </Card>
  );
}
