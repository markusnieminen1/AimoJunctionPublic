// Rich cards for the assistant's `actions[]` (API_CONTRACT AssistantAction). Display only: every figure comes from the action.
// Unknown action types render a neutral "Unsupported action" card and never crash the stream.
import { useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { Bot, CheckCircle2, Clock, MessageCircleQuestion, PackageSearch, Puzzle, ShoppingCart, Sparkles, Tag as TagIcon, Undo2, UtensilsCrossed } from "lucide-react";
import { useCancelOrder, useOrder, useQuote } from "../../api/hooks";
import { useAuth } from "../../auth/AuthContext";
import type {
  A2ANegotiationTurn,
  AssistantAction,
  DealItemOut,
  DealsResponse,
  MenuPlanResponse,
  Order,
  Product,
  Quote,
  RecipeSanityWarning,
} from "../../api/types";
import { useCart } from "../../cart/CartContext";
import { allergenBuckets } from "../../domain/allergens";
import { parseDietList } from "../../domain/diets";
import { useStrings } from "../../i18n";
import { formatDayDate } from "../../lib/dates";
import { formatEur } from "../../lib/format";
import { Badge } from "../Badge";
import { Button, ButtonLink } from "../Button";
import { CO2Value } from "../CO2Value";
import { CopyChip } from "../CopyChip";
import { DeliveryStatusBadge } from "../DeliveryStatusBadge";
import { DietChips } from "../DietChips";
import { ErrorPanel } from "../ErrorPanel";
import { Money } from "../Money";
import { NegotiatedSavingsBadge } from "../NegotiatedSavingsBadge";
import { PolicyBanner } from "../PolicyBanner";
import { ReasoningSteps } from "../ReasoningSteps";
import { Stripe } from "../Stripe";
import { TemperatureBadge } from "../TemperatureBadge";
import { toast } from "../Toast";
import { RecipeSanityNotice } from "../approvals/RecipeSanityNotice";
import { UnknownAllergenMarker } from "../catalog/ProductCard";
import { AddToCartControl, Co2PerKg, ReliabilityDot, SkuChip, UnitLine } from "../product/ProductBits";
import { OrderResultCard } from "../quote/OrderResultCard";
import { QuotePanel } from "../quote/QuotePanel";

function CardShell({ icon, title, children, testId }: { icon: ReactNode; title: ReactNode; children: ReactNode; testId: string }) {
  return (
    <section className="min-w-0 overflow-hidden rounded border border-line bg-white" data-testid={testId}>
      <header className="flex items-center gap-2 border-b border-line bg-primary-soft px-4 py-2.5">
        <span className="shrink-0 text-navy">{icon}</span>
        <h3 className="min-w-0 truncate text-[14px] font-bold text-navy">{title}</h3>
      </header>
      <div className="min-w-0">{children}</div>
    </section>
  );
}

const iconCls = "h-4 w-4";

// ---------- search_results ----------

function AssistantProductRow({ product: p }: { product: Product }) {
  const t = useStrings().assistant;
  const unknown = allergenBuckets(p).unknown;
  const brand = [p.brand, p.vendor && p.vendor !== p.brand ? p.vendor : null].filter(Boolean).join(" · ");
  return (
    <li className="flex flex-col gap-2 px-4 py-3 sm:flex-row sm:items-center sm:gap-3" data-testid="assistant-product">
      <div className="min-w-0 flex-1">
        <div className="flex min-w-0 items-center gap-1.5">
          <SkuChip id={p.id} className="shrink-0" />
          <Link to={`/products/${encodeURIComponent(p.id)}`} className="min-w-0 truncate text-[16px] font-bold text-navy hover:underline" title={`${t.openProduct}: ${p.name}`}>
            {p.name}
          </Link>
        </div>
        {(brand || p.category_name) && <p className="mt-0.5 truncate text-xs text-ink-muted">{brand || p.category_name}</p>}
        <div className="mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-1">
          <TemperatureBadge code={p.temperature_condition} showRange={false} />
          <DietChips product={p} />
          {unknown && <UnknownAllergenMarker />}
          <UnitLine product={p} />
          <ReliabilityDot value={p.delivery_reliability} />
          <Co2PerKg value={p.co2_kg_per_kg} />
        </div>
      </div>
      <AddToCartControl product={p} className="shrink-0" />
    </li>
  );
}

export function SearchResultsCard({ products }: { products: Product[] }) {
  const t = useStrings().assistant;
  const list = Array.isArray(products) ? products : [];
  return (
    <CardShell icon={<PackageSearch className={iconCls} strokeWidth={1.75} />} title={t.productsTitle(list.length)} testId="action-search_results">
      {list.length === 0 ? (
        <p className="px-4 py-3 text-[14px] text-ink-muted">{t.noProducts}</p>
      ) : (
        <ul className="divide-y divide-line">
          {list.map((p, i) => (
            <AssistantProductRow key={`${p.id}-${i}`} product={p} />
          ))}
        </ul>
      )}
    </CardShell>
  );
}

// ---------- quote ----------

export function QuoteActionCard({ quote, placedOrder, onOrderPlaced }: { quote: Quote; placedOrder?: Order; onOrderPlaced: (o: Order) => void }) {
  const t = useStrings().assistant;
  if (placedOrder) {
    // An order already exists for this quote: never offer Place order again (the quote is used).
    return (
      <div className="min-w-0 space-y-2 rounded border border-line bg-white p-4" data-testid="action-quote">
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-ink-secondary">
          <CheckCircle2 className="h-4 w-4 text-success" strokeWidth={1.75} />
          <span className="font-semibold text-ink">{t.quoteTitle}</span>
          <CopyChip value={quote.id} />
          <Money value={quote.total_excl_vat_eur} vat="excl" className="font-semibold text-ink" />
          <NegotiatedSavingsBadge savingsEur={quote.negotiated_savings_eur} />
          <span>{t.quoteOrdered}</span>
        </div>
        <OrderResultCard order={placedOrder} />
      </div>
    );
  }
  return <LiveQuotePanel quote={quote} onOrderPlaced={onOrderPlaced} />;
}

/** The chat's quote is a snapshot: re-read it so a quote ordered elsewhere (e.g. on /cart) shows as used, not orderable. */
function LiveQuotePanel({ quote, onOrderPlaced }: { quote: Quote; onOrderPlaced: (o: Order) => void }) {
  const live = useQuote(quote.id, { silent: true });
  const shown = live.data && live.data.id === quote.id ? live.data : quote;
  return (
    <div className="min-w-0" data-testid="action-quote">
      <QuotePanel quote={shown} showSubstitutes={false} onOrderPlaced={onOrderPlaced} />
    </div>
  );
}

// ---------- order ----------

export function OrderActionCard({ order }: { order: Order }) {
  const strings = useStrings();
  const t = strings.assistant;
  const { isUser } = useAuth();
  const live = useOrder(order.id, { silent: true });
  const o = live.data ?? order;
  const cancel = useCancelOrder({ silent: true });
  const canUndo = isUser && (o.status === "PLACED" || o.status === "PENDING_APPROVAL");

  return (
    <div className="min-w-0 space-y-2" data-testid="action-order">
      {o.status === "PENDING_APPROVAL" && (
        <div className="flex flex-wrap items-center gap-3 rounded-[3px] border-l-4 border-l-warning bg-yellow-soft px-4 py-3" data-testid="waiting-approval">
          <Clock className="h-5 w-5 shrink-0 text-warning" strokeWidth={1.75} />
          <div className="min-w-0 flex-1">
            <p className="text-[14px] font-bold text-ink">{t.waitingApproval}</p>
            <p className="text-[13px] text-ink">{t.waitingApprovalBody}</p>
          </div>
          <ButtonLink to="/approvals" size="sm" variant="outline">
            {t.openApprovals}
          </ButtonLink>
        </div>
      )}
      <OrderResultCard order={o} />
      {canUndo && (
        <div className="flex flex-wrap items-center gap-2">
          <Button
            type="button"
            variant="danger-text"
            size="sm"
            icon={<Undo2 className="h-3.5 w-3.5" strokeWidth={1.75} />}
            disabled={cancel.isPending}
            onClick={() =>
              cancel.mutate(
                { id: o.id },
                {
                  onSuccess: () => toast.success(strings.quote.undoDone),
                },
              )
            }
            data-testid="undo-order-btn"
          >
            {strings.quote.undoOrder}
          </Button>
        </div>
      )}
      {cancel.isError && <ErrorPanel error={cancel.error} />}
    </div>
  );
}

// ---------- clarification ----------

export function ClarificationCard({
  question,
  options,
  answer,
  disabled,
  onChoose,
}: {
  question: string;
  options?: string[] | null;
  answer?: string;
  disabled: boolean;
  onChoose: (option: string) => void;
}) {
  const t = useStrings().assistant;
  const opts = Array.isArray(options) ? options.filter((o): o is string => typeof o === "string") : [];
  return (
    <CardShell icon={<MessageCircleQuestion className={iconCls} strokeWidth={1.75} />} title={t.clarificationTitle} testId="action-clarification">
      <div className="px-4 py-3">
        <p className="text-[16px] leading-[21px] text-ink">{question}</p>
        {opts.length > 0 && (
          <div className="mt-3 flex flex-wrap gap-2" role="group" aria-label={question}>
            {opts.map((o) => {
              const chosen = answer === o;
              return (
                <button
                  key={o}
                  type="button"
                  disabled={disabled || answer != null}
                  aria-pressed={chosen}
                  onClick={() => onChoose(o)}
                  className={`max-w-full rounded-cta border-2 px-4 py-2 text-left text-[14px] font-bold transition-colors disabled:cursor-not-allowed ${
                    chosen
                      ? "border-navy bg-navy text-white"
                      : answer != null
                        ? "border-line bg-primary-soft text-ink-muted"
                        : "border-navy bg-white text-navy hover:bg-primary-soft disabled:opacity-40"
                  }`}
                >
                  {chosen && <CheckCircle2 className="-mt-0.5 mr-1 inline h-3.5 w-3.5" strokeWidth={2} />}
                  {o}
                </button>
              );
            })}
          </div>
        )}
        {answer != null && <p className="label-display mt-2 text-[14px] text-ink-muted">{t.answered}</p>}
      </div>
    </CardShell>
  );
}

// ---------- deals ----------

function isValioProduct(p: Product): boolean {
  return /valio/i.test(`${p.brand ?? ""} ${p.vendor ?? ""} ${p.name}`);
}

function DealRow({ item }: { item: DealItemOut }) {
  const t = useStrings().assistant;
  const p = item.product;
  const line = item.quote_line;
  const unknown = allergenBuckets(p).unknown;
  const valio = isValioProduct(p);
  const pct = Math.round(item.discount_pct);
  return (
    <li className="flex flex-col gap-3 px-4 py-3 sm:flex-row sm:items-center sm:justify-between" data-testid="assistant-deal-item">
      <div className="min-w-0 flex-1 space-y-1">
        <div className="flex flex-wrap items-center gap-1.5">
          {item.on_sale && pct > 0 && (
            <span className="label-display inline-flex items-center bg-yellow px-1.5 py-0.5 text-[13px] leading-4 text-black">
              -{pct}%
            </span>
          )}
          {valio && <Badge tone="info">{t.dealsValio}</Badge>}
          {item.promoted && (
            <Badge tone="navy" icon={<Sparkles className="h-3 w-3" strokeWidth={1.75} />}>
              {t.dealsPick}
            </Badge>
          )}
          <SkuChip id={p.id} />
          <Link to={`/products/${encodeURIComponent(p.id)}`} className="min-w-0 truncate text-[15px] font-bold text-navy hover:underline">
            {p.name}
          </Link>
        </div>

        <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1 text-xs">
          {line.unit_price_eur != null && (
            <span className="inline-flex items-baseline gap-1.5">
              {item.on_sale && item.list_price_ex_vat != null && (
                <span className="tabular-nums text-ink-muted line-through">{formatEur(item.list_price_ex_vat)}</span>
              )}
              <Money value={line.unit_price_eur} className={`text-[15px] font-bold ${item.on_sale ? "text-red" : "text-ink"}`} />
              <span className="text-ink-muted">/ {p.sales_unit}</span>
            </span>
          )}
          <DeliveryStatusBadge status={line.delivery_status} />
          <TemperatureBadge code={p.temperature_condition} showRange={false} />
          <DietChips product={p} />
          {unknown && <UnknownAllergenMarker />}
          <UnitLine product={p} />
        </div>

        {item.promotion_reasons && item.promotion_reasons.length > 0 && (
          <p className="text-xs text-ink-secondary">{item.promotion_reasons.join(" · ")}</p>
        )}
      </div>
      <AddToCartControl product={p} className="shrink-0" />
    </li>
  );
}

export function DealsActionCard({ deals }: { deals: DealsResponse }) {
  const t = useStrings().assistant;
  const items = Array.isArray(deals.items) ? deals.items : [];
  const promos = Array.isArray(deals.promotions_applied) ? deals.promotions_applied : [];
  return (
    <CardShell icon={<TagIcon className={iconCls} strokeWidth={1.75} />} title={t.dealsTitle(items.length)} testId="action-deals">
      {promos.length > 0 && (
        <div className="flex flex-wrap items-center gap-1.5 border-b border-line bg-lavender/40 px-4 py-2 text-xs">
          <span className="font-semibold text-navy">{t.dealsPick}:</span>
          {promos.map((r) => (
            <Badge key={r} tone="navy">
              {r}
            </Badge>
          ))}
        </div>
      )}
      {items.length === 0 ? (
        <p className="px-4 py-3 text-[14px] text-ink-muted">{t.dealsEmpty}</p>
      ) : (
        <ul className="divide-y divide-line">
          {items.map((item, i) => (
            <DealRow key={`${item.product.id}-${i}`} item={item} />
          ))}
        </ul>
      )}
    </CardShell>
  );
}

// ---------- menu_plan ----------

export function MenuPlanActionCard({ menuPlan }: { menuPlan: MenuPlanResponse }) {
  const t = useStrings().assistant;
  const cart = useCart();
  const [added, setAdded] = useState(false);
  const recipes = Array.isArray(menuPlan.recipes) ? menuPlan.recipes : [];
  const items = Array.isArray(menuPlan.items) ? menuPlan.items : [];
  const warnings = Array.isArray(menuPlan.warnings) ? menuPlan.warnings : [];
  const reasoning = Array.isArray(menuPlan.reasoning_steps) ? menuPlan.reasoning_steps : [];
  const promos = Array.isArray(menuPlan.promotions_applied) ? menuPlan.promotions_applied : [];
  const co2CoveredItems = items.filter((item) => item.quote_line?.co2_kg != null);
  const co2Bases = Array.from(new Set(co2CoveredItems.map((item) => item.quote_line.co2_basis).filter((b): b is string => Boolean(b))));
  const partialCo2Coverage = items.length > 0 && co2CoveredItems.length < items.length;
  const co2BasisSummary = t.menuCo2Basis(co2CoveredItems.length, items.length, co2Bases);

  const handleAddMenu = () => {
    if (menuPlan.requested_delivery_date) cart.setDeliveryDate(menuPlan.requested_delivery_date);
    if (menuPlan.diet) {
      const codes = parseDietList(menuPlan.diet);
      if (codes.length) cart.setDiet(codes);
    }
    let count = 0;
    for (const item of items) {
      const qty = item.quote_line?.quantity ?? 1;
      if (item.product && qty > 0) {
        cart.addLine(item.product, qty);
        count++;
      }
    }
    setAdded(true);
    toast.success(t.menuAddedToCart(count));
  };

  return (
    <CardShell icon={<UtensilsCrossed className={iconCls} strokeWidth={1.75} />} title={t.menuTitle(menuPlan.pax, menuPlan.meal_type)} testId="action-menu_plan">
      <div className="space-y-4 p-4">
        {/* Meta & cost summary */}
        <div className="flex flex-wrap items-center justify-between gap-3 rounded border border-line bg-primary-soft/50 p-3">
          <div className="space-y-1">
            <div className="flex flex-wrap items-center gap-2 text-xs text-ink-secondary">
              <span className="font-semibold text-ink">{formatDayDate(menuPlan.requested_delivery_date)}</span>
              {menuPlan.diet && <Badge tone="info">Diet: {menuPlan.diet}</Badge>}
              {promos.map((p) => (
                <Badge key={p} tone="navy">
                  {p}
                </Badge>
              ))}
            </div>
            {menuPlan.estimated_savings_eur > 0 && (
              <p className="inline-flex items-center gap-1 rounded-sm bg-yellow px-2 py-0.5 text-xs font-bold text-black tabular-nums">
                {t.menuSavings(formatEur(menuPlan.estimated_savings_eur), formatEur(menuPlan.estimated_list_total_excl_vat_eur))}
              </p>
            )}
          </div>
          <div className="text-right">
            <p className="text-[11px] font-bold uppercase tracking-[0.04em] text-ink-muted">{t.menuTotalExcl}</p>
            <div className="flex flex-wrap items-baseline justify-end gap-2">
              <Money value={menuPlan.estimated_total_excl_vat_eur} className="text-[22px] font-bold text-navy" />
              <span className="text-xs text-ink-muted">
                (<Money value={menuPlan.estimated_total_incl_vat_eur} vat="incl" />)
              </span>
            </div>
            {menuPlan.estimated_co2_kg != null && (
              <div className="mt-0.5 flex flex-col items-end text-xs">
                <CO2Value kg={menuPlan.estimated_co2_kg} basis={co2BasisSummary} />
                {partialCo2Coverage && (
                  <span className="mt-0.5 text-[11px] font-medium text-warning" data-testid="menu-plan-co2-partial">
                    {t.menuCo2Partial(co2CoveredItems.length, items.length)}
                  </span>
                )}
              </div>
            )}
          </div>
        </div>

        {(menuPlan.budget_exceeded || warnings.length > 0) && (
          <Stripe kind="warning" compact accent>
            <ul className="space-y-0.5 text-xs">
              {menuPlan.budget_exceeded && <li>{t.menuBudgetWarn}</li>}
              {warnings.map((w, i) => (
                <li key={i}>{w}</li>
              ))}
            </ul>
          </Stripe>
        )}

        {menuPlan.policy_preview && <PolicyBanner decision={menuPlan.policy_preview} />}

        {/* Recipes */}
        {recipes.length > 0 && (
          <div className="space-y-2">
            <h4 className="text-xs font-bold uppercase tracking-[0.04em] text-ink-muted">{t.menuRecipes}</h4>
            <ul className="grid gap-2 sm:grid-cols-2">
              {recipes.map((r) => (
                <li key={r.id} className="rounded border border-line bg-white p-3">
                  <div className="flex flex-wrap items-center justify-between gap-1.5">
                    <span className="text-[14px] font-bold text-navy">{r.name}</span>
                    <Badge tone="neutral">{r.meal_type}</Badge>
                  </div>
                  <p className="mt-1 text-xs text-ink-secondary">{r.chef_note}</p>
                  {r.diet_tags && r.diet_tags.length > 0 && (
                    <div className="mt-2 flex flex-wrap gap-1">
                      {r.diet_tags.map((tag) => (
                        <span key={tag} className="inline-flex h-5 items-center border border-navy px-1.5 text-[10px] font-bold text-navy">
                          {tag}
                        </span>
                      ))}
                    </div>
                  )}
                </li>
              ))}
            </ul>
          </div>
        )}

        {/* Selected ingredients */}
        {items.length > 0 && (
          <div className="space-y-2">
            <h4 className="text-xs font-bold uppercase tracking-[0.04em] text-ink-muted">{t.menuIngredients}</h4>
            <ul className="divide-y divide-line rounded border border-line bg-white">
              {items.map((item, i) => {
                const p = item.product;
                const line = item.quote_line;
                const unknown = allergenBuckets(p).unknown;
                const pct = Math.round(item.discount_pct);
                return (
                  <li key={`${item.recipe_id}-${p.id}-${i}`} className="flex flex-col gap-1.5 px-3 py-2.5 sm:flex-row sm:items-center sm:justify-between">
                    <div className="min-w-0 flex-1 space-y-1">
                      <div className="flex flex-wrap items-center gap-1.5 text-xs">
                        <span className="font-semibold text-ink-secondary">{item.ingredient_label}</span>
                        <span className="text-ink-muted">·</span>
                        <SkuChip id={p.id} />
                        <Link to={`/products/${encodeURIComponent(p.id)}`} className="font-bold text-navy hover:underline">
                          {p.name}
                        </Link>
                        {pct > 0 && <span className="label-display bg-yellow px-1.5 py-0.5 text-[11px] leading-none text-black">-{pct}%</span>}
                        {item.promoted && <Badge tone="navy">{t.dealsPick}</Badge>}
                      </div>
                      <div className="flex flex-wrap items-center gap-2 text-xs">
                        <DietChips product={p} />
                        {unknown && <UnknownAllergenMarker />}
                        <DeliveryStatusBadge status={line.delivery_status} />
                        <span className="text-ink-secondary">{item.selection_reason}</span>
                      </div>
                    </div>
                    <div className="shrink-0 text-right tabular-nums text-xs">
                      <p className="font-bold text-ink">
                        {line.quantity} {line.sales_unit} · <Money value={line.line_total_eur} />
                      </p>
                      {item.savings_eur > 0 && (
                        <p className="text-success font-semibold">-{formatEur(item.savings_eur)}</p>
                      )}
                      {line.co2_kg != null ? (
                        <div className="mt-0.5 flex justify-end">
                          <CO2Value kg={line.co2_kg} basis={line.co2_basis} className="text-[11px]" />
                        </div>
                      ) : (
                        <p className="mt-0.5 text-[11px] text-ink-muted">{t.menuCo2Missing}</p>
                      )}
                    </div>
                  </li>
                );
              })}
            </ul>
          </div>
        )}

        {/* Expandable reasoning */}
        {reasoning.length > 0 && (
          <details className="rounded border border-line bg-primary-soft/30 px-3 py-2" data-testid="menu-plan-reasoning-toggle">
            <summary className="cursor-pointer text-xs font-bold text-navy hover:underline">{t.menuReasoning(reasoning.length)}</summary>
            <div className="mt-3">
              <ReasoningSteps steps={reasoning} testId="menu-plan-reasoning" />
            </div>
          </details>
        )}

        {/* Add menu ingredients to cart */}
        {items.length > 0 && (
          <div className="flex flex-wrap items-center gap-2 border-t border-line pt-3">
            <Button variant="lime" size="md" onClick={handleAddMenu} icon={<ShoppingCart className="h-4 w-4" strokeWidth={1.75} />}>
              {t.menuAddToCart}
            </Button>
            {added && (
              <ButtonLink to="/cart" variant="outline" size="md">
                {t.menuViewCart}
              </ButtonLink>
            )}
          </div>
        )}
      </div>
    </CardShell>
  );
}

export function A2ANegotiationCard({
  summary,
  targetPax,
  turns,
}: {
  summary?: string;
  targetPax?: number;
  turns: A2ANegotiationTurn[];
}) {
  const t = useStrings().assistant;
  return (
    <CardShell
      icon={<Bot className={iconCls} strokeWidth={1.75} />}
      title={summary ? `${t.a2aTitle} · ${summary}` : t.a2aTitle}
      testId="action-a2a_negotiation"
    >
      <div className="space-y-2.5 p-4 text-[13px]">
        {targetPax != null && (
          <p className="text-xs font-semibold text-ink-secondary">
            Pax: <span className="font-bold text-navy">{targetPax}</span>
          </p>
        )}
        <ol className="space-y-2">
          {turns.map((turn, i) => (
            <li key={i} className="rounded border border-line bg-surface px-3 py-2">
              <div className="flex flex-wrap items-center justify-between gap-1.5">
                <span className="font-mono-code text-[11px] font-bold uppercase text-navy">{turn.speaker}</span>
                {turn.status && (
                  <Badge tone={turn.status === "PENDING_APPROVAL" ? "warning" : turn.status === "verified" ? "success" : "info"}>
                    {turn.status}
                  </Badge>
                )}
              </div>
              <p className="mt-1 text-[13px] text-ink">{turn.message}</p>
            </li>
          ))}
        </ol>
      </div>
    </CardShell>
  );
}

// ---------- unknown ----------

export function UnsupportedActionCard({ type }: { type: string }) {
  const t = useStrings().assistant;
  return (
    <div className="flex items-start gap-2 rounded border border-dashed border-line-strong bg-primary-soft px-4 py-3" data-testid="action-unsupported">
      <Puzzle className="mt-0.5 h-4 w-4 shrink-0 text-ink-muted" strokeWidth={1.75} />
      <div className="min-w-0">
        <p className="text-sm font-semibold text-ink-secondary">
          {t.unsupportedTitle} <span className="font-mono-code rounded bg-white px-1.5 py-0.5 text-[11px] font-medium text-ink-secondary">{type || "?"}</span>
        </p>
        <p className="mt-0.5 text-xs text-ink-muted">{t.unsupportedBody}</p>
      </div>
    </div>
  );
}

// ---------- dispatcher ----------

export type ActionHandlers = {
  answer?: string;
  placedOrder?: Order;
  busy: boolean;
  onChoose: (option: string) => void;
  onOrderPlaced: (order: Order) => void;
};

/** Render one AssistantAction defensively: anything malformed falls back to the unsupported card. */
export function ActionCard({ action, h }: { action: AssistantAction; h: ActionHandlers }) {
  const a = action as unknown as Record<string, unknown> | null;
  const type = a && typeof a.type === "string" ? a.type : "";
  if (type === "search_results" && Array.isArray(a!.products)) return <SearchResultsCard products={a!.products as Product[]} />;
  if (type === "quote" && a!.quote && typeof a!.quote === "object" && Array.isArray((a!.quote as Quote).lines) && (a!.quote as Quote).policy_preview) {
    return <QuoteActionCard quote={a!.quote as Quote} placedOrder={h.placedOrder} onOrderPlaced={h.onOrderPlaced} />;
  }
  if (type === "order" && a!.order && typeof a!.order === "object" && typeof (a!.order as Order).id === "string") return <OrderActionCard order={a!.order as Order} />;
  if (type === "clarification" && typeof a!.question === "string") {
    return <ClarificationCard question={a!.question} options={a!.options as string[] | undefined} answer={h.answer} disabled={h.busy} onChoose={h.onChoose} />;
  }
  if (type === "deals" && a!.deals && typeof a!.deals === "object" && Array.isArray((a!.deals as DealsResponse).items)) {
    return <DealsActionCard deals={a!.deals as DealsResponse} />;
  }
  if (type === "menu_plan" && a!.menu_plan && typeof a!.menu_plan === "object" && typeof (a!.menu_plan as MenuPlanResponse).plan_id === "string") {
    return <MenuPlanActionCard menuPlan={a!.menu_plan as MenuPlanResponse} />;
  }
  if (type === "recipe_sanity_warning" && a!.warning && typeof a!.warning === "object") {
    return <RecipeSanityNotice warning={a!.warning as RecipeSanityWarning} />;
  }
  if (type === "a2a_negotiation" && Array.isArray(a!.turns)) {
    return (
      <A2ANegotiationCard
        summary={typeof a!.summary === "string" ? a!.summary : undefined}
        targetPax={typeof a!.target_pax === "number" ? a!.target_pax : undefined}
        turns={a!.turns as A2ANegotiationTurn[]}
      />
    );
  }
  return <UnsupportedActionCard type={type} />;
}
