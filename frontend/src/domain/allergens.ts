// Allergen code → label map (display only). Source: docs/research_notes/.../valio_rules.py ALLERGEN.
// Mocks encode "may contain" as "CODE:MAY_CONTAIN"; plain "AM" means contains. Unknown suffixes never crash.

export const ALLERGEN_LABELS: Record<string, string> = {
  AW: "Cereals containing gluten",
  UW: "Wheat",
  NR: "Rye",
  GB: "Barley",
  GO: "Oats",
  GS: "Spelt",
  GK: "Khorasan",
  TR: "Triticale",
  AC: "Crustaceans",
  AE: "Eggs",
  AF: "Fish",
  ABD: "Tuna",
  ADQ: "Pollock",
  AP: "Peanuts",
  AY: "Soy",
  AM: "Milk",
  ML: "Lactose",
  GOM: "Goat milk",
  AN: "Tree nuts",
  SA: "Almond",
  SH: "Hazelnut",
  SW: "Walnut",
  SC: "Cashew",
  SP: "Pecan",
  SR: "Brazil nut",
  ST: "Pistachio",
  SM: "Macadamia",
  BC: "Celery",
  BM: "Mustard",
  AS: "Sesame",
  AU: "Sulphites",
  NL: "Lupin",
  UM: "Molluscs",
  UN: "Shellfish",
  NC: "Cocoa",
  NE: "Peas",
  NP: "Legumes",
  NK: "Coriander",
  NM: "Corn",
  NW: "Carrot",
  PN: "Pine nut",
  SO: "Coconut",
  SN: "Chestnut",
  BF: "Beef",
  PO: "Pork",
  CM: "Chicken",
  BP: "Linalool",
  X99: "No declarable allergens",
};

/** Label for a code; unknown codes show the raw code. */
export function allergenLabel(code: string): string {
  return ALLERGEN_LABELS[code] ?? code;
}

export type ParsedAllergen = { code: string; label: string; mayContain: boolean; raw: string };

/** Parse "AM" / "AM:MAY_CONTAIN" / "AM:<anything>" (unknown suffix → treated as contains, the safer reading). */
export function parseAllergen(raw: string): ParsedAllergen {
  const [code, suffix] = raw.split(":", 2);
  const c = (code ?? raw).trim();
  const mayContain = (suffix ?? "").toUpperCase().replace(/[^A-Z]/g, "") === "MAYCONTAIN";
  return { code: c, label: allergenLabel(c), mayContain, raw };
}

export type AllergenBuckets = {
  contains: ParsedAllergen[];
  mayContain: ParsedAllergen[];
  freeFrom: ParsedAllergen[];
  /** true when both allergens and non_allergens are empty: no supplier data, never "safe". */
  unknown: boolean;
};

/** Split a product's allergen fields into the 4 display buckets (DESIGN.md §6C). */
export function allergenBuckets(p: { allergens: string[] | null | undefined; non_allergens: string[] | null | undefined }): AllergenBuckets {
  const parsed = (p.allergens ?? []).map(parseAllergen);
  // Conflicting supplier data (a code both declared and "free from") must never render as safe: the declaration wins.
  const declared = new Set(parsed.map((a) => a.code));
  const free = (p.non_allergens ?? []).map((c) => parseAllergen(c.split(":")[0] ?? c)).filter((a) => !declared.has(a.code));
  return {
    contains: parsed.filter((a) => !a.mayContain),
    mayContain: parsed.filter((a) => a.mayContain),
    freeFrom: free,
    unknown: parsed.length === 0 && free.length === 0,
  };
}

/** `allergen_free` query values accepted by GET /api/products (comma list). */
export const ALLERGEN_FREE_OPTIONS: { value: string; label: string; codes: string[] }[] = [
  { value: "lactose", label: "Lactose", codes: ["ML"] },
  { value: "milk", label: "Milk", codes: ["AM"] },
  { value: "gluten", label: "Gluten", codes: ["AW"] },
  { value: "egg", label: "Egg", codes: ["AE"] },
  { value: "fish", label: "Fish", codes: ["AF"] },
  { value: "soy", label: "Soy", codes: ["AY"] },
  { value: "nuts", label: "Tree nuts", codes: ["AN"] },
  { value: "peanut", label: "Peanuts", codes: ["AP"] },
];
