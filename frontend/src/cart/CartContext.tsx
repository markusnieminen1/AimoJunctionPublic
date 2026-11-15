// Cart (local state persisted to localStorage). Holds only what the user picked; all money comes from the API.
// `lastQuote` stores the API's quote total for the current cart contents (the header shows it; never client math).
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import type { DietCode, Product, Quote } from "../api/types";
import { DIET_CODES, dietParam, parseDietList } from "../domain/diets";
import { defaultDeliveryDate } from "../lib/dates";
import { useServerNow } from "../lib/useServerNow";
import { useAuth } from "../auth/AuthContext";

/** Display-only snapshot of the product when it was added. */
export type CartProductSnapshot = Pick<
  Product,
  "id" | "name" | "sales_unit" | "base_unit" | "units" | "temperature_condition" | "image_url" | "brand" | "labels" | "allergens" | "non_allergens"
>;

export type CartLine = { product_id: string; quantity: number; product: CartProductSnapshot };

export type CartQuoteRef = {
  id: string;
  total_excl_vat_eur: number;
  total_incl_vat_eur: number;
  expires_at: string;
  /** Diet requirement sent with POST /api/quotes (comma list), null = none. */
  diet: string | null;
  /** cartSignature() of the lines + date + diet the quote was made for */
  signature: string;
};

/** `owner` = principal id the cart belongs to (null = not yet claimed, e.g. a cart saved before owners existed). */
type CartState = { owner: string | null; lines: CartLine[]; requested_delivery_date: string | null; diet: DietCode[]; lastQuote: CartQuoteRef | null };

const emptyCart = (owner: string | null): CartState => ({ owner, lines: [], requested_delivery_date: null, diet: [], lastQuote: null });

type CartValue = {
  lines: CartLine[];
  /** Explicit date or the default (next weekday ≥ server now + 1 day). */
  requestedDeliveryDate: string;
  /** Optional order-wide diet requirement (sent as `diet` with POST /api/quotes; the server enforces R-HARD-DIET). */
  diet: DietCode[];
  itemCount: number;
  /** The last quote, only if it still matches the cart contents; otherwise null. */
  currentQuote: CartQuoteRef | null;
  addLine: (product: Product | CartProductSnapshot, quantity: number) => void;
  setQuantity: (productId: string, quantity: number) => void;
  removeLine: (productId: string) => void;
  /** Replace one product with another (substitute swap), keeping or changing quantity. */
  swapLine: (fromProductId: string, to: Product | CartProductSnapshot, quantity?: number) => void;
  setDeliveryDate: (date: string | null) => void;
  setDiet: (codes: DietCode[]) => void;
  /** Remember the API quote; `diet` = the diet param that was sent with it. */
  setLastQuote: (q: Quote, diet?: string | null) => void;
  clear: () => void;
};

const KEY = "aimoannos.cart";
const CartContext = createContext<CartValue | null>(null);

export function cartSignature(lines: { product_id: string; quantity: number }[], date: string, diet?: string | null): string {
  return [...lines]
    .sort((a, b) => a.product_id.localeCompare(b.product_id))
    .map((l) => `${l.product_id}x${l.quantity}`)
    .concat(date)
    .concat(diet ? [`diet=${diet}`] : [])
    .join("|");
}

function snapshot(p: Product | CartProductSnapshot): CartProductSnapshot {
  return {
    id: p.id,
    name: p.name,
    sales_unit: p.sales_unit,
    base_unit: p.base_unit,
    units: p.units,
    temperature_condition: p.temperature_condition,
    image_url: p.image_url,
    brand: p.brand,
    labels: p.labels,
    allergens: p.allergens,
    non_allergens: p.non_allergens,
  };
}

function load(): CartState {
  try {
    const raw = window.localStorage.getItem(KEY);
    if (raw) {
      const s = JSON.parse(raw) as CartState;
      if (Array.isArray(s.lines)) {
        const diet = Array.isArray(s.diet) ? DIET_CODES.filter((c) => s.diet.includes(c)) : [];
        const owner = typeof s.owner === "string" ? s.owner : null;
        return { owner, lines: s.lines, requested_delivery_date: s.requested_delivery_date ?? null, diet, lastQuote: s.lastQuote ?? null };
      }
    }
  } catch {
    /* ignore */
  }
  return emptyCart(null);
}

