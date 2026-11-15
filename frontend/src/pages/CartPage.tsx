// M5 /cart: lines editor + requested delivery date (left), sticky Quote & Policy panel (right).
// Prices, totals, VAT, CO₂, delivery status and policy come only from POST /api/quotes.
import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { CalendarDays, FileText, Handshake, ShoppingCart } from "lucide-react";
import { useCreateQuote, useNegotiateQuote, useNegotiationSettings, useQuote } from "../api/hooks";
import type { LineOptimizationSwap, Order, Quote } from "../api/types";
import { cartSignature, useCart } from "../cart/CartContext";
import { OrdersLayout } from "../components/AccountLayout";
import { Button, ButtonLink } from "../components/Button";
import { Card, CardHeader } from "../components/Card";
import { CartLineRow } from "../components/cart/CartLineRow";
import { CartOptimizerPanel } from "../components/cart/CartOptimizerPanel";
import { DietRequirementPicker } from "../components/cart/DietRequirementPicker";
import { EmptyState } from "../components/EmptyState";
import { ErrorPanel } from "../components/ErrorPanel";
import { Field, TextInput } from "../components/FormField";
import { Skeleton } from "../components/Skeleton";
import { toast } from "../components/Toast";
import { QuotePanel } from "../components/quote/QuotePanel";
import { SubstitutesPanel, type SwapHandler } from "../components/quote/SubstitutesPanel";
import { useStrings } from "../i18n";
import { dietParam } from "../domain/diets";
import { defaultDeliveryDate, formatDayDate } from "../lib/dates";
import { useServerNow } from "../lib/useServerNow";

