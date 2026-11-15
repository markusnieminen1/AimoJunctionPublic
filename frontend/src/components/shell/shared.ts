// Shell helpers shared by the top bar, header rows, drawer and footer (DESIGN_VALIO §3).
import { useAuth } from "../../auth/AuthContext";
import { useCart } from "../../cart/CartContext";
import { usePendingApprovalsCount } from "../../api/hooks";

export type HeaderVariant = "webshop" | "myaimo";

const MY_AIMO_PREFIXES = ["/assistant", "/agents", "/audit", "/account", "/admin"];

/** Webshop (lavender) for catalog/cart/orders/approvals, My Aimo (petrol-light) for overview/assistant/agents/audit/account. */
export function headerVariantFor(pathname: string): HeaderVariant {
  if (pathname === "/" || pathname === "") return "myaimo";
  return MY_AIMO_PREFIXES.some((p) => pathname === p || pathname.startsWith(`${p}/`)) ? "myaimo" : "webshop";
}

/** Header-row background per variant (the active store tab uses the same colour so it merges into the row). */
export const ROW_BG: Record<HeaderVariant, string> = { webshop: "bg-lavender", myaimo: "bg-primary-soft" };

/** Pages that lay out their own full-bleed bands (hero, AI band, category band) instead of the padded container. */
const FULL_BLEED = new Set(["/", "/assistant", "/catalog"]);
export function isFullBleed(pathname: string): boolean {
  return FULL_BLEED.has(pathname.replace(/\/+$/, "") || "/");
}

export function useShellCounts() {
  const { isUser } = useAuth();
  const { itemCount } = useCart();
  const pending = usePendingApprovalsCount({ enabled: isUser });
  const approvals = pending.data ?? 0;
  // Approvals are hidden only when there is nothing pending *and* this principal cannot approve (agents).
  return { approvals, cart: itemCount, showApprovals: isUser || approvals > 0 };
}

export function firstName(name: string | undefined | null): string {
  return (name ?? "").trim().split(/\s+/)[0] ?? "";
}
