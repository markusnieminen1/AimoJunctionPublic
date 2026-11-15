// M4 /catalog, M10 look (DESIGN_VALIO §6 /catalog): breadcrumb + display h1, a full-bleed category pill band, a 200 px
// filter column (drawer below 992), toolbar, pagination bar and portal product tiles. Debounced search synced with ?q=,
// server-side category/diet/free-from filters, page-local temperature filter, grid/list toggle (remembered).
// Everything shown comes from GET /api/products. The API has no sort parameter, so there is no sort select.
import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { Bot, ChevronDown, ChevronRight, LayoutGrid, List, PackageSearch, Plus, Search, SlidersHorizontal, Sparkles, Tag as TagIcon, X } from "lucide-react";
import { useCategories, useProductDeals, useProducts } from "../api/hooks";
import type { DealItemOut, DietCode, ProductSearchParams } from "../api/types";
import { useCart } from "../cart/CartContext";
import { Badge } from "../components/Badge";
import { Button } from "../components/Button";
import { PageHeader } from "../components/Card";
import { DietChips } from "../components/DietChips";
import { Money } from "../components/Money";
import { PaginationBar } from "../components/PaginationBar";
import { CategoryPill, FilterPill } from "../components/Pills";
import { CatalogFilters, FREE_FROM_FILTERS, getTempFilters, type CatalogFilterState, type TempFilter } from "../components/catalog/CatalogFilters";
import { ProductCard, ProductRow, UnknownAllergenMarker } from "../components/catalog/ProductCard";
import { EmptyState } from "../components/EmptyState";
import { ErrorPanel } from "../components/ErrorPanel";
import { Skeleton } from "../components/Skeleton";
import { toast } from "../components/Toast";
import { allergenBuckets } from "../domain/allergens";
import { DIETS, DIET_CODES } from "../domain/diets";
import { temperatureInfo } from "../domain/temperature";
import { useStrings } from "../i18n";
import { formatEur } from "../lib/format";

export const CATALOG_PAGE_SIZE = 24;
const VIEW_KEY = "aimoannos.catalogView";
type View = "grid" | "list";

function loadView(): View {
  try {
    return window.localStorage.getItem(VIEW_KEY) === "list" ? "list" : "grid";
  } catch {
    return "grid";
  }
}

const csv = (v: string | null) => (v ? v.split(",").filter(Boolean) : []);

