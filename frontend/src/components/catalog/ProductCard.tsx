// Portal product tile (grid) and order-line-style row (list), DESIGN_VALIO §4.5. Display only: no prices here; they are
// live per date (POST /api/products/check), so the tile offers "Check price" instead of inventing one.
import { Link } from "react-router-dom";
import { FileText, HelpCircle } from "lucide-react";
import type { Product } from "../../api/types";
import { allergenBuckets } from "../../domain/allergens";
import { useStrings } from "../../i18n";
import { DietChips } from "../DietChips";
import { ProductImage } from "../ProductImage";
import { TemperatureBadge } from "../TemperatureBadge";
import { AddToCartControl, Co2PerKg, ReliabilityDot, SkuChip, UnitLine } from "../product/ProductBits";

/** Amber dashed "Allergens unknown" marker for products without any supplier allergen data (never "safe"). */
export function UnknownAllergenMarker() {
  return (
    <span
      title="No supplier allergen data: verify before serving"
      className="inline-flex items-center gap-1 whitespace-nowrap rounded-sm border border-dashed border-warning bg-[var(--color-unknown-allergen-bg)] px-1.5 py-0.5 text-[12px] font-bold leading-4 text-warning"
    >
      <HelpCircle className="h-3 w-3" strokeWidth={1.75} />
      Allergens unknown
    </span>
  );
}

const href = (p: Product) => `/products/${encodeURIComponent(p.id)}`;

function brandLine(p: Product) {
  return [p.brand, p.vendor && p.vendor !== p.brand ? p.vendor : null].filter(Boolean).join(" · ");
}

export function ProductCard({ product: p }: { product: Product }) {
  const t = useStrings().catalog;
  const unknown = allergenBuckets(p).unknown;
  return (
    <article className="relative flex min-w-0 flex-col rounded border border-line bg-white p-3 sm:p-4" data-testid="product-card">
      <div className="flex items-start justify-between gap-2">
        <div className="-ml-4 flex min-w-0 flex-col items-start gap-1 sm:-ml-5">
          <TemperatureBadge code={p.temperature_condition} showRange={false} />
        </div>
        <Link to={href(p)} className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-primary-soft text-navy hover:bg-[#E2EBEF]" aria-label={`${t.details}: ${p.name}`} title={t.details}>
          <FileText className="h-4 w-4" strokeWidth={1.5} />
        </Link>
      </div>
      <Link to={href(p)} className="group mt-2 block" tabIndex={-1} aria-hidden="true">
        <ProductImage src={p.image_url} alt="" category={p.category} className="h-[110px] w-full sm:h-[130px] [&_svg]:h-14 [&_svg]:w-14" />
      </Link>
      <Link to={href(p)} className="mt-3 line-clamp-2 min-h-[42px] text-[15px] font-bold leading-[21px] text-navy hover:underline sm:text-[16px]">
        {p.name}
      </Link>
      <div className="mt-1 flex min-w-0 items-baseline gap-2">
        <SkuChip id={p.id} />
        <span className="truncate text-[12px] text-ink-muted">{brandLine(p)}</span>
      </div>
      <div className="mt-2 flex min-h-[22px] flex-wrap items-center gap-1">
        <DietChips product={p} />
        {unknown && <UnknownAllergenMarker />}
      </div>
      <div className="mt-auto pt-3">
        <Link to={`${href(p)}#check`} className="text-[14px] font-bold text-navy hover:underline">
          {t.checkPrice}
        </Link>
        <div className="mt-2 flex flex-wrap items-end justify-between gap-2">
          <div className="flex min-w-0 flex-col gap-0.5">
            <UnitLine product={p} />
            <Co2PerKg value={p.co2_kg_per_kg} />
          </div>
          <AddToCartControl product={p} variant="icon" />
        </div>
        <div className="mt-3 flex items-center justify-between gap-2 border-t border-line pt-3">
          <ReliabilityDot value={p.delivery_reliability} />
        </div>
      </div>
    </article>
  );
}

export function ProductRow({ product: p }: { product: Product }) {
  const unknown = allergenBuckets(p).unknown;
  return (
    <div className="flex flex-col gap-3 border-b border-line bg-white p-4 last:border-b-0 md:flex-row md:items-center" data-testid="product-row">
      <div className="flex min-w-0 flex-1 items-center gap-4">
        <ProductImage src={p.image_url} alt="" category={p.category} className="h-12 w-12 shrink-0 [&_svg]:h-8 [&_svg]:w-8" />
        <div className="min-w-0 flex-1">
          <Link to={href(p)} className="line-clamp-2 text-[14px] font-bold leading-5 text-navy hover:underline">
            {p.name}
          </Link>
          <div className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-1">
            <SkuChip id={p.id} />
            <span className="truncate text-[12px] text-ink-muted">{brandLine(p)}</span>
            <DietChips product={p} />
            {unknown && <UnknownAllergenMarker />}
          </div>
        </div>
      </div>
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 md:w-[360px] md:shrink-0">
        <TemperatureBadge code={p.temperature_condition} showRange={false} />
        <UnitLine product={p} />
        <ReliabilityDot value={p.delivery_reliability} />
        <Co2PerKg value={p.co2_kg_per_kg} />
      </div>
      <AddToCartControl product={p} variant="icon" className="md:shrink-0" />
    </div>
  );
}
