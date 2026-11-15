// Unit conversion helper for the "1,000 ST = 1,000 L"-style hint (display only, no prices).
import type { ProductUnit } from "../api/types";
import { formatNumber } from "../lib/format";

/** Base units per one sales unit, or null if the product does not list its sales unit. */
export function baseUnitsPerSalesUnit(p: { sales_unit: string; units: ProductUnit[] }): number | null {
  const u = p.units.find((x) => x.unit_id === p.sales_unit);
  return u ? u.size_in_base_units : null;
}

/** "1,000 ST = 1,000 ST" / "50 KI = 250 ST"; returns null when unknown. */
export function baseQuantityHint(p: { sales_unit: string; base_unit: string; units: ProductUnit[] }, qty: number): string | null {
  const per = baseUnitsPerSalesUnit(p);
  if (per == null) return null;
  return `${formatNumber(qty)} ${p.sales_unit} = ${formatNumber(qty * per)} ${p.base_unit}`;
}