export function CartProvider({ children }: { children: ReactNode }) {
  const [stored, setState] = useState<CartState>(load);
  const principalId = useAuth().principal?.id ?? null;

  // The cart belongs to the principal who filled it: after logging in as someone else (company A → company B on the
  // same browser) the other account's lines, diet and quote id are dropped, never shown. Unclaimed carts are adopted.
  let state = stored;
  if (principalId && stored.owner !== principalId) {
    state = stored.owner == null ? { ...stored, owner: principalId } : emptyCart(principalId);
    setState(state);
  }

  useEffect(() => {
    try {
      window.localStorage.setItem(KEY, JSON.stringify(state));
    } catch {
      /* ignore */
    }
  }, [state]);

  const addLine = useCallback((product: Product | CartProductSnapshot, quantity: number) => {
    if (!(quantity > 0)) return;
    setState((s) => {
      const existing = s.lines.find((l) => l.product_id === product.id);
      const lines = existing
        ? s.lines.map((l) => (l.product_id === product.id ? { ...l, quantity: l.quantity + quantity } : l))
        : [...s.lines, { product_id: product.id, quantity, product: snapshot(product) }];
      return { ...s, lines };
    });
  }, []);

  const setQuantity = useCallback((productId: string, quantity: number) => {
    setState((s) => ({
      ...s,
      lines: quantity > 0 ? s.lines.map((l) => (l.product_id === productId ? { ...l, quantity } : l)) : s.lines.filter((l) => l.product_id !== productId),
    }));
  }, []);

  const removeLine = useCallback((productId: string) => {
    setState((s) => ({ ...s, lines: s.lines.filter((l) => l.product_id !== productId) }));
  }, []);

  const swapLine = useCallback((fromProductId: string, to: Product | CartProductSnapshot, quantity?: number) => {
    setState((s) => {
      const from = s.lines.find((l) => l.product_id === fromProductId);
      const qty = quantity ?? from?.quantity ?? 1;
      const rest = s.lines.filter((l) => l.product_id !== fromProductId && l.product_id !== to.id);
      const already = s.lines.find((l) => l.product_id === to.id);
      return { ...s, lines: [...rest, { product_id: to.id, quantity: (already?.quantity ?? 0) + qty, product: snapshot(to) }] };
    });
  }, []);

  const setDeliveryDate = useCallback((date: string | null) => setState((s) => ({ ...s, requested_delivery_date: date })), []);
  const clear = useCallback(() => setState((s) => ({ ...s, lines: [], lastQuote: null })), []);


  const setDiet = useCallback((codes: DietCode[]) => setState((s) => ({ ...s, diet: DIET_CODES.filter((c) => codes.includes(c)) })), []);

  const setLastQuote = useCallback((q: Quote, diet?: string | null) => {
    const d = dietParam(parseDietList(diet ?? q.diet ?? null)) ?? null;
    setState((s) => ({
      ...s,
      lastQuote: {
        id: q.id,
        total_excl_vat_eur: q.total_excl_vat_eur,
        total_incl_vat_eur: q.total_incl_vat_eur,
        expires_at: q.expires_at,
        diet: d,
        signature: cartSignature(q.lines, q.requested_delivery_date, d),
      },
    }));
  }, []);

  // Re-evaluated when the server clock syncs (and once a minute), never from the raw browser date.
  const nowMs = useServerNow(60_000);
  const requestedDeliveryDate = state.requested_delivery_date ?? defaultDeliveryDate(nowMs);
  const signature = cartSignature(state.lines, requestedDeliveryDate, dietParam(state.diet));

  const value = useMemo<CartValue>(
    () => ({
      lines: state.lines,
      requestedDeliveryDate,
      diet: state.diet,
      itemCount: state.lines.length,
      currentQuote: state.lastQuote && state.lastQuote.signature === signature && state.lines.length > 0 ? state.lastQuote : null,
      addLine,
      setQuantity,
      removeLine,
      swapLine,
      setDeliveryDate,
      setDiet,
      setLastQuote,
      clear,
    }),
    [state, requestedDeliveryDate, signature, addLine, setQuantity, removeLine, swapLine, setDeliveryDate, setDiet, setLastQuote, clear],
  );

  return <CartContext.Provider value={value}>{children}</CartContext.Provider>;
}

export function useCart(): CartValue {
  const v = useContext(CartContext);
  if (!v) throw new Error("useCart must be used inside <CartProvider>");
  return v;
}