export default function CartPage() {
  const strings = useStrings();
  const t = strings.cart;
  const cart = useCart();
  const nowMs = useServerNow(60_000);
  const createQuote = useCreateQuote({ silent: true });
  const negotiateQuote = useNegotiateQuote({ silent: true });
  const negSettings = useNegotiationSettings({ silent: true });
  const [quote, setQuote] = useState<Quote | null>(null);
  const [placedOrder, setPlacedOrder] = useState<Order | null>(null);
  const [requoteAfterChange, setRequoteAfterChange] = useState<"quote" | "negotiate" | null>(null);
  const [manualDate, setManualDate] = useState(false);
  const [alignedDeliveryDate, setAlignedDeliveryDate] = useState<string | null>(null);
  const autoAdvancedRef = useRef<string>("");
  /** The diet param that was sent with the shown quote (the real backend does not echo it). */
  const [quoteDiet, setQuoteDiet] = useState<string | null>(null);

  const maxAvailableFor = (q: Quote): string =>
    q.lines.reduce(
      (maxD, l) => (l.available_date && l.available_date > maxD ? l.available_date : maxD),
      q.requested_delivery_date,
    );

  // Restore the last quote after a reload, if it still matches the cart.
  const restoreId = !quote && cart.currentQuote ? cart.currentQuote.id : undefined;
  const restored = useQuote(restoreId, { silent: true });
  useEffect(() => {
    if (!quote && restored.data && restored.data.id === restoreId) {
      setQuote(restored.data);
      setQuoteDiet(restored.data.diet ?? cart.currentQuote?.diet ?? null);
    }
  }, [quote, restored.data, restoreId, cart.currentQuote?.diet]);

  const cartDiet = dietParam(cart.diet);
  const signature = cartSignature(cart.lines, cart.requestedDeliveryDate, cartDiet);
  const outdated = quote != null && !placedOrder && cartSignature(quote.lines, quote.requested_delivery_date, quoteDiet) !== signature;

  const getQuote = () => {
    if (cart.lines.length === 0 || createQuote.isPending) return;
    setPlacedOrder(null);
    const diet = cartDiet;
    createQuote.mutate(
      {
        requested_delivery_date: cart.requestedDeliveryDate,
        lines: cart.lines.map((l) => ({ product_id: l.product_id, quantity: l.quantity })),
        ...(diet ? { diet } : {}),
      },
      {
        onSuccess: (q) => {
          setQuote(q);
          setQuoteDiet(diet ?? null);
          cart.setLastQuote(q, diet ?? null);
          const maxAvail = maxAvailableFor(q);
          const key = cartSignature(cart.lines, "", diet);
          if (!manualDate && maxAvail > q.requested_delivery_date && autoAdvancedRef.current !== key) {
            autoAdvancedRef.current = key;
            cart.setDeliveryDate(maxAvail);
            setAlignedDeliveryDate(maxAvail);
            setRequoteAfterChange("quote");
          }
        },
      },
    );
  };

  const canNegotiate = Boolean(negSettings.data?.allow_price_negotiation && cart.lines.length > 0 && !placedOrder);
  const runNegotiateQuote = () => {
    if (!canNegotiate || negotiateQuote.isPending) return;
    setPlacedOrder(null);
    const diet = cartDiet;
    negotiateQuote.mutate(
      {
        requested_delivery_date: cart.requestedDeliveryDate,
        lines: cart.lines.map((l) => ({ product_id: l.product_id, quantity: l.quantity })),
        ...(diet ? { diet } : {}),
        target_discount_pct: 5,
      },
      {
        onSuccess: (res) => {
          setQuote(res.quote);
          setQuoteDiet(diet ?? null);
          cart.setLastQuote(res.quote, diet ?? null);
          const maxAvail = maxAvailableFor(res.quote);
          const key = cartSignature(cart.lines, "", diet);
          if (!manualDate && maxAvail > res.quote.requested_delivery_date && autoAdvancedRef.current !== key) {
            autoAdvancedRef.current = key;
            cart.setDeliveryDate(maxAvail);
            setAlignedDeliveryDate(maxAvail);
            setRequoteAfterChange("negotiate");
          } else {
            toast.success(t.negotiatedToast);
          }
        },
      },
    );
  };

  // After a substitute swap or last-available alignment, the cart lines/date change; re-quote once they have.
  useEffect(() => {
    if (requoteAfterChange) {
      const mode = requoteAfterChange;
      setRequoteAfterChange(null);
      if (mode === "negotiate") runNegotiateQuote();
      else getQuote();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [requoteAfterChange, signature]);

  // A new cart after an order starts fresh (the old quote/order card is done).
  useEffect(() => {
    if (placedOrder && cart.lines.length > 0) {
      setPlacedOrder(null);
      setQuote(null);
      setAlignedDeliveryDate(null);
      setManualDate(false);
    }
  }, [placedOrder, cart.lines.length]);

  const onSwap: SwapHandler = (fromId, sub) => {
    const from = cart.lines.find((l) => l.product_id === fromId);
    cart.swapLine(fromId, sub.product, sub.line.quantity);
    toast.info(t.swapped(from?.product.name ?? fromId, sub.product.name));
    setRequoteAfterChange("quote");
  };

  const onApplyOptimizerSwaps = (swaps: LineOptimizationSwap[]) => {
    if (swaps.length === 0) return;
    for (const sw of swaps) {
      cart.swapLine(sw.original_line.product_id, sw.recommended_product, sw.suggested_quantity);
    }
    toast.info(t.optApplied(swaps.length));
    setRequoteAfterChange("quote");
  };

  const onOrderPlaced = (order: Order) => {
    setPlacedOrder(order);
    cart.clear();
  };

  const quoteLineFor = (productId: string) => (quote && !outdated ? quote.lines.find((l) => l.product_id === productId) ?? null : null);
  const defaultDate = defaultDeliveryDate(nowMs);

  return (
    <OrdersLayout
      breadcrumb={[{ label: strings.portal.frontPage, to: "/catalog" }, { label: t.breadcrumb }]}
      title={t.title}
      subtitle={t.subtitle}
    >
      <div className="grid grid-cols-1 items-start gap-6 xl:grid-cols-12">
        <div className="min-w-0 space-y-4 xl:col-span-8">
          <Card padded={false}>
            <div className="border-b border-line px-4 py-3.5">
              <CardHeader
                title={t.linesTitle}
                description={cart.lines.length > 0 ? t.lines(cart.lines.length) : undefined}
                actions={
                  cart.lines.length > 0 ? (
                    <Button size="sm" variant="text" onClick={cart.clear}>
                      {t.clear}
                    </Button>
                  ) : undefined
                }
              />
            </div>
            {cart.lines.length === 0 ? (
              <EmptyState
                icon={<ShoppingCart className="h-5 w-5" strokeWidth={1.75} />}
                title={placedOrder ? t.ordered : t.emptyTitle}
                description={placedOrder ? undefined : t.emptyBody}
                action={<ButtonLink to="/catalog" variant={placedOrder ? "outline" : "primary"}>{placedOrder ? t.newOrder : t.browse}</ButtonLink>}
              />
            ) : (
              <ul className="divide-y divide-line">
                {cart.lines.map((l) => (
                  <CartLineRow
                    key={l.product_id}
                    line={l}
                    quoteLine={quoteLineFor(l.product_id)}
                    onQuantity={(n) => cart.setQuantity(l.product_id, n)}
                    onRemove={() => cart.removeLine(l.product_id)}
                  />
                ))}
              </ul>
            )}
          </Card>

          {cart.lines.length > 0 && (
            <Card className="space-y-5">
              <Field label={t.deliveryDate} hint={`${formatDayDate(cart.requestedDeliveryDate)} · ${t.deliveryDateHint}`} className="max-w-xs">
                {(p) => (
                  <TextInput
                    {...p}
                    type="date"
                    prefix={<CalendarDays className="h-4 w-4" strokeWidth={1.75} />}
                    value={cart.requestedDeliveryDate}
                    onChange={(e) => {
                      const val = e.target.value;
                      setManualDate(Boolean(val && val !== defaultDate));
                      setAlignedDeliveryDate(null);
                      cart.setDeliveryDate(val && val !== defaultDate ? val : null);
                    }}
                    className="tabular-nums"
                  />
                )}
              </Field>
              {(alignedDeliveryDate || (quote && maxAvailableFor(quote) > cart.requestedDeliveryDate)) && (
                <p data-testid="last-available-delivery-note" className="text-[12.5px] font-medium text-navy">
                  {t.lastAvailableDeliveryNote(formatDayDate(alignedDeliveryDate ?? maxAvailableFor(quote!)))}
                </p>
              )}
              <DietRequirementPicker value={cart.diet} onChange={cart.setDiet} />
            </Card>
          )}

          {cart.lines.length > 0 && !placedOrder && (
            <CartOptimizerPanel
              lines={cart.lines}
              requestedDeliveryDate={cart.requestedDeliveryDate}
              diet={cartDiet ?? null}
              signature={signature}
              onApplySwaps={onApplyOptimizerSwaps}
            />
          )}
        </div>

        <aside className="min-w-0 space-y-3 xl:col-span-4 xl:row-span-2 xl:sticky xl:top-[136px]" aria-label="Quote and policy">
          {createQuote.isError && <ErrorPanel error={createQuote.error} onRetry={getQuote} />}
          {negotiateQuote.isError && <ErrorPanel error={negotiateQuote.error} onRetry={runNegotiateQuote} />}
          {canNegotiate && (quote == null || !(quote.negotiated_savings_eur && quote.negotiated_savings_eur > 0) || outdated) && (
            <div className="space-y-1.5">
              <Button
                type="button"
                variant="outline"
                size="md"
                className="w-full border-navy text-navy hover:bg-primary-soft"
                icon={<Handshake className="h-4 w-4" strokeWidth={1.75} />}
                onClick={runNegotiateQuote}
                loading={negotiateQuote.isPending}
                data-testid="negotiate-quote-btn"
              >
                {t.negotiateQuote}
              </Button>
              <div className="flex justify-end">
                <Link
                  to="/admin"
                  data-testid="cart-negotiation-admin-link"
                  className="text-[12px] font-bold text-navy underline underline-offset-2 hover:text-primary"
                >
                  {t.negotiateAdminLink} →
                </Link>
              </div>
            </div>
          )}
          {!canNegotiate && cart.lines.length > 0 && !placedOrder && negSettings.data && (
            <div className="flex flex-wrap items-center justify-between gap-2 rounded-none border border-line bg-surface px-3 py-2 text-[12px]">
              <span className="min-w-0 text-ink-secondary">{t.negotiateDisabledHint}</span>
              <Link
                to="/admin"
                data-testid="cart-negotiation-admin-link"
                className="shrink-0 font-bold text-navy underline underline-offset-2 hover:text-primary"
              >
                {t.negotiateAdminLink} →
              </Link>
            </div>
          )}
          {quote ? (
            <QuotePanel
              quote={quote}
              diet={quote.diet ?? quoteDiet}
              outdated={outdated}
              onRequote={cart.lines.length > 0 ? getQuote : undefined}
              requoting={createQuote.isPending}
              onSwap={onSwap}
              showSubstitutes={false}
              onOrderPlaced={onOrderPlaced}
            />
          ) : createQuote.isPending || (restoreId && restored.isLoading) ? (
            <div className="rounded border border-line bg-primary-soft p-6" aria-busy="true" aria-label="Loading quote">
              <Skeleton className="h-5 w-24" />
              <Skeleton className="mt-4 h-16 w-full" />
              <Skeleton className="mt-2 h-16 w-full" />
              <Skeleton className="mt-4 h-12 w-full" />
            </div>
          ) : (
            <div className="rounded border border-line bg-primary-soft p-6">
              <EmptyState
                icon={<FileText className="h-5 w-5" strokeWidth={1.75} />}
                title={t.noQuoteTitle}
                description={t.noQuoteBody}
                action={
                  <Button variant="primary" size="lg" className="w-full" onClick={getQuote} disabled={cart.lines.length === 0} loading={createQuote.isPending}>
                    {t.getQuote}
                  </Button>
                }
              />
            </div>
          )}
        </aside>

        {/* Substitutes get the wide column (comparison tiles side by side); on phones they follow the quote that flagged them. */}
        {quote && !placedOrder && !outdated && (
          <SubstitutesPanel
            className="min-w-0 xl:col-span-8 xl:col-start-1 xl:row-start-2"
            lines={quote.lines}
            requestedDeliveryDate={quote.requested_delivery_date}
            diet={quote.diet ?? quoteDiet}
            onSwap={onSwap}
          />
        )}
      </div>
    </OrdersLayout>
  );
}
