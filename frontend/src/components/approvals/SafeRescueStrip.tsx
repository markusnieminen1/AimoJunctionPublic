import { useMemo } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Sparkles, Wrench } from "lucide-react";
import { toApiError } from "../../api/client";
import { endpoints } from "../../api/endpoints";
import { invalidateOrderViews, useAgent } from "../../api/hooks";
import type { Order, SubstituteItem } from "../../api/types";
import { useAuth } from "../../auth/AuthContext";
import {
  buildRecipeRightSizeLines,
  evaluateRescueEligibility,
  executeSafeRescue,
  mergeDiets,
  pickVerifiedSubstitute,
  type RescueFixKind,
} from "../../domain/orderRescue";
import { useStrings } from "../../i18n";
import { formatEur } from "../../lib/format";
import { Button } from "../Button";
import { ErrorPanel } from "../ErrorPanel";
import { toast } from "../Toast";

/**
 * 1-Tap "Apply Safe Fix & Place" Kitchen Rescue strip on ApprovalCard (Path A Core-API flow).
 * Strictly limited to human principals (isUser) and curable Gate 2 holds with zero Gate 1 / UNIT_MISMATCH blocks.
 */
export function SafeRescueStrip({
  order,
  onRescued,
  onStale,
  className = "",
}: {
  order: Order;
  onRescued: (newOrder: Order, supersededOrder: Order) => void;
  onStale: () => void;
  className?: string;
}) {
  const { isUser } = useAuth();
  const qc = useQueryClient();
  const strings = useStrings();
  const t = strings.approvals;

  const eligibility = useMemo(() => evaluateRescueEligibility(order), [order]);
  const shouldFetchAgent = isUser && eligibility.eligible && order.created_by.kind === "agent";
  const agentQ = useAgent(shouldFetchAgent ? order.created_by.id : undefined, {
    enabled: shouldFetchAgent,
    silent: true,
  });

  const agentDiet =
    (agentQ.data as { hard_diet?: string | null; diet?: string | null } | undefined)?.hard_diet ??
    (agentQ.data as { diet?: string | null } | undefined)?.diet ??
    null;
  const effectiveDiet = useMemo(() => mergeDiets(order.diet, agentDiet), [order.diet, agentDiet]);

  const riskyKey = eligibility.riskyLines.map((l) => `${l.product_id}:${l.quantity}`).join("|");
  const subsQ = useQuery({
    queryKey: ["rescue-subs", order.id, order.content_hash, effectiveDiet ?? "", riskyKey],
    enabled: isUser && eligibility.hasDeliveryRiskFix && (!shouldFetchAgent || !agentQ.isLoading),
    staleTime: 30_000,
    meta: { silent: true },
    queryFn: async () => {
      const pairs = await Promise.all(
        eligibility.riskyLines.map(async (orig) => {
          const res = await endpoints.substitutes({
            product_id: orig.product_id,
            quantity: orig.quantity,
            requested_delivery_date: order.requested_delivery_date,
            diet: effectiveDiet,
          });
          return [orig.product_id, pickVerifiedSubstitute(orig, res.items)] as const;
        }),
      );
      return new Map<string, SubstituteItem | null>(pairs);
    },
  });

  const recipeLines = useMemo(
    () => (eligibility.hasRecipeFix ? buildRecipeRightSizeLines(order) : null),
    [eligibility.hasRecipeFix, order],
  );
  const recipeSavingsEur = useMemo(() => {
    const sum = (order.recipe_sanity_warning?.offending_lines ?? []).reduce(
      (acc, line) => acc + (line.excess_cost_eur > 0 ? line.excess_cost_eur : 0),
      0,
    );
    return sum > 0 ? Math.round(sum * 100) / 100 : null;
  }, [order.recipe_sanity_warning]);

  const swapCandidate = useMemo(() => {
    if (!eligibility.hasDeliveryRiskFix || !subsQ.data) return null;
    const picked = new Map<string, SubstituteItem>();
    for (const orig of eligibility.riskyLines) {
      const sub = subsQ.data.get(orig.product_id) ?? null;
      if (!sub) return null;
      picked.set(orig.product_id, sub);
    }
    const cleanLines = order.lines.map((l) => {
      const sub = picked.get(l.product_id);
      return sub ? { product_id: sub.product.id, quantity: sub.line.quantity } : { product_id: l.product_id, quantity: l.quantity };
    });

    let delta = 0;
    let deltaKnown = true;
    for (const orig of eligibility.riskyLines) {
      const sub = picked.get(orig.product_id)!;
      if (orig.line_total_eur == null || sub.line.line_total_eur == null) {
        deltaKnown = false;
        break;
      }
      delta += sub.line.line_total_eur - orig.line_total_eur;
    }

    const newTotalEur = deltaKnown ? Math.max(0, Math.round((order.total_excl_vat_eur + delta) * 100) / 100) : null;
    const savingsEur = deltaKnown && delta < -0.005 ? Math.round(-delta * 100) / 100 : null;
    const productIds = [...new Set(eligibility.riskyLines.map((l) => picked.get(l.product_id)!.product.id))].join(", ");
    const storageNotes = eligibility.riskyLines
      .map((l) => picked.get(l.product_id)?.trade_offs?.[0])
      .filter((n): n is string => Boolean(n && /chilled|uht|ambient|frozen|pakaste|huoneen/i.test(n)));

    return { cleanLines, newTotalEur, savingsEur, productIds, storageNote: storageNotes[0] ?? null };
  }, [eligibility.hasDeliveryRiskFix, eligibility.riskyLines, order.lines, order.total_excl_vat_eur, subsQ.data]);

  const rescueM = useMutation({
    mutationFn: ({
      fixKind,
      cleanLines,
      savingsEur,
    }: {
      fixKind: RescueFixKind;
      cleanLines: { product_id: string; quantity: number }[];
      savingsEur?: number | null;
    }) =>
      executeSafeRescue({
        order,
        fixKind,
        cleanLines,
        effectiveDiet,
        savingsEur,
      }),
    meta: { silent: true },
    onSuccess: ({ newOrder, supersededOrder }) => {
      invalidateOrderViews(qc, newOrder);
      invalidateOrderViews(qc, supersededOrder);
      toast.success(t.rescueSuccess(newOrder.id, order.id));
      onRescued(newOrder, supersededOrder);
    },
    onError: (err) => {
      const code = toApiError(err).code;
      if (code === "APPROVAL_CONTENT_CHANGED" || code === "INVALID_STATE") {
        onStale();
      }
    },
  });

  if (!isUser || !eligibility.eligible) return null;

  const isCheckingSubs = eligibility.hasDeliveryRiskFix && (subsQ.isLoading || (shouldFetchAgent && agentQ.isLoading));
  const hasAnyAction = Boolean(recipeLines) || Boolean(swapCandidate);
  if (!hasAnyAction && !isCheckingSubs) return null;

  return (
    <div
      className={`rounded border border-success/40 bg-success-bg/65 p-3 ${className}`}
      data-testid="safe-rescue-strip"
    >
      <div className="flex items-start gap-2">
        <Wrench className="mt-0.5 h-4 w-4 shrink-0 text-success" strokeWidth={2} aria-hidden="true" />
        <div className="min-w-0 flex-1 space-y-2">
          <div>
            <p className="text-[13px] font-bold text-ink">{t.rescueTitle}</p>
            <p className="text-[12px] text-ink-secondary">{t.rescueSubtitle}</p>
          </div>

          {rescueM.isError && <ErrorPanel error={rescueM.error} />}

          <div className="flex flex-wrap items-center gap-2">
            {recipeLines && (
              <Button
                variant="lime"
                size="sm"
                data-testid="rescue-recipe-btn"
                loading={rescueM.isPending && rescueM.variables?.fixKind === "ADJUST_TO_RECIPE_YIELD"}
                disabled={rescueM.isPending}
                icon={<Sparkles className="h-3.5 w-3.5 shrink-0" strokeWidth={2} />}
                onClick={() =>
                  rescueM.mutate({
                    fixKind: "ADJUST_TO_RECIPE_YIELD",
                    cleanLines: recipeLines,
                    savingsEur: recipeSavingsEur,
                  })
                }
              >
                {t.rescueRecipeBtn(recipeSavingsEur != null ? formatEur(recipeSavingsEur) : null)}
              </Button>
            )}

            {isCheckingSubs && !swapCandidate && (
              <span className="text-[12px] font-medium text-ink-secondary" data-testid="rescue-checking">
                {t.rescueChecking}
              </span>
            )}

            {swapCandidate && (
              <div className="flex flex-wrap items-center gap-2">
                <Button
                  variant="lime"
                  size="sm"
                  data-testid="rescue-swap-btn"
                  loading={rescueM.isPending && rescueM.variables?.fixKind === "SWAP_TO_VERIFIED_SUBSTITUTE"}
                  disabled={rescueM.isPending}
                  icon={<Sparkles className="h-3.5 w-3.5 shrink-0" strokeWidth={2} />}
                  onClick={() =>
                    rescueM.mutate({
                      fixKind: "SWAP_TO_VERIFIED_SUBSTITUTE",
                      cleanLines: swapCandidate.cleanLines,
                      savingsEur: swapCandidate.savingsEur,
                    })
                  }
                >
                  {t.rescueSwapBtn(
                    swapCandidate.productIds,
                    swapCandidate.newTotalEur != null ? formatEur(swapCandidate.newTotalEur) : "—",
                    swapCandidate.savingsEur != null ? formatEur(swapCandidate.savingsEur) : null,
                  )}
                </Button>
                {swapCandidate.storageNote && (
                  <span className="rounded bg-white/90 px-2 py-0.5 text-[11px] font-medium text-ink-secondary" data-testid="rescue-storage-note">
                    {t.rescueStorageNote} {swapCandidate.storageNote}
                  </span>
                )}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
