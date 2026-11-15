// Small product display pieces shared by the catalog, product detail, cart and quote views (display only).
import { useId, useState } from "react";
import { Leaf, Plus, ShoppingCart } from "lucide-react";
import type { Product } from "../../api/types";
import { useCart, type CartProductSnapshot } from "../../cart/CartContext";
import { baseQuantityHint } from "../../domain/units";
import { useStrings } from "../../i18n";
import { formatNumber, formatPercent } from "../../lib/format";
import { Button } from "../Button";
import { toast } from "../Toast";
import { Tooltip } from "../Tooltip";

/** SKU: e.g. "100001" in grey mono (portal tiles show the product code in grey under the name). */
export function SkuChip({ id, className = "" }: { id: string; className?: string }) {
  return <span className={`font-mono-code inline-block text-[13px] leading-5 text-ink-muted ${className}`}>{id}</span>;
}

/** Reliability dot + "98% on-time" from `delivery_reliability` (0..1). Null → "Reliability n/a" (grey). */
export function ReliabilityDot({ value, className = "" }: { value: number | null | undefined; className?: string }) {
  const c = useStrings().catalog;
  if (value == null) {
    return (
      <span className={`inline-flex items-center gap-1 whitespace-nowrap text-xs text-ink-muted ${className}`}>
        <span className="h-2.5 w-2.5 rounded-full border border-line-strong bg-white" aria-hidden="true" />
        {c.reliabilityNa}
      </span>
    );
  }
  const dot = value >= 0.95 ? "bg-lime" : value >= 0.8 ? "bg-yellow" : "bg-red";
  return (
    <span className={`tabular-nums inline-flex items-center gap-2 whitespace-nowrap text-[13px] text-ink ${className}`}>
      <span className={`h-2.5 w-2.5 rounded-full ${dot}`} aria-hidden="true" />
      {formatPercent(value)} {c.onTime}
    </span>
  );
}

/** "~1.1 kg CO₂e/kg" with a tooltip explaining it is a category-average estimate. Null → nothing. */
export function Co2PerKg({ value, className = "" }: { value: number | null | undefined; className?: string }) {
  const c = useStrings().catalog;
  if (value == null) return null;
  return (
    <Tooltip content={c.co2PerKgHelp}>
      <span tabIndex={0} className={`tabular-nums inline-flex cursor-help items-center gap-1 whitespace-nowrap text-[12px] text-ink-muted underline decoration-dotted underline-offset-2 ${className}`}>
        <Leaf className="h-3.5 w-3.5 text-success" strokeWidth={1.75} />~{formatNumber(value, value < 10 ? 1 : 0)} {c.co2PerKg}
      </span>
    </Tooltip>
  );
}

/** "1 ST = 1 ST"-style unit line for one sales unit (from the product's own `units`). */
export function UnitLine({ product, className = "" }: { product: Pick<Product, "sales_unit" | "base_unit" | "units">; className?: string }) {
  const hint = baseQuantityHint(product, 1);
  return (
    <span className={`tabular-nums whitespace-nowrap text-[12px] text-ink-muted ${className}`}>
      <span className="font-bold text-ink">{product.sales_unit}</span>
      {hint && product.sales_unit !== product.base_unit && <span className="text-ink-muted"> · {hint}</span>}
    </span>
  );
}

/** Parse a quantity input: positive number or null. */
export function parseQty(v: string): number | null {
  const n = Number(v.replace(",", "."));
  return Number.isFinite(n) && n > 0 ? n : null;
}

/**
 * Compact qty (sales units) + add button. Adds to the local cart only; prices come from the quote.
 * `variant="icon"` = the portal tile's round 40 px petrol cart button (aria-label "Add <name>").
 */
export function AddToCartControl({ product, size = "sm", variant = "button", className = "" }: {
  product: Product | CartProductSnapshot;
  size?: "sm" | "md";
  variant?: "button" | "icon";
  className?: string;
}) {
  const c = useStrings().catalog;
  const { addLine } = useCart();
  const [qty, setQty] = useState("1");
  const inputId = `qty-${product.id}-${useId().replace(/:/g, "")}`;
  const n = parseQty(qty);
  const add = () => {
    if (n == null) return;
    addLine(product, n);
    toast.success(c.added(product.name), baseQuantityHint(product, n) ?? `${formatNumber(n)} ${product.sales_unit}`);
  };
  const label = `${c.add} ${product.name}`;
  return (
    <div className={`flex items-center gap-2 ${className}`}>
      <label className="sr-only" htmlFor={inputId}>
        {c.qty} ({product.sales_unit})
      </label>
      <div className={`flex ${variant === "icon" || size === "md" ? "h-10" : "h-9"} items-center rounded border border-line-strong bg-white focus-within:border-navy`}>
        <input
          id={inputId}
          inputMode="decimal"
          value={qty}
          onChange={(e) => setQty(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && add()}
          aria-invalid={n == null}
          className="tabular-nums h-full w-11 rounded-l bg-transparent px-2 text-right text-[14px] text-ink focus:outline-none"
        />
        <span className="pr-2 text-[12px] font-bold text-ink-muted">{product.sales_unit}</span>
      </div>
      {variant === "icon" ? (
        <Button variant="icon" onClick={add} disabled={n == null} aria-label={label} title={label}>
          <ShoppingCart className="h-[18px] w-[18px]" strokeWidth={1.5} />
        </Button>
      ) : (
        <Button size={size === "md" ? "md" : "sm"} onClick={add} disabled={n == null} icon={<Plus className="h-4 w-4" strokeWidth={2} />} aria-label={label}>
          {c.add}
        </Button>
      )}
    </div>
  );
}