export default function CatalogPage() {
  const strings = useStrings();
  const c = strings.catalog;
  const [params, setParams] = useSearchParams();
  const urlQ = params.get("q") ?? "";
  const page = Math.max(1, Number(params.get("page")) || 1);
  const state: CatalogFilterState = {
    category: params.get("category"),
    diets: csv(params.get("diet")).filter((d): d is DietCode => (DIET_CODES as string[]).includes(d)),
    free: csv(params.get("free")),
    temp: (["chilled", "frozen", "ambient"] as string[]).includes(params.get("temp") ?? "") ? (params.get("temp") as TempFilter) : null,
  };

  const update = (patch: Record<string, string | null>) => {
    setParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        for (const [k, v] of Object.entries(patch)) {
          if (v == null || v === "") next.delete(k);
          else next.set(k, v);
        }
        return next;
      },
      { replace: true },
    );
  };

  const onFilterChange = (patch: Partial<CatalogFilterState>) => {
    const out: Record<string, string | null> = { page: null };
    if ("category" in patch) out.category = patch.category ?? null;
    if (patch.diets) out.diet = patch.diets.join(",") || null;
    if (patch.free) out.free = patch.free.join(",") || null;
    if ("temp" in patch) out.temp = patch.temp ?? null;
    update(out);
  };

  // Debounced search box ↔ ?q= (the header quick search navigates here too).
  const [term, setTerm] = useState(urlQ);
  const pushed = useRef(urlQ);
  useEffect(() => {
    if (urlQ !== pushed.current) {
      pushed.current = urlQ;
      setTerm(urlQ);
    }
  }, [urlQ]);
  useEffect(() => {
    const t = window.setTimeout(() => {
      const v = term.trim();
      if (v !== pushed.current) {
        pushed.current = v;
        update({ q: v || null, page: null });
      }
    }, 300);
    return () => window.clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [term]);

  const [view, setView] = useState<View>(loadView);
  const changeView = (v: View) => {
    setView(v);
    try {
      window.localStorage.setItem(VIEW_KEY, v);
    } catch {
      /* ignore */
    }
  };
  const [drawer, setDrawer] = useState(false);
  const [dealsOpen, setDealsOpen] = useState(false);
  const [onSaleOnly, setOnSaleOnly] = useState(false);
  const cart = useCart();

  const search: ProductSearchParams = {
    q: urlQ || undefined,
    category: state.category ?? undefined,
    diet: state.diets.length ? state.diets.join(",") : undefined,
    allergen_free: state.free.length ? state.free.join(",") : undefined,
    limit: CATALOG_PAGE_SIZE,
    offset: (page - 1) * CATALOG_PAGE_SIZE,
  };
  const products = useProducts(search, { silent: true });
  const categories = useCategories();
  const deals = useProductDeals(
    {
      q: urlQ || undefined,
      category: state.category ?? undefined,
      diet: state.diets.length ? state.diets.join(",") : undefined,
      on_sale_only: onSaleOnly || undefined,
      requested_delivery_date: cart.requestedDeliveryDate,
      limit: 6,
    },
    { silent: true },
  );

  const items = products.data?.items ?? [];
  const shown = useMemo(() => (state.temp ? items.filter((p) => temperatureInfo(p.temperature_condition).zone === state.temp) : items), [items, state.temp]);
  const total = products.data?.total ?? 0;
  const pages = Math.max(1, Math.ceil(total / CATALOG_PAGE_SIZE));
  const catName = state.category ? categories.data?.items.find((c) => c.code === state.category)?.name ?? state.category : null;

  const pills: { key: string; label: string; clear: () => void }[] = [];
  if (urlQ) pills.push({ key: "q", label: `“${urlQ}”`, clear: () => { setTerm(""); pushed.current = ""; update({ q: null, page: null }); } });
  if (catName) pills.push({ key: "cat", label: catName, clear: () => onFilterChange({ category: null }) });
  for (const d of state.diets) pills.push({ key: `d-${d}`, label: `${DIETS[d].chip} · ${DIETS[d].en}`, clear: () => onFilterChange({ diets: state.diets.filter((x) => x !== d) }) });
  for (const f of state.free) pills.push({ key: `f-${f}`, label: `Free from ${FREE_FROM_FILTERS.find((o) => o.value === f)?.label.toLowerCase() ?? f}`, clear: () => onFilterChange({ free: state.free.filter((x) => x !== f) }) });
  if (state.temp) pills.push({ key: "temp", label: `${getTempFilters(c).find((t) => t.value === state.temp)?.label} (this page)`, clear: () => onFilterChange({ temp: null }) });
  const clearAll = () => {
    setTerm("");
    pushed.current = "";
    setParams(new URLSearchParams(), { replace: true });
  };

  const filters = <CatalogFilters state={state} categories={categories.data?.items} categoriesLoading={categories.isLoading} onChange={onFilterChange} />;
  const topCats = [...(categories.data?.items ?? [])].sort((x, y) => y.product_count - x.product_count).slice(0, 12);
  const gotoPage = (n: number) => update({ page: n > 1 ? String(n) : null });

  return (
    <div className="pb-16">
      <div className="container-portal pt-6 md:pt-8 lg:pt-12">
        <PageHeader
          breadcrumb={[{ label: strings.portal.frontPage, to: "/" }, { label: c.title, to: urlQ || catName ? "/catalog" : undefined }, ...(catName ? [{ label: catName }] : [])]}
          title={urlQ ? c.searchResults(urlQ) : catName ?? c.title}
          subtitle={c.subtitle}
        />
      </div>

      {/* Full-bleed petrol-light band with category pills (front page "Browse product categories"). */}
      <section className="bg-primary-soft" aria-label={c.categoryBand}>
        <div className="container-portal py-6">
          <h2 className="h-card mb-4">{c.categoryBand}</h2>
          <div className="no-scrollbar -mx-4 flex gap-2 overflow-x-auto px-4 md:mx-0 md:flex-wrap md:overflow-visible md:px-0" data-testid="category-pills">
            <CategoryPill active={!state.category} onClick={() => onFilterChange({ category: null })}>
              {c.allProducts}
            </CategoryPill>
            {categories.isLoading
              ? Array.from({ length: 6 }, (_, i) => <Skeleton key={i} className="h-8 w-36 shrink-0 rounded-full" />)
              : topCats.map((cat) => (
                  <CategoryPill key={cat.code} active={state.category === cat.code} count={cat.product_count} onClick={() => onFilterChange({ category: state.category === cat.code ? null : cat.code })}>
                    {cat.name}
                  </CategoryPill>
                ))}
          </div>
        </div>
      </section>

      <div className="container-portal pt-8">
        {!deals.isError && ((deals.data?.items.length ?? 0) > 0 || onSaleOnly) && (
          <CatalogDealsStrip
            items={deals.data?.items ?? []}
            open={dealsOpen}
            onSaleOnly={onSaleOnly}
            onToggleOpen={() => setDealsOpen((v) => !v)}
            onToggleOnSale={() =>
              setOnSaleOnly((v) => {
                const next = !v;
                if (next) setDealsOpen(true);
                return next;
              })
            }
            onAdd={(item) => {
              cart.addLine(item.product, 1);
              toast.success(c.added(item.product.name));
            }}
          />
        )}
        <div className="flex gap-6">
          <aside className="hidden w-[200px] shrink-0 lg:block" aria-label={c.filters}>
            {filters}
          </aside>

          <div className="min-w-0 flex-1">
            <div className="mb-4 flex flex-wrap items-center gap-2">
              <div className="relative min-w-[200px] flex-1">
                <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-navy" strokeWidth={1.75} />
                <input
                  type="search"
                  value={term}
                  onChange={(e) => setTerm(e.target.value)}
                  placeholder={c.searchPlaceholder}
                  aria-label="Search products"
                  className="h-[45px] w-full rounded border border-line bg-white pl-9 pr-3 text-[16px] placeholder:text-line-strong focus:border-navy focus:outline-none focus:ring-[3px] focus:ring-[rgba(0,86,112,0.15)]"
                />
              </div>
              <Button variant="outline" className="lg:hidden" icon={<SlidersHorizontal className="h-4 w-4" strokeWidth={1.5} />} onClick={() => setDrawer(true)}>
                {c.showFilters}
                {pills.length > 0 && <span className="tabular-nums ml-1 inline-flex h-5 min-w-[20px] items-center justify-center rounded-full bg-red px-1 font-sans text-[10px] font-bold text-white">{pills.length}</span>}
              </Button>
            </div>

            <div className="mb-4 flex min-h-[24px] flex-wrap items-center gap-2">
              <span className="tabular-nums mr-1 text-[14px] font-bold text-ink" data-testid="catalog-total">
                {products.data ? c.results(total) : " "}
              </span>
              {pills.map((p) => (
                <span key={p.key} className="inline-flex h-8 items-center gap-1 rounded-full bg-navy pl-3 pr-1 text-[13px] font-bold text-white">
                  {p.label}
                  <button type="button" onClick={p.clear} aria-label={`Remove filter ${p.label}`} className="rounded-full p-1 hover:bg-white/20">
                    <X className="h-3.5 w-3.5" strokeWidth={2} />
                  </button>
                </span>
              ))}
              {pills.length > 1 && (
                <Button variant="text" size="sm" onClick={clearAll}>
                  {c.clearAll}
                </Button>
              )}
              {state.temp && products.data && shown.length < items.length && (
                <span className="text-[13px] text-ink-muted">· {c.pageHidden(items.length - shown.length)}</span>
              )}
              <div className="ml-auto flex gap-2" role="group" aria-label="View">
                <Button variant="icon-light" aria-pressed={view === "grid"} aria-label={c.grid} onClick={() => changeView("grid")} className={view === "grid" ? "!bg-navy !text-white" : ""}>
                  <LayoutGrid className="h-4 w-4" strokeWidth={1.5} />
                </Button>
                <Button variant="icon-light" aria-pressed={view === "list"} aria-label={c.list} onClick={() => changeView("list")} className={view === "list" ? "!bg-navy !text-white" : ""}>
                  <List className="h-4 w-4" strokeWidth={1.5} />
                </Button>
              </div>
            </div>

            {products.data && total > 0 && (
              <PaginationBar noun={c.noun} page={page} pageSize={CATALOG_PAGE_SIZE} total={total} onPage={gotoPage} className="mb-4" />
            )}

            {products.isError && products.data && (
              <div className="mb-4">
                <ErrorPanel error={products.error} title="Could not refresh results · showing earlier results" onRetry={() => void products.refetch()} />
              </div>
            )}
            {products.isError && !products.data ? (
              <ErrorPanel error={products.error} onRetry={() => void products.refetch()} />
            ) : products.isLoading ? (
              <CatalogSkeleton view={view} />
            ) : shown.length === 0 ? (
              <EmptyState
                icon={<PackageSearch strokeWidth={1.5} />}
                title={c.emptyTitle}
                description={c.emptyBody}
                action={pills.length > 0 ? <Button variant="outline" onClick={clearAll}>{c.clearAll}</Button> : undefined}
              />
            ) : view === "grid" ? (
              <div className={`grid grid-cols-2 gap-3 sm:gap-4 md:grid-cols-3 xl:grid-cols-4 xl:gap-6 ${products.isFetching ? "opacity-70" : ""}`}>
                {shown.map((p) => (
                  <ProductCard key={p.id} product={p} />
                ))}
              </div>
            ) : (
              <div className={`overflow-hidden rounded border border-line ${products.isFetching ? "opacity-70" : ""}`}>
                {shown.map((p) => (
                  <ProductRow key={p.id} product={p} />
                ))}
              </div>
            )}

            {products.data && pages > 1 && (
              <div className="mt-8 flex items-center justify-center gap-4">
                <Button variant="outline" size="md" disabled={page <= 1} onClick={() => gotoPage(page - 1)}>
                  {c.prev}
                </Button>
                <span className="tabular-nums text-[14px] text-ink-muted">{c.pageOf(page, pages)}</span>
                <Button variant="primary" size="md" disabled={page >= pages} onClick={() => gotoPage(page + 1)}>
                  {c.next}
                </Button>
              </div>
            )}
          </div>
        </div>
      </div>

      {drawer && (
        <div className="fixed inset-0 z-50 lg:hidden" role="dialog" aria-modal="true" aria-label={c.filters}>
          <div className="absolute inset-0 bg-[rgba(0,32,42,0.55)]" onClick={() => setDrawer(false)} />
          <div className="absolute inset-y-0 left-0 flex w-[85vw] max-w-[360px] flex-col bg-white shadow-popover">
            <div className="flex h-14 shrink-0 items-center justify-between bg-navy px-4">
              <h2 className="label-display text-[20px] text-white">{c.filters}</h2>
              <button type="button" onClick={() => setDrawer(false)} aria-label={strings.common.close} className="flex h-10 w-10 items-center justify-center text-white">
                <X className="h-6 w-6" strokeWidth={1.5} />
              </button>
            </div>
            <div className="flex-1 overflow-y-auto p-4">{filters}</div>
            <div className="flex gap-2 border-t border-line p-4">
              <Button variant="outline" className="flex-1" onClick={clearAll}>
                {c.clearAll}
              </Button>
              <Button className="flex-1" onClick={() => setDrawer(false)}>
                {products.data ? c.results(total) : strings.common.close}
              </Button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function CatalogDealsStrip({
  items,
  open,
  onSaleOnly,
  onToggleOpen,
  onToggleOnSale,
  onAdd,
}: {
  items: DealItemOut[];
  open: boolean;
  onSaleOnly: boolean;
  onToggleOpen: () => void;
  onToggleOnSale: () => void;
  onAdd: (item: DealItemOut) => void;
}) {
  const strings = useStrings();
  const c = strings.catalog;
  return (
    <section className="mb-6 overflow-hidden rounded border border-line bg-white" data-testid="catalog-deals" aria-label={c.dealsTitle}>
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-line bg-lavender/45 px-4 py-3">
        <div className="flex flex-wrap items-center gap-2">
          <TagIcon className="h-4 w-4 text-navy" strokeWidth={1.75} aria-hidden="true" />
          <h2 className="text-[16px] font-bold text-navy">{c.dealsTitle}</h2>
          {items.length > 0 && (
            <span className="rounded bg-white/80 px-1.5 py-0.5 text-xs font-bold text-navy tabular-nums">
              {items.length}
            </span>
          )}
          <FilterPill active={onSaleOnly} onClick={onToggleOnSale}>
            {c.dealsOnSaleOnly}
          </FilterPill>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <Link
            to={`/assistant?prompt=${encodeURIComponent(c.dealsAskBotPrompt)}`}
            className="inline-flex items-center gap-1.5 text-xs font-bold text-navy hover:underline"
          >
            <Bot className="h-3.5 w-3.5" strokeWidth={1.75} />
            {c.dealsAskBot}
          </Link>
          <Button
            variant="text"
            size="sm"
            aria-expanded={open}
            onClick={onToggleOpen}
            icon={open ? <ChevronDown className="h-4 w-4" strokeWidth={1.75} /> : <ChevronRight className="h-4 w-4" strokeWidth={1.75} />}
          >
            {open ? c.dealsHide : c.dealsShow}
          </Button>
        </div>
      </div>
      {open && (
        <div className="p-4">
          {items.length === 0 ? (
            <p className="text-sm text-ink-muted">{strings.assistant.dealsEmpty}</p>
          ) : (
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
              {items.map((item) => {
                const p = item.product;
                const pct = Math.round(item.discount_pct);
                const valio = /valio/i.test(`${p.brand ?? ""} ${p.vendor ?? ""} ${p.name}`);
                const unknown = allergenBuckets(p).unknown;
                return (
                  <div key={p.id} className="flex flex-col justify-between rounded border border-line bg-white p-3" data-testid="catalog-deal-card">
                    <div className="space-y-1.5">
                      <div className="flex flex-wrap items-center gap-1.5">
                        {item.on_sale && pct > 0 && (
                          <span className="label-display bg-yellow px-1.5 py-0.5 text-[13px] leading-4 text-black">-{pct}%</span>
                        )}
                        {(item.promoted || valio) && (
                          <Badge tone="navy" icon={<Sparkles className="h-3 w-3" strokeWidth={1.75} />}>
                            {valio ? c.dealsValioPick : c.dealsPromoted}
                          </Badge>
                        )}
                        <span className="font-mono-code text-[11px] text-ink-muted">{p.id}</span>
                      </div>
                      <Link to={`/products/${encodeURIComponent(p.id)}`} className="block line-clamp-2 text-[14px] font-bold leading-5 text-navy hover:underline">
                        {p.name}
                      </Link>
                      <div className="flex flex-wrap items-center gap-1.5">
                        <DietChips product={p} />
                        {unknown && <UnknownAllergenMarker />}
                      </div>
                    </div>
                    <div className="mt-3 flex items-center justify-between gap-2 border-t border-line pt-2.5">
                      <div className="tabular-nums">
                        {item.on_sale && item.list_price_ex_vat != null && (
                          <span className="mr-1.5 text-xs text-ink-muted line-through">{formatEur(item.list_price_ex_vat)}</span>
                        )}
                        <Money value={item.quote_line.unit_price_eur} className={`text-[16px] font-bold ${item.on_sale ? "text-red" : "text-ink"}`} />
                        <span className="ml-1 text-xs text-ink-muted">/ {p.sales_unit}</span>
                      </div>
                      <Button
                        variant="icon"
                        aria-label={`${c.add} ${p.name}`}
                        onClick={() => onAdd(item)}
                      >
                        <Plus className="h-4 w-4" strokeWidth={2} />
                      </Button>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      )}
    </section>
  );
}

function CatalogSkeleton({ view }: { view: View }) {
  if (view === "list") {
    return (
      <div className="space-y-px rounded border border-line bg-white p-2" aria-busy="true" aria-label="Loading">
        {Array.from({ length: 8 }, (_, i) => (
          <Skeleton key={i} className="h-[64px] w-full" />
        ))}
      </div>
    );
  }
  return (
    <div className="grid grid-cols-2 gap-3 sm:gap-4 md:grid-cols-3 xl:grid-cols-4 xl:gap-6" aria-busy="true" aria-label="Loading">
      {Array.from({ length: 8 }, (_, i) => (
        <div key={i} className="rounded border border-line bg-white p-4">
          <Skeleton className="mb-2 h-5 w-20" />
          <Skeleton className="h-[130px] w-full" />
          <Skeleton className="mt-3 h-4 w-4/5" />
          <Skeleton className="mt-1.5 h-3 w-1/2" />
          <Skeleton className="mt-6 h-10 w-full" />
        </div>
      ))}
    </div>
  );
}
