import type { DietCode } from "../api/types";
import { DIETS, productDiets } from "../domain/diets";

/** Compact 22 px square Finnish diet code badges (L, VL, M, G, VEG, ♥). Pass `codes` or a `product`. */
export function DietChips({ codes, product }: { codes?: DietCode[]; product?: { labels: string[]; non_allergens: string[]; allergens?: string[] } }) {
  const list = codes ?? (product ? productDiets(product) : []);
  if (list.length === 0) return null;
  return (
    <span className="inline-flex flex-wrap gap-1">
      {list.map((c) => (
        <span
          key={c}
          title={`${DIETS[c].en} (${DIETS[c].fi})`}
          className="inline-flex h-[22px] min-w-[22px] items-center justify-center rounded-sm border border-navy bg-white px-1 text-[11px] font-bold leading-none text-navy"
        >
          {DIETS[c].chip}
        </span>
      ))}
    </span>
  );
}
