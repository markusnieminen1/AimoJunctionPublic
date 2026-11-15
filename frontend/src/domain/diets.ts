// Finnish menu codes / diet chips (display + filter params only). See PROGRESS.md "Labels → diet chips".
import type { DietCode } from "../api/types";

export const DIET_CODES: DietCode[] = ["L", "VL", "M", "G", "VEG", "HEART"];

export const DIETS: Record<DietCode, { chip: string; en: string; fi: string }> = {
  L: { chip: "L", en: "Lactose-free", fi: "Laktoositon" },
  VL: { chip: "VL", en: "Low-lactose", fi: "Vähälaktoosinen" },
  M: { chip: "M", en: "Milk-free", fi: "Maidoton" },
  G: { chip: "G", en: "Gluten-free", fi: "Gluteeniton" },
  VEG: { chip: "VEG", en: "Vegan", fi: "Vegaani" },
  HEART: { chip: "♥", en: "Heart Symbol", fi: "Sydänmerkki" },
};

const HEART_RE = /HEART|SYDAN|SYDÄN/i;

/** Diet codes derived from a product's labels/non_allergens (display only; the backend filters with `diet=`). */
export function productDiets(p: {
  labels: string[] | null | undefined;
  non_allergens: string[] | null | undefined;
  allergens?: string[] | null | undefined;
}): DietCode[] {
  const labels = p.labels ?? [];
  const free = p.non_allergens ?? [];
  // A free-from chip is never shown when the same product declares (or may contain) that allergen: conflicting data
  // must not look safe. Codes: ML lactose, AM milk, AW cereals containing gluten ("AM:MAY_CONTAIN" counts too).
  const declared = new Set((p.allergens ?? []).map((a) => a.split(":")[0]!.trim()));
  const out: DietCode[] = [];
  if (labels.includes("FREE_FROM_LACTOSE") && !declared.has("ML")) out.push("L");
  if (labels.includes("LOW_LACTOSE")) out.push("VL");
  if ((free.includes("AM") || labels.includes("FREE_FROM_MILK")) && !declared.has("AM")) out.push("M");
  if (labels.includes("FREE_FROM_GLUTEN") && !declared.has("AW")) out.push("G");
  if (labels.includes("VEGAN")) out.push("VEG");
  if (labels.some((l) => HEART_RE.test(l))) out.push("HEART");
  return out;
}

/** Parse a comma list ("L,G", "heart") into known DietCode values, in canonical order (display / cart state only). */
export function parseDietList(raw: string | null | undefined): DietCode[] {
  if (!raw) return [];
  const toks = raw.split(",").map((t) => t.trim().toUpperCase()).map((t) => (t === "HEART_SYMBOL" || t === "HEART_LOW_SALT" || t === "♥" ? "HEART" : t));
  return DIET_CODES.filter((c) => toks.includes(c));
}

/** Canonical comma list for the API's `diet` param, or undefined when no diet is required. */
export function dietParam(codes: DietCode[]): string | undefined {
  const list = DIET_CODES.filter((c) => codes.includes(c));
  return list.length ? list.join(",") : undefined;
}
