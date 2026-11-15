// One cart line: product, sales-unit stepper, base-unit helper ("1,000 ST = 1,000 ST"), remove; optional quote figures.
import { useEffect, useState } from "react";
import { allergenBuckets } from "../../domain/allergens";
import { UnknownAllergenMarker } from "../catalog/ProductCard";
import { Link } from "react-router-dom";
import { Minus, Plus } from "lucide-react";
import type { QuoteLine } from "../../api/types";
import type { CartLine } from "../../cart/CartContext";
import { baseQuantityHint } from "../../domain/units";
import { useStrings } from "../../i18n";
import { formatNumber } from "../../lib/format";
import { Button } from "../Button";
import { DeliveryStatusBadge } from "../DeliveryStatusBadge";
import { DietChips } from "../DietChips";
import { Money } from "../Money";
import { ProductImage } from "../ProductImage";
import { TemperatureBadge } from "../TemperatureBadge";
import { SkuChip, parseQty } from "../product/ProductBits";

export function CartLineRow({ line, quoteLine, onQuantity, onRemove }: {
  line: CartLine;
  /** The matching line of a quote that still matches the cart (display only). */
  quoteLine?: QuoteLine | null;
  onQuantity: (qty: number) => void;
  onRemove: () => void;
}) {
  const strings = useStrings();
  const p = line.product;
  const [text, setText] = useState(String(line.quantity));
  useEffect(() => setText(String(line.quantity)), [line.quantity]);
  const parsed = parseQty(text);
  const hint = baseQuantityHint(p, parsed ?? line.quantity);
  const step = (d: number) => {
    const next = Math.max(1, Math.round((line.quantity + d) * 100) / 100);
    onQuantity(next);
  };

  return (
    <li className="flex flex-col gap-3 px-4 py-4 sm:flex-row sm:items-center sm:gap-4" data-testid="cart-line">
      <div className="flex min-w-0 flex-1 items-start gap-3.5">
        <ProductImage src={p.image_url} alt="" className="h-12 w-12 shrink-0 [&_svg]:h-7 [&_svg]:w-7" />
        <div className="min-w-0">
          <Link to={`/products/${encodeURIComponent(p.id)}`} className="line-clamp-2 text-[14px] font-semibold leading-[1.4] text-ink hover:text-navy hover:underline">
            {p.name}
          </Link>
          <div className="mt-1 flex flex-wrap items-center gap-1.5">
            <SkuChip id={p.id} />
            <TemperatureBadge code={p.temperature_condition} showRange={false} />
            <DietChips product={p} />
            {allergenBuckets(p).unknown && <UnknownAllergenMarker />}
          </div>
          {quoteLine && (
            <div className="mt-1.5 flex flex-wrap items-center gap-2 text-xs">
              <Money value={quoteLine.line_total_eur} className="font-semibold text-ink" />
              <DeliveryStatusBadge status={quoteLine.delivery_status} />
            </div>
          )}
        </div>
      </div>
      <div className="flex items-center justify-between gap-3 sm:justify-end">
        <div>
          <div className="flex h-[45px] items-center rounded-[2px] border border-line-strong bg-white">
            <button
              type="button"
              onClick={() => step(-1)}
              disabled={line.quantity <= 1}
              aria-label={`${strings.cart.decrease}: ${p.name}`}
              className="flex h-full w-10 items-center justify-center border-r border-line text-[#005670] transition-colors hover:bg-primary-soft disabled:opacity-40"
            >
              <Minus className="h-3.5 w-3.5" strokeWidth={2} />
            </button>
            <input
              inputMode="decimal"
              value={text}
              aria-label={`${strings.catalog.qty} (${p.sales_unit}): ${p.name}`}
              aria-invalid={parsed == null}
              onChange={(e) => {
                setText(e.target.value);
                const n = parseQty(e.target.value);
                if (n != null) onQuantity(n);
              }}
              onBlur={() => parsed == null && setText(String(line.quantity))}
              className={`tabular-nums h-full w-16 bg-transparent px-1 text-center text-sm font-semibold focus:outline-none ${parsed == null ? "text-danger" : "text-ink"}`}
            />
            <span className="px-1.5 text-[11px] font-semibold text-ink-muted">{p.sales_unit}</span>
            <button
              type="button"
              onClick={() => step(1)}
              aria-label={`${strings.cart.increase}: ${p.name}`}
              className="flex h-full w-10 items-center justify-center border-l border-line text-[#005670] transition-colors hover:bg-primary-soft"
            >
              <Plus className="h-3.5 w-3.5" strokeWidth={2} />
            </button>
          </div>
          <p className="tabular-nums mt-1 text-right text-[11.5px] text-ink-muted" data-testid="base-hint">
            {hint ?? `${formatNumber(parsed ?? line.quantity)} ${p.sales_unit}`}
          </p>
        </div>
        <Button type="button" variant="text" size="sm" onClick={onRemove} aria-label={`${strings.cart.remove}: ${p.name}`}>
          {strings.cart.remove}
        </Button>
      </div>
    </li>
  );
}
