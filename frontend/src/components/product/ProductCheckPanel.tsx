// "Check live price & availability" (DESIGN_VALIO §6 /products: petrol-light box, qty stepper, date input with calendar
// icon, primary CHECK, result, round cart icon + ADD TO CART): qty (sales units) + date → POST /api/products/check → LineFacts.
// The unit price shown is exactly the API's; the stepper only edits the requested quantity.
import { useState } from "react";
import { Link } from "react-router-dom";
import { CalendarDays, CheckCircle2, Minus, Plus, RefreshCw, ShoppingCart } from "lucide-react";
import { useProductCheck } from "../../api/hooks";
import type { Product, QuoteLine } from "../../api/types";
import { useCart } from "../../cart/CartContext";
import { baseQuantityHint } from "../../domain/units";
import { useStrings } from "../../i18n";
import { defaultDeliveryDate, formatDayDate } from "../../lib/dates";
import { useServerNow } from "../../lib/useServerNow";
import { Button } from "../Button";
import { Money } from "../Money";
import { ErrorPanel } from "../ErrorPanel";
import { Field, TextInput } from "../FormField";
import { LineFacts } from "../quote/LineBits";
import { parseQty } from "./ProductBits";

export function ProductCheckPanel({ product }: { product: Product }) {
  const strings = useStrings();
  const nowMs = useServerNow(60_000);
  const cart = useCart();
  const [qty, setQty] = useState("1");
  const [date, setDate] = useState<string | null>(null);
  const effectiveDate = date ?? defaultDeliveryDate(nowMs);
  const n = parseQty(qty);
  const check = useProductCheck({ silent: true });
  const [result, setResult] = useState<{ line: QuoteLine; qty: number; date: string } | null>(null);
  const [added, setAdded] = useState(false);

  const run = () => {
    if (n == null) return;
    setAdded(false);
    check.mutate(
      { product_ids: [product.id], quantities: { [product.id]: n }, requested_delivery_date: effectiveDate },
      {
        onSuccess: (r) => {
          const line = r.items.find((l) => l.product_id === product.id) ?? r.items[0];
          setResult(line ? { line, qty: n, date: effectiveDate } : null);
        },
      },
    );
  };

  const stale = result != null && (result.qty !== n || result.date !== effectiveDate);
  const hint = n != null ? baseQuantityHint(product, n) : null;

  const addToCart = () => {
    if (n == null) return;
    if (cart.lines.length === 0) cart.setDeliveryDate(effectiveDate);
    cart.addLine(product, n);
    setAdded(true);
  };

  const step = (d: number) => {
    const cur = n ?? 0;
    const next = Math.max(1, cur + d);
    setQty(String(next));
  };
  const qtyId = `check-qty-${product.id}`;

  return (
    <section id="check" className="scroll-mt-[calc(var(--header-h)+16px)] rounded bg-primary-soft p-5 md:p-6" aria-labelledby="check-title">
      <h2 id="check-title" className="text-[20px] font-bold leading-[22px] text-navy">
        {strings.product.checkTitle}
      </h2>
      <p className="mt-2 text-[14px] leading-5 text-ink">{strings.product.checkLead}</p>
      <form
        className="mt-5 grid gap-5 sm:grid-cols-2"
        onSubmit={(e) => {
          e.preventDefault();
          run();
        }}
      >
        <div>
          <label htmlFor={qtyId} className="mb-2 block text-[14px] font-bold leading-[17px] text-ink">
            {`${strings.product.quantity} (${product.sales_unit})`}
          </label>
          <div className="flex h-[45px] items-stretch gap-1">
            <Button variant="icon-light" type="button" aria-label={strings.cart.decrease} onClick={() => step(-1)} className="!h-[45px] !w-10 !rounded bg-white">
              <Minus className="h-4 w-4" strokeWidth={1.75} />
            </Button>
            <input
              id={qtyId}
              inputMode="decimal"
              value={qty}
              onChange={(e) => setQty(e.target.value)}
              aria-invalid={n == null}
              aria-describedby={`${qtyId}-hint`}
              className="tabular-nums h-[45px] w-20 min-w-0 flex-1 rounded border border-line-strong bg-white px-2 text-center text-[16px] focus:border-navy focus:outline-none focus:ring-[3px] focus:ring-[rgba(0,86,112,0.15)] sm:flex-none"
            />
            <Button variant="icon-light" type="button" aria-label={strings.cart.increase} onClick={() => step(1)} className="!h-[45px] !w-10 !rounded bg-white">
              <Plus className="h-4 w-4" strokeWidth={1.75} />
            </Button>
          </div>
          <p id={`${qtyId}-hint`} className={`mt-1.5 text-[12px] ${n == null ? "font-medium text-danger" : "text-ink-muted"}`}>
            {n == null ? "Enter a quantity greater than 0" : hint ?? " "}
          </p>
        </div>
        <Field label={strings.product.deliveryDate} hint={formatDayDate(effectiveDate)}>
          {(p) => (
            <div className="relative">
              <CalendarDays className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-navy" strokeWidth={1.5} />
              <TextInput {...p} type="date" value={effectiveDate} onChange={(e) => setDate(e.target.value || null)} className="tabular-nums pl-9" />
            </div>
          )}
        </Field>
        <div className="sm:col-span-2">
          <Button type="submit" size="lg" loading={check.isPending} disabled={n == null} icon={result ? <RefreshCw className="h-4 w-4" strokeWidth={1.75} /> : undefined}>
            {strings.product.checkCta}
          </Button>
        </div>
      </form>

      {check.isError && (
        <div className="mt-4">
          <ErrorPanel error={check.error} onRetry={run} />
        </div>
      )}

      {result && !check.isError && (
        <div className="mt-5 rounded border border-line bg-white px-4 py-3" data-testid="check-result" aria-live="polite">
          {stale && <p className="mb-2 rounded-[3px] bg-yellow-soft px-3 py-1.5 text-[13px] font-medium text-ink">{strings.product.inputsChanged}</p>}
          {result.line.unit_price_eur != null && (
            <p className="tabular-nums flex flex-wrap items-baseline gap-x-1.5">
              <Money value={result.line.unit_price_eur} className="text-[20px] font-bold leading-[22px] text-ink" />
              <span className="text-[14px] text-ink">/ {result.line.sales_unit}</span>
              <span className="text-[13px] text-ink-muted">{strings.common.exclVat}</span>
            </p>
          )}
          <p className="tabular-nums mt-1 text-[13px] font-bold text-ink-secondary">
            {strings.product.deliveryDate}: {formatDayDate(result.date)}
          </p>
          <LineFacts line={result.line} className="mt-1" />
        </div>
      )}

      <div className="mt-5 flex flex-wrap items-center gap-3">
        <Button variant="icon" onClick={addToCart} disabled={n == null} aria-hidden="true" tabIndex={-1}>
          <ShoppingCart className="h-[18px] w-[18px]" strokeWidth={1.5} />
        </Button>
        <Button variant={result ? "primary" : "outline"} size="lg" onClick={addToCart} disabled={n == null}>
          {strings.product.addToCart}
        </Button>
        {added && (
          <span className="inline-flex items-center gap-1.5 text-[14px] font-medium text-success" role="status">
            <CheckCircle2 className="h-4 w-4" strokeWidth={1.75} />
            {strings.product.addedToCart}
            <Link to="/cart" className="font-bold text-link hover:underline">
              {strings.product.viewCart}
            </Link>
          </span>
        )}
      </div>
    </section>
  );
}
