// M4 /products/:id, M10 look (DESIGN_VALIO §6 /products): breadcrumb, 2 columns ≥ 992 (image + spec rows | display h1, SKU,
// diet chips, Check price & availability panel), then the 4-state allergen matrix as stripes. Phones: title + check first.
import { Link, useParams } from "react-router-dom";
import { PackageX } from "lucide-react";
import { toApiError } from "../api/client";
import { useProduct } from "../api/hooks";
import type { Product } from "../api/types";
import { ButtonLink } from "../components/Button";
import { Breadcrumb, Card } from "../components/Card";
import { EmptyState } from "../components/EmptyState";
import { ErrorMeta, ErrorPanel } from "../components/ErrorPanel";
import { ProductImage } from "../components/ProductImage";
import { Skeleton } from "../components/Skeleton";
import { TemperatureBadge } from "../components/TemperatureBadge";
import { DietChips } from "../components/DietChips";
import { AllergenMatrix } from "../components/product/AllergenMatrix";
import { ProductCheckPanel } from "../components/product/ProductCheckPanel";
import { Co2PerKg, ReliabilityDot, SkuChip } from "../components/product/ProductBits";
import { UnknownAllergenMarker } from "../components/catalog/ProductCard";
import { allergenBuckets } from "../domain/allergens";
import { useStrings } from "../i18n";
import { formatNumber } from "../lib/format";

function Spec({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="grid grid-cols-[130px_minmax(0,1fr)] gap-4 border-b border-line py-3 text-[14px] leading-5 last:border-b-0 sm:grid-cols-[170px_minmax(0,1fr)]">
      <dt className="font-bold text-ink">{label}</dt>
      <dd className="min-w-0 text-ink">{children ?? "—"}</dd>
    </div>
  );
}

function prettyLabel(l: string) {
  return l.replace(/_/g, " ").toLowerCase().replace(/^\w/, (c) => c.toUpperCase());
}

function Specs({ p }: { p: Product }) {
  const strings = useStrings();
  const labels = p.labels.filter((l) => !/Temperature$/.test(l));
  return (
    <section aria-labelledby="specs-title" className="mt-8">
      <h2 id="specs-title" className="text-[20px] font-bold leading-[22px] text-navy">
        {strings.product.specs}
      </h2>
      <dl className="mt-3 border-t border-line">
        <Spec label={strings.product.sku}>
          <SkuChip id={p.id} />
        </Spec>
        <Spec label={strings.product.brand}>{p.brand ?? "—"}</Spec>
        <Spec label={strings.product.vendor}>{p.vendor ?? "—"}</Spec>
        <Spec label={strings.product.category}>
          <Link to={`/catalog?category=${encodeURIComponent(p.category)}`} className="text-link hover:underline">
            {p.category_name ?? p.category}
          </Link>{" "}
          <span className="font-mono-code text-[11px] text-ink-muted">{p.category}</span>
        </Spec>
        <Spec label={strings.product.origin}>{p.country_of_origin ?? "—"}</Spec>
        <Spec label={strings.product.salesUnit}>
          <span className="font-bold">{p.sales_unit}</span> <span className="text-ink-muted">· {strings.product.baseUnit.toLowerCase()} {p.base_unit}</span>
        </Spec>
        <Spec label={strings.product.packSizes}>
          <ul className="flex flex-wrap gap-1.5">
            {p.units.map((u) => (
              <li key={u.unit_id} className="tabular-nums rounded-sm bg-primary-soft px-2 py-0.5 text-[13px]">
                1 {u.unit_id} = {formatNumber(u.size_in_base_units)} {p.base_unit}
              </li>
            ))}
          </ul>
        </Spec>
        <Spec label={strings.product.temperature}>
          <TemperatureBadge code={p.temperature_condition} />
        </Spec>
        <Spec label={strings.product.co2}>{p.co2_kg_per_kg == null ? <span className="text-ink-muted">Not available</span> : <Co2PerKg value={p.co2_kg_per_kg} />}</Spec>
        <Spec label={strings.product.reliability}>
          <ReliabilityDot value={p.delivery_reliability} />
        </Spec>
        <Spec label={strings.product.labels}>
          {labels.length === 0 ? (
            <span className="text-ink-muted">{strings.product.noLabels}</span>
          ) : (
            <ul className="flex flex-wrap gap-1">
              {labels.map((l) => (
                <li key={l} title={l} className="rounded-sm border border-line px-2 py-0.5 text-[12px] text-ink-secondary">
                  {prettyLabel(l)}
                </li>
              ))}
            </ul>
          )}
        </Spec>
      </dl>
    </section>
  );
}

export default function ProductPage() {
  const strings = useStrings();
  const { id = "" } = useParams();
  const q = useProduct(id, { silent: true });

  if (q.isLoading) {
    return (
      <div aria-busy="true" aria-label={strings.common.loading}>
        <Skeleton className="mb-3 h-4 w-48" />
        <Skeleton className="mb-5 h-7 w-2/3" />
        <div className="grid gap-5 lg:grid-cols-2">
          <Skeleton className="h-[520px] w-full" />
          <Skeleton className="h-[420px] w-full" />
        </div>
      </div>
    );
  }

  if (q.isError) {
    const e = toApiError(q.error);
    if (e.status === 404 || e.code === "NOT_FOUND" || e.code === "UNKNOWN_PRODUCT") {
      return (
        <Card className="mx-auto max-w-lg">
          <EmptyState
            icon={<PackageX className="h-5 w-5" strokeWidth={1.75} />}
            title={strings.product.notFoundTitle}
            description={
              <>
                {strings.product.notFoundBody(id)}
                <ErrorMeta error={q.error} />
              </>
            }
            action={
              <ButtonLink to="/catalog" variant="outline">
                {strings.cart.browse}
              </ButtonLink>
            }
          />
        </Card>
      );
    }
    return <ErrorPanel error={q.error} onRetry={() => void q.refetch()} />;
  }

  const p = q.data!;
  const unknown = allergenBuckets(p).unknown;
  return (
    <div>
      <Breadcrumb
        className="mb-4"
        items={[
          { label: strings.portal.frontPage, to: "/" },
          { label: strings.product.back, to: "/catalog" },
          { label: p.category_name ?? p.category, to: `/catalog?category=${encodeURIComponent(p.category)}` },
          { label: p.name },
        ]}
      />
      <div className="grid gap-8 lg:grid-cols-2 lg:gap-12">
        <div className="min-w-0 lg:order-2">
          <h1 className="font-display text-[32px] leading-[35px] tracking-[0.01em] text-navy md:text-[40px] md:leading-[44px]">{p.name}</h1>
          <div className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-2">
            <SkuChip id={p.id} className="text-[14px]" />
            {p.brand && <span className="text-[14px] text-ink-muted">{p.brand}</span>}
            <TemperatureBadge code={p.temperature_condition} />
            <DietChips product={p} />
            {unknown && <UnknownAllergenMarker />}
          </div>
          <div className="mt-6">
            <ProductCheckPanel key={p.id} product={p} />
          </div>
        </div>
        <div className="min-w-0 lg:order-1">
          <div className="rounded border border-line bg-white p-4">
            <ProductImage src={p.image_url} alt={p.name} category={p.category} className="h-[240px] w-full md:h-[400px] [&_svg]:h-24 [&_svg]:w-24" />
          </div>
          <Specs p={p} />
        </div>
      </div>
      <AllergenMatrix product={p} className="mt-12 md:mt-16" />
    </div>
  );
}
