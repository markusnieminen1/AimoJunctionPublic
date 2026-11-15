// 4-state allergen safety matrix (DESIGN.md §6C) drawn as portal notification stripes (DESIGN_VALIO §6 /products):
// Contains = error stripe, May contain = warning stripe, Free from = success stripe, and Unknown (no supplier data) =
// dashed amber border on cream with the verbatim "verify supplier specification" text. Unknown is never green/safe, and an
// empty bucket is neutral grey ("none listed" is not a safety claim).
import { AlertOctagon, AlertTriangle, CheckCircle2, HelpCircle } from "lucide-react";
import type { ReactNode } from "react";
import type { Product } from "../../api/types";
import { allergenBuckets, type ParsedAllergen } from "../../domain/allergens";
import { productDiets } from "../../domain/diets";
import { useStrings } from "../../i18n";
import { DietChips } from "../DietChips";

function Bucket({ title, items, tone, icon, testId }: { title: string; items: ParsedAllergen[]; tone: "success" | "danger" | "warning"; icon: ReactNode; testId: string }) {
  const strings = useStrings();
  const box = { success: "bg-success-bg border-l-success", danger: "bg-red-soft border-l-danger", warning: "bg-yellow-soft border-l-warning" }[tone];
  const iconTone = { success: "text-success", danger: "text-red-strong", warning: "text-warning" }[tone];
  const filled = items.length > 0;
  return (
    <div className={`rounded-[3px] border-l-4 px-4 py-3 ${filled ? box : "border-l-line-strong bg-primary-soft"}`} data-testid={testId}>
      <div className="flex items-center gap-2 text-[14px] font-bold text-ink">
        <span className={filled ? iconTone : "text-ink-muted"}>{icon}</span>
        {title}
        <span className="tabular-nums ml-auto font-normal text-ink-muted">{items.length}</span>
      </div>
      {filled ? (
        <ul className="mt-2 flex flex-wrap gap-1.5">
          {items.map((a) => (
            <li key={a.raw} className="rounded-sm bg-white/80 px-2 py-0.5 text-[13px] font-medium text-ink" title={a.raw}>
              {a.label}
              <span className="font-mono-code ml-1 text-[10px] text-ink-muted">{a.code}</span>
            </li>
          ))}
        </ul>
      ) : (
        <p className="mt-1 text-[13px] text-ink-muted">{strings.product.noneListed}</p>
      )}
    </div>
  );
}

export function AllergenMatrix({ product, className = "" }: { product: Pick<Product, "allergens" | "non_allergens" | "labels">; className?: string }) {
  const strings = useStrings();
  const b = allergenBuckets(product);
  const ic = "h-5 w-5";
  const diets = productDiets(product);
  return (
    <section className={className} aria-labelledby="allergen-title">
      <h2 id="allergen-title" className="h-section">
        {strings.product.allergenTitle}
      </h2>
      <p className="mt-2 text-[16px] leading-[21px] text-ink">{strings.product.allergenLead}</p>
      {diets.length > 0 && (
        <div className="mt-4 flex items-center gap-2 text-[14px] text-ink">
          <span className="font-bold">{strings.product.diets}</span>
          <DietChips codes={diets} />
        </div>
      )}
      {b.unknown ? (
        <div className="hatch-warning mt-5 rounded-[3px] border-2 border-dashed border-warning px-4 py-4 md:px-6" data-testid="allergen-unknown" role="status">
          <div className="flex items-start gap-3">
            <HelpCircle className="mt-0.5 h-6 w-6 shrink-0 text-warning" strokeWidth={1.75} />
            <div>
              <p className="text-[16px] font-bold text-warning">Unknown (no supplier data)</p>
              <p className="mt-1 text-[15px] leading-6 text-ink">{strings.allergens.unknown}</p>
            </div>
          </div>
        </div>
      ) : (
        <>
          <div className="mt-5 grid gap-3 md:grid-cols-3">
            <Bucket title={strings.allergens.contains} items={b.contains} tone="danger" icon={<AlertOctagon className={ic} strokeWidth={1.75} />} testId="allergen-contains" />
            <Bucket title={strings.allergens.mayContain} items={b.mayContain} tone="warning" icon={<AlertTriangle className={ic} strokeWidth={1.75} />} testId="allergen-may" />
            <Bucket title={strings.allergens.freeFrom} items={b.freeFrom} tone="success" icon={<CheckCircle2 className={ic} strokeWidth={1.75} />} testId="allergen-free" />
          </div>
          <p className="mt-3 flex items-start gap-2 text-[13px] leading-5 text-ink-muted">
            <HelpCircle className="mt-0.5 h-4 w-4 shrink-0 text-warning" strokeWidth={1.75} />
            {strings.product.allergenNotListed}
          </p>
        </>
      )}
    </section>
  );
}
