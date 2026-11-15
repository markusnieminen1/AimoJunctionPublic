// MSW mock builder for POST /api/quotes/optimize (P2 Pre-Flight Cart Optimizer).
import type {
  DeliveryStatus,
  DietCode,
  LineOptimizationSwap,
  OptimizeObjective,
  OptimizeSummary,
  Principal,
  Product,
  QuoteLine,
  QuoteOptimizeResponse,
} from "../api/types";
import { productDiets } from "../domain/diets";
import { temperatureInfo } from "../domain/temperature";
import { PRODUCTS } from "./data";
import { MockHttpError, audit, buildLine, evaluatePolicy, parseDietField, resolveLines, validateDate } from "./db";

const r2 = (n: number) => Math.round(n * 100) / 100;
const r1 = (n: number) => Math.round(n * 10) / 10;

const STATUS_RANK: Record<DeliveryStatus, number> = {
  ON_TIME: 0,
  UNKNOWN: 1,
  AT_RISK: 2,
  LATE: 3,
  NOT_DELIVERABLE: 4,
};

const OBJECTIVES: OptimizeObjective[] = ["BALANCED", "SAVE_MONEY", "MAX_RELIABILITY", "LOW_CARBON"];

function perSalesUnit(p: Product): number {
  return p.units.find((u) => u.unit_id === p.sales_unit)?.size_in_base_units ?? 1;
}

function isPlantBasedCategory(p: Product): boolean {
  return p.category === "10413" || (p.labels.includes("VEGAN") && p.category.startsWith("104"));
}

function summarize(
  principal: Principal,
  lines: QuoteLine[],
  now: number,
  diet: DietCode[],
): OptimizeSummary {
  const excl = r2(lines.reduce((s, l) => s + (l.line_total_eur ?? 0), 0));
  const probs = lines.map((l) => l.on_time_probability).filter((x): x is number => x != null);
  const minProb = probs.length ? Math.min(...probs) : null;
  const co2Lines = lines.filter((l) => l.co2_kg != null);
  const estCo2 = co2Lines.length ? r1(co2Lines.reduce((s, l) => s + (l.co2_kg ?? 0), 0)) : null;
  const overall = lines.reduce<DeliveryStatus>(
    (worst, l) => (STATUS_RANK[l.delivery_status] > STATUS_RANK[worst] ? l.delivery_status : worst),
    "ON_TIME",
  );
  return {
    total_excl_vat_eur: excl,
    min_on_time_probability: minProb,
    estimated_co2_kg: estCo2,
    co2_covered_lines: co2Lines.length,
    total_lines: lines.length,
    overall_delivery_status: overall,
    policy_preview: evaluatePolicy(principal, lines, excl, now, undefined, diet),
  };
}

function scoreCandidate(
  objective: OptimizeObjective,
  orig: QuoteLine,
  cand: QuoteLine,
): number {
  const eurSave = (orig.line_total_eur ?? 0) - (cand.line_total_eur ?? 0);
  const relGain = (cand.on_time_probability ?? 0) - (orig.on_time_probability ?? 0);
  const statusGain = STATUS_RANK[orig.delivery_status] - STATUS_RANK[cand.delivery_status];
  const co2Save =
    orig.co2_kg != null && cand.co2_kg != null ? orig.co2_kg - cand.co2_kg : 0;

  if (objective === "SAVE_MONEY") {
    return eurSave > 0.005 ? eurSave + statusGain * 5 : 0;
  }
  if (objective === "MAX_RELIABILITY") {
    if (statusGain > 0) return statusGain * 100 + relGain * 50 + Math.max(0, eurSave);
    return relGain > 0.005 ? relGain * 100 : 0;
  }
  if (objective === "LOW_CARBON") {
    return co2Save > 0.05 ? co2Save * 10 + Math.max(0, statusGain * 20) : 0;
  }
  // BALANCED
  const normCost = (orig.line_total_eur ?? 1) > 0 ? Math.max(-0.5, eurSave / Math.max(1, orig.line_total_eur ?? 1)) : 0;
  const normRel = relGain + statusGain * 0.4;
  const normCo2 = (orig.co2_kg ?? 0) > 0 ? co2Save / Math.max(1, orig.co2_kg ?? 1) : 0;
  const score = 0.4 * normCost + 0.35 * normRel + 0.25 * normCo2;
  return score > 0.02 ? score : 0;
}

