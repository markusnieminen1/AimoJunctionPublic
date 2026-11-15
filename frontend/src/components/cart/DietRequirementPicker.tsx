// Cart-wide diet requirement (Finnish menu codes). Sent as `diet` with POST /api/quotes; the server checks every
// line against it and blocks unsafe lines with R-HARD-DIET. The UI never decides diet safety itself.
import { Salad } from "lucide-react";
import type { DietCode } from "../../api/types";
import { DIETS, DIET_CODES, parseDietList } from "../../domain/diets";
import { useI18n } from "../../i18n";
import { FilterPill } from "../Pills";

export function DietRequirementPicker({ value, onChange }: { value: DietCode[]; onChange: (codes: DietCode[]) => void }) {
  const { locale, strings } = useI18n();
  const c = strings.cart;
  const dietLabel = (d: DietCode) => (locale === "fi" ? DIETS[d].fi : DIETS[d].en);
  const toggle = (d: DietCode) => onChange(value.includes(d) ? value.filter((x) => x !== d) : [...value, d]);
  return (
    <fieldset className="min-w-0">
      <legend className="mb-2 flex items-center gap-1.5 text-[14px] font-semibold text-ink">
        {c.dietTitle} <span className="font-normal text-ink-muted">({c.optional})</span>
      </legend>
      <div className="flex flex-wrap gap-2" role="group" aria-label={c.dietTitle}>
        {DIET_CODES.map((d) => (
          <FilterPill
            key={d}
            active={value.includes(d)}
            title={`${DIETS[d].en} (${DIETS[d].fi})`}
            onClick={() => toggle(d)}
          >
            {DIETS[d].chip} <span className="font-normal opacity-85">· {dietLabel(d)}</span>
          </FilterPill>
        ))}
      </div>
      <p className="mt-2 text-xs leading-4 text-ink-muted">{value.length ? c.dietActive(value.map(dietLabel).join(" + ")) : c.dietHint}</p>
    </fieldset>
  );
}

/** Read-only "Diet requirement: M · Milk-free" row (quote panel, order detail). Renders nothing without a diet. */
export function DietRequirementBadge({ diet, className = "" }: { diet: string | null | undefined; className?: string }) {
  const codes = parseDietList(diet);
  if (codes.length === 0) return null;
  return (
    <span className={`inline-flex flex-wrap items-center gap-1 ${className}`} data-testid="diet-requirement">
      <Salad className="h-3.5 w-3.5 text-navy" strokeWidth={1.75} aria-hidden />
      {codes.map((c) => (
        <span key={c} title={`${DIETS[c].en} (${DIETS[c].fi})`} className="inline-flex items-center gap-1 rounded border border-[#DEE4E6] bg-primary-soft px-1.5 py-0.5 text-[11px] font-semibold text-navy">
          <span className="font-bold">{DIETS[c].chip}</span> {DIETS[c].en}
        </span>
      ))}
    </span>
  );
}
