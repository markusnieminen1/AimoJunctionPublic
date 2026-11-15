import { formatNumber } from "../../lib/format";
// Catalog filter sidebar content (also used inside the mobile drawer): categories, Finnish diet chips, free-from, temperature.
import { useMemo, useState } from "react";
import type { Category, DietCode } from "../../api/types";
import { ALLERGEN_FREE_OPTIONS } from "../../domain/allergens";
import { DIETS, DIET_CODES } from "../../domain/diets";
import type { TempZone } from "../../domain/temperature";
import { useStrings, type Strings } from "../../i18n";
import { Skeleton } from "../Skeleton";

/** Free-from options not already covered by a diet chip (L/M/G cover lactose, milk, gluten). */
export const FREE_FROM_FILTERS = ALLERGEN_FREE_OPTIONS.filter((o) => !["lactose", "milk", "gluten"].includes(o.value));

export type TempFilter = Exclude<TempZone, "unknown">;
export function getTempFilters(c: Strings["catalog"]): { value: TempFilter; label: string }[] {
  return [
    { value: "chilled", label: c.tempChilled },
    { value: "frozen", label: c.tempFrozen },
    { value: "ambient", label: c.tempAmbient },
  ];
}

export type CatalogFilterState = { category: string | null; diets: DietCode[]; free: string[]; temp: TempFilter | null };

// Portal filter pills (DESIGN_VALIO §4.6): petrol-light, bold 14 petrol; active = petrol with white text.
const chip = (active: boolean) =>
  `inline-flex h-9 items-center rounded-full px-4 text-[14px] font-bold transition-colors ${active ? "bg-navy text-white" : "bg-primary-soft text-navy hover:bg-[#E2EBEF]"}`;

function SectionLabel({ children }: { children: React.ReactNode }) {
  return <h3 className="label-display mb-3 text-[18px] leading-[19px] text-navy">{children}</h3>;
}

export function CatalogFilters({ state, categories, categoriesLoading, onChange }: {
  state: CatalogFilterState;
  categories: Category[] | undefined;
  categoriesLoading: boolean;
  onChange: (patch: Partial<CatalogFilterState>) => void;
}) {
  const c = useStrings().catalog;
  const tempFilters = getTempFilters(c);
  const [catFilter, setCatFilter] = useState("");
  const shownCats = useMemo(() => {
    const list = categories ?? [];
    const f = catFilter.trim().toLowerCase();
    return f ? list.filter((x) => x.name.toLowerCase().includes(f) || x.code.startsWith(f)) : list;
  }, [categories, catFilter]);

  const toggleDiet = (d: DietCode) => onChange({ diets: state.diets.includes(d) ? state.diets.filter((x) => x !== d) : [...state.diets, d] });
  const toggleFree = (v: string) => onChange({ free: state.free.includes(v) ? state.free.filter((x) => x !== v) : [...state.free, v] });

  return (
    <div className="space-y-7">
      <section aria-label={c.diets}>
        <SectionLabel>{c.diets}</SectionLabel>
        <div className="flex flex-wrap gap-2">
          {DIET_CODES.map((d) => (
            <button key={d} type="button" aria-pressed={state.diets.includes(d)} title={`${DIETS[d].en} (${DIETS[d].fi})`} className={chip(state.diets.includes(d))} onClick={() => toggleDiet(d)}>
              {DIETS[d].chip}
              <span className="sr-only"> {DIETS[d].en}</span>
            </button>
          ))}
        </div>
        <p className="mt-2 text-[12px] leading-4 text-ink-muted">{c.dietsHint}</p>
      </section>

      <section aria-label={c.freeFrom}>
        <SectionLabel>{c.freeFrom}</SectionLabel>
        <div className="flex flex-wrap gap-2">
          {FREE_FROM_FILTERS.map((o) => (
            <button key={o.value} type="button" aria-pressed={state.free.includes(o.value)} className={chip(state.free.includes(o.value))} onClick={() => toggleFree(o.value)}>
              {o.label}
            </button>
          ))}
        </div>
      </section>

      <section aria-label={c.temperature}>
        <SectionLabel>{c.temperature}</SectionLabel>
        <div className="flex flex-wrap gap-2">
          <button type="button" aria-pressed={state.temp == null} className={chip(state.temp == null)} onClick={() => onChange({ temp: null })}>
            {c.tempAll}
          </button>
          {tempFilters.map((t) => (
            <button key={t.value} type="button" aria-pressed={state.temp === t.value} className={chip(state.temp === t.value)} onClick={() => onChange({ temp: state.temp === t.value ? null : t.value })}>
              {t.label}
            </button>
          ))}
        </div>
        <p className="mt-2 text-[12px] leading-4 text-ink-muted">{c.temperatureHint}</p>
      </section>

      <section aria-label={c.categories}>
        <SectionLabel>{c.categories}</SectionLabel>
        {(categories?.length ?? 0) > 10 && (
          <input
            value={catFilter}
            onChange={(e) => setCatFilter(e.target.value)}
            placeholder="Filter categories…"
            aria-label="Filter categories"
            className="mb-2 h-10 w-full rounded border border-line px-3 text-[14px] placeholder:text-line-strong focus:border-navy focus:outline-none"
          />
        )}
        {categoriesLoading ? (
          <div className="space-y-1.5">
            {Array.from({ length: 6 }, (_, i) => (
              <Skeleton key={i} className="h-6 w-full" />
            ))}
          </div>
        ) : (
          <ul className="max-h-[360px] space-y-0.5 overflow-y-auto pr-1">
            <li>
              <button
                type="button"
                onClick={() => onChange({ category: null })}
                className={`flex w-full items-center justify-between border-l-4 py-1.5 pl-3 pr-1 text-left text-[14px] leading-[18px] text-ink hover:underline ${state.category == null ? "border-navy font-bold" : "border-transparent"}`}
              >
                {c.allCategories}
              </button>
            </li>
            {shownCats.map((c) => (
              <li key={c.code}>
                <button
                  type="button"
                  onClick={() => onChange({ category: state.category === c.code ? null : c.code })}
                  aria-pressed={state.category === c.code}
                  className={`flex w-full items-start justify-between gap-2 border-l-4 py-1.5 pl-3 pr-1 text-left text-[14px] leading-[18px] text-ink hover:underline ${
                    state.category === c.code ? "border-navy font-bold" : "border-transparent"
                  }`}
                >
                  <span className="min-w-0">{c.name}</span>
                  <span className="tabular-nums shrink-0 font-normal text-ink-muted">{formatNumber(c.product_count)}</span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