export function buildQuoteOptimizeResponse(
  principal: Principal,
  rawBody: Record<string, unknown>,
  requestId: string,
  now: number,
): QuoteOptimizeResponse {
  const date = validateDate(rawBody.requested_delivery_date);
  const objective: OptimizeObjective = OBJECTIVES.includes(rawBody.objective as OptimizeObjective)
    ? (rawBody.objective as OptimizeObjective)
    : "BALANCED";
  const allowPlant = rawBody.allow_plant_based_swaps === true;
  const reqDiet = parseDietField(rawBody.diet);
  const resolved = resolveLines(rawBody.lines as { product_id: string; quantity: number }[]);
  if (resolved.length > 25) {
    throw new MockHttpError(422, "VALIDATION_ERROR", "lines must contain 1..25 items for optimization", { field: "lines" });
  }

  const checkedIds = new Set<string>(resolved.map((r) => r.product.id));
  const origLines = resolved.map((r) => buildLine(r.product, r.quantity, date, now));
  const afterLines = [...origLines];
  const swaps: LineOptimizationSwap[] = [];
  const unchanged_lines: number[] = [];

  for (let idx = 0; idx < resolved.length; idx++) {
    const { product: origProd, quantity: origQty } = resolved[idx]!;
    const origLine = origLines[idx]!;

    // Infer original dietary assurances (L/M/G/VEG) + any explicit request diet.
    const inferred = productDiets(origProd).filter((d): d is DietCode => d === "L" || d === "M" || d === "G" || d === "VEG");
    const requiredDiets = [...new Set<DietCode>([...reqDiet, ...inferred])];

    let best: {
      prod: Product;
      line: QuoteLine;
      qty: number;
      score: number;
    } | null = null;

    for (const cand of PRODUCTS) {
      if (cand.id === origProd.id) continue;
      if (checkedIds.size >= 50 && !checkedIds.has(cand.id)) continue;

      const sameCategory = cand.category === origProd.category;
      const crossPlant =
        allowPlant &&
        (objective === "LOW_CARBON" || objective === "BALANCED") &&
        origProd.category.startsWith("104") &&
        isPlantBasedCategory(cand);

      if (!sameCategory && !crossPlant) continue;
      if (!allowPlant && !origProd.category.startsWith("10413") && isPlantBasedCategory(cand)) continue;

      // Cross-group plant swaps allow relaxing dairy-vs-plant while STILL enforcing explicit diet & G allergen safety.
      const effectiveReq = crossPlant ? reqDiet : requiredDiets;
      if (!effectiveReq.every((d) => productDiets(cand).includes(d))) continue;

      checkedIds.add(cand.id);
      const candQty = Math.max(1, Math.ceil((origQty * perSalesUnit(origProd)) / perSalesUnit(cand)));
      const candLine = buildLine(cand, candQty, date, now);

      if (candLine.unit_price_eur == null || candLine.delivery_status === "NOT_DELIVERABLE") continue;
      if (STATUS_RANK[candLine.delivery_status] > STATUS_RANK[origLine.delivery_status]) continue;
      if (objective === "SAVE_MONEY" && candLine.delivery_status !== "ON_TIME") continue;
      if (objective === "LOW_CARBON" && candLine.delivery_status !== "ON_TIME" && origLine.delivery_status === "ON_TIME") continue;

      const score = scoreCandidate(objective, origLine, candLine);
      if (score > 0 && (!best || score > best.score)) {
        best = { prod: cand, line: candLine, qty: candQty, score };
      }
    }

    if (!best) {
      unchanged_lines.push(idx);
      continue;
    }

    afterLines[idx] = best.line;
    const tradeoffs: string[] = [];
    const tOrig = temperatureInfo(origProd.temperature_condition);
    const tCand = temperatureInfo(best.prod.temperature_condition);
    tradeoffs.push(
      tOrig.zone === tCand.zone
        ? `Storage: ${tCand.label}`
        : `Storage: ${tCand.label} (was ${tOrig.label})`,
    );
    if (best.prod.sales_unit !== origProd.sales_unit || perSalesUnit(best.prod) !== perSalesUnit(origProd)) {
      tradeoffs.push(`Pack equivalence: ${best.qty} ${best.prod.sales_unit} replaces ${origQty} ${origProd.sales_unit}`);
    }
    if (origLine.on_time_probability != null && best.line.on_time_probability != null) {
      tradeoffs.push(`Reliability ${(best.line.on_time_probability * 100).toFixed(0)}% vs ${(origLine.on_time_probability * 100).toFixed(0)}%`);
    }
    if (best.prod.co2_kg_per_kg != null && origProd.co2_kg_per_kg != null && best.prod.co2_kg_per_kg === origProd.co2_kg_per_kg) {
      tradeoffs.push("CO2e: identical product-group average (0.0 kg delta)");
    }

    const eurDelta = r2((best.line.line_total_eur ?? 0) - (origLine.line_total_eur ?? 0));
    const relDelta =
      best.line.on_time_probability != null && origLine.on_time_probability != null
        ? r2(best.line.on_time_probability - origLine.on_time_probability)
        : null;
    const co2Delta =
      best.line.co2_kg != null && origLine.co2_kg != null
        ? r1(best.line.co2_kg - origLine.co2_kg)
        : null;

    const reasons: string[] = [];
    if (STATUS_RANK[best.line.delivery_status] < STATUS_RANK[origLine.delivery_status]) {
      reasons.push(`Upgrades delivery from ${origLine.delivery_status} to ${best.line.delivery_status}`);
    }
    if (eurDelta < 0) reasons.push(`Saves ${Math.abs(eurDelta).toFixed(2)} EUR excl. VAT`);
    if (co2Delta != null && co2Delta < 0) reasons.push(`Reduces CO2e by ${Math.abs(co2Delta).toFixed(1)} kg`);

    swaps.push({
      line_index: idx,
      original_line: origLine,
      recommended_product: best.prod,
      recommended_line: best.line,
      suggested_quantity: best.qty,
      line_total_delta_eur: eurDelta,
      reliability_delta: relDelta,
      co2_delta_kg: co2Delta,
      co2_basis_note: best.line.co2_basis ?? origLine.co2_basis ?? null,
      tradeoffs,
      reason: reasons.join(" · ") || `Better match for ${objective}`,
    });
  }

  const before_summary = summarize(principal, origLines, now, reqDiet);
  const after_summary = summarize(principal, afterLines, now, reqDiet);

  let compCo2Count = 0;
  let compCo2Delta = 0;
  for (let i = 0; i < origLines.length; i++) {
    const a = origLines[i]!.co2_kg;
    const b = afterLines[i]!.co2_kg;
    if (a != null && b != null) {
      compCo2Count += 1;
      compCo2Delta += b - a;
    }
  }

  audit(
    principal.company_id,
    principal,
    "quote.optimize",
    "quote",
    "preview",
    requestId,
    { objective, allow_plant_based_swaps: allowPlant, swaps_found: swaps.length, checked_ids: checkedIds.size },
    "ok",
    now,
  );

  return {
    objective,
    requested_delivery_date: date,
    diet: reqDiet.length ? reqDiet.join(",") : null,
    before_summary,
    after_summary,
    deltas: {
      total_excl_vat_delta_eur: r2(after_summary.total_excl_vat_eur - before_summary.total_excl_vat_eur),
      min_reliability_delta:
        after_summary.min_on_time_probability != null && before_summary.min_on_time_probability != null
          ? r2(after_summary.min_on_time_probability - before_summary.min_on_time_probability)
          : null,
      comparable_co2_delta_kg: compCo2Count > 0 ? r1(compCo2Delta) : null,
      comparable_co2_lines: compCo2Count,
    },
    swaps,
    unchanged_lines,
  };
}
