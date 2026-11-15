// 80 px desktop header row (≥ 992) and 56 px mobile header row (< 992), DESIGN_VALIO §3.3–3.4.
// Webshop: logo, PRODUCTS / ORDERS mega menus + ASSISTANT, search, Approvals / Delivery date / Cart.
// My Aimo: logo, OVERVIEW / A.I. ASSISTANT / AGENTS & PERMISSIONS / AUDIT & A2A, Approvals / Cart.
import { useEffect, useRef, useState, type FormEvent, type ReactNode } from "react";
import { Link, NavLink, useLocation, useNavigate } from "react-router-dom";
import { Bell, CalendarDays, ChevronDown, Menu, Search, ShoppingCart } from "lucide-react";
import { useCategories } from "../../api/hooks";
import { useCart } from "../../cart/CartContext";
import { useStrings } from "../../i18n";
import { formatDate, formatShortDate } from "../../lib/dates";
import { formatEur, formatNumber } from "../../lib/format";
import { AimoWordmark } from "../AimoWordmark";
import { CountBadge } from "../Badge";
import { ROW_BG, useShellCounts, type HeaderVariant } from "./shared";

const navText = "label-display text-[18px] leading-[19px]";

function NavUnderline({ active, children }: { active: boolean; children: ReactNode }) {
  return <span className={`border-b-2 pb-1 pt-1 ${active ? "border-navy" : "border-transparent group-hover:border-navy"}`}>{children}</span>;
}

// ---------- mega menus ----------

function useOutside(open: boolean, close: () => void) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => ref.current && !ref.current.contains(e.target as Node) && close();
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && close();
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open, close]);
  return ref;
}

const megaLink = "block py-1.5 text-[16px] leading-[21px] text-ink hover:text-link hover:underline";
const megaHead = "label-display mb-3 text-[18px] leading-[19px] text-navy";

function ProductsMega({ onNavigate }: { onNavigate: () => void }) {
  const p = useStrings().portal;
  const cats = useCategories({ silent: true });
  const top = [...(cats.data?.items ?? [])].sort((a, b) => b.product_count - a.product_count).slice(0, 12);
  return (
    <div className="grid gap-8 md:grid-cols-[240px_minmax(0,1fr)]">
      <div>
        <h3 className={megaHead}>{p.mega.diets}</h3>
        <ul>
          {p.dietLinks.map((d) => (
            <li key={d.code}>
              <Link to={`/catalog?diet=${d.code}`} className={megaLink} onClick={onNavigate}>
                {d.label}
              </Link>
            </li>
          ))}
        </ul>
      </div>
      <div>
        <h3 className={megaHead}>{p.mega.categories}</h3>
        <ul className="grid grid-cols-2 gap-x-8 xl:grid-cols-3">
          {top.map((c) => (
            <li key={c.code}>
              <Link to={`/catalog?category=${encodeURIComponent(c.code)}`} className={megaLink} onClick={onNavigate}>
                {c.name} <span className="tabular-nums text-[14px] text-ink-muted">{formatNumber(c.product_count)}</span>
              </Link>
            </li>
          ))}
        </ul>
        <Link to="/catalog" className="mt-4 inline-block text-[16px] font-bold text-link hover:underline" onClick={onNavigate}>
          {p.mega.wholeCatalog}
        </Link>
      </div>
    </div>
  );
}

function OrdersMega({ onNavigate, approvals }: { onNavigate: () => void; approvals: number }) {
  const p = useStrings().portal;
  return (
    <ul className="max-w-sm">
      <li>
        <Link to="/orders" className={megaLink} onClick={onNavigate}>
          {p.mega.orderManagement}
        </Link>
      </li>
      <li>
        <Link to="/approvals" className={`${megaLink} flex items-center gap-2`} onClick={onNavigate}>
          {p.mega.approvals}
          <CountBadge count={approvals} />
        </Link>
      </li>
      <li>
        <Link to="/cart" className={megaLink} onClick={onNavigate}>
          {p.mega.cartQuote}
        </Link>
      </li>
    </ul>
  );
}

function MegaItem({ id, label, active, open, setOpen, children }: {
  id: string;
  label: string;
  active: boolean;
  open: string | null;
  setOpen: (v: string | null) => void;
  children: ReactNode;
}) {
  const isOpen = open === id;
  return (
    <li className="flex h-full items-center" onMouseEnter={() => setOpen(id)} onMouseLeave={() => setOpen(null)}>
      <button
        type="button"
        aria-expanded={isOpen}
        aria-controls={`mega-${id}`}
        onClick={() => setOpen(isOpen ? null : id)}
        className={`group flex h-full items-center gap-1 text-navy ${navText}`}
      >
        <NavUnderline active={active || isOpen}>{label}</NavUnderline>
        <ChevronDown className={`h-3.5 w-3.5 transition-transform ${isOpen ? "rotate-180" : ""}`} strokeWidth={2} aria-hidden="true" />
      </button>
      {isOpen && (
        <div id={`mega-${id}`} className="absolute inset-x-0 top-full z-40 bg-white shadow-popover">
          <div className="container-portal py-8">{children}</div>
        </div>
      )}
    </li>
  );
}

// ---------- search ----------

export function HeaderSearch({ className = "", autoFocus = false, onDone }: { className?: string; autoFocus?: boolean; onDone?: () => void }) {
  const p = useStrings().portal;
  const navigate = useNavigate();
  const loc = useLocation();
  const [q, setQ] = useState(() => new URLSearchParams(loc.search).get("q") ?? "");
  const submit = (e: FormEvent) => {
    e.preventDefault();
    const term = q.trim();
    navigate(term ? `/catalog?q=${encodeURIComponent(term)}` : "/catalog");
    onDone?.();
  };
  return (
    <form onSubmit={submit} role="search" className={className}>
      <label className="relative block">
        <span className="sr-only">{p.searchLabel}</span>
        <Search className="pointer-events-none absolute left-5 top-1/2 h-4 w-4 -translate-y-1/2 text-navy" strokeWidth={1.75} />
        <input
          value={q}
          autoFocus={autoFocus}
          onChange={(e) => setQ(e.target.value)}
          placeholder={p.searchPlaceholder}
          className="h-[49px] w-full rounded bg-white pl-[51px] pr-4 text-[14px] text-ink placeholder:text-line-strong focus:outline-none focus:ring-2 focus:ring-link"
        />
      </label>
    </form>
  );
}

// ---------- right cluster ----------

function ClusterItem({ to, icon, badge, line1, line2, ariaLabel, compactLabels, testId }: {
  to: string;
  icon: ReactNode;
  badge?: number;
  line1: string;
  line2: ReactNode;
  ariaLabel: string;
  compactLabels: boolean;
  testId: string;
}) {
  return (
    <Link to={to} aria-label={ariaLabel} className="group flex shrink-0 items-center gap-2.5 text-navy" data-testid={testId}>
      <span className="relative flex h-10 w-10 items-center justify-center">
        {icon}
        {badge ? (
          <span className="absolute -right-0.5 -top-0.5">
            <CountBadge count={badge} />
          </span>
        ) : null}
      </span>
      <span className={`flex-col text-[14px] leading-[17px] ${compactLabels ? "hidden xl:flex" : "flex"}`}>
        <span className="whitespace-nowrap text-navy group-hover:underline">{line1}</span>
        <span className="tabular-nums whitespace-nowrap font-bold text-navy">{line2}</span>
      </span>
    </Link>
  );
}

const bigIcon = "h-7 w-7";

function RightCluster({ variant }: { variant: HeaderVariant }) {
  const p = useStrings().portal;
  const counts = useShellCounts();
  const cart = useCart();
  const subtotal = cart.currentQuote ? formatEur(cart.currentQuote.total_excl_vat_eur) : "—";
  return (
    <div className="flex shrink-0 items-center gap-4 xl:gap-6">
      {counts.showApprovals && (
        <ClusterItem
          testId="header-approvals"
          to="/approvals"
          icon={<Bell className={bigIcon} strokeWidth={1.5} />}
          badge={counts.approvals}
          line1={p.approvalsLabel}
          line2={p.approvalsPending(counts.approvals)}
          ariaLabel={p.approvalsAria(counts.approvals)}
          compactLabels
        />
      )}
      {variant === "webshop" && (
        <ClusterItem
          testId="header-delivery"
          to="/cart"
          icon={<CalendarDays className={bigIcon} strokeWidth={1.5} />}
          line1={p.deliveryDate}
          line2={formatDate(cart.requestedDeliveryDate)}
          ariaLabel={`${p.deliveryDate}: ${formatDate(cart.requestedDeliveryDate)}`}
          compactLabels
        />
      )}
      <ClusterItem
        testId="header-cart"
        to="/cart"
        icon={<ShoppingCart className={bigIcon} strokeWidth={1.5} />}
        badge={counts.cart}
        line1={p.cart}
        line2={subtotal}
        ariaLabel={p.cartAria(counts.cart, subtotal)}
        compactLabels={false}
      />
    </div>
  );
}

// ---------- desktop row ----------

function WebshopNav({ approvals }: { approvals: number }) {
  const p = useStrings().portal;
  const loc = useLocation();
  const [open, setOpen] = useState<string | null>(null);
  const close = () => setOpen(null);
  const ref = useOutside(open != null, close);
  useEffect(() => setOpen(null), [loc.pathname, loc.search]);
  const path = loc.pathname;
  return (
    <div ref={ref} className="contents">
      <nav aria-label="Main" className="h-full">
        <ul className="flex h-full items-center gap-6">
          <MegaItem id="products" label={p.webshopNav.products} active={path.startsWith("/catalog") || path.startsWith("/products")} open={open} setOpen={setOpen}>
            <ProductsMega onNavigate={close} />
          </MegaItem>
          <MegaItem id="orders" label={p.webshopNav.orders} active={["/orders", "/approvals", "/cart"].some((x) => path.startsWith(x))} open={open} setOpen={setOpen}>
            <OrdersMega onNavigate={close} approvals={approvals} />
          </MegaItem>
          <li className="flex h-full items-center">
            <NavLink to="/assistant" className={`group text-navy ${navText}`}>
              {({ isActive }) => <NavUnderline active={isActive}>{p.webshopNav.assistant}</NavUnderline>}
            </NavLink>
          </li>
        </ul>
      </nav>
    </div>
  );
}

function MyAimoNav() {
  const p = useStrings().portal;
  const items = [
    { to: "/", label: p.myAimoNav.overview, end: true },
    { to: "/assistant", label: p.myAimoNav.assistant },
    { to: "/agents", label: p.myAimoNav.agents },
    { to: "/audit", label: p.myAimoNav.audit },
  ];
  return (
    <nav aria-label="Main" className="h-full min-w-0">
      <ul className="flex h-full items-center gap-5 xl:gap-6">
        {items.map((i) => (
          <li key={i.to} className="flex h-full items-center">
            <NavLink to={i.to} end={i.end} className={`group whitespace-nowrap text-navy ${navText}`}>
              {({ isActive }) => <NavUnderline active={isActive}>{i.label}</NavUnderline>}
            </NavLink>
          </li>
        ))}
      </ul>
    </nav>
  );
}

export function DesktopHeaderRow({ variant }: { variant: HeaderVariant }) {
  const counts = useShellCounts();
  return (
    <div className={`relative hidden h-20 lg:block ${ROW_BG[variant]}`} data-testid="header-row" data-variant={variant}>
      <div className="container-portal flex h-20 items-center gap-6 xl:gap-12">
        <Link to="/" className="shrink-0" aria-label="Aimo Annos home">
          <AimoWordmark />
        </Link>
        {variant === "webshop" ? <WebshopNav approvals={counts.approvals} /> : <MyAimoNav />}
        {variant === "webshop" ? <HeaderSearch className="min-w-[160px] max-w-[550px] flex-1" /> : <div className="flex-1" />}
        <RightCluster variant={variant} />
      </div>
    </div>
  );
}

// ---------- mobile row ----------

export function MobileHeaderRow({ variant, onMenu, searchOpen, onToggleSearch }: {
  variant: HeaderVariant;
  onMenu: () => void;
  searchOpen: boolean;
  onToggleSearch: () => void;
}) {
  const p = useStrings().portal;
  const counts = useShellCounts();
  const cart = useCart();
  const btn = "relative flex h-10 w-10 shrink-0 items-center justify-center text-navy";
  return (
    <div className={`lg:hidden ${ROW_BG[variant]}`} data-testid="mobile-header-row" data-variant={variant}>
      <div className="flex h-14 items-center px-2 md:px-6">
        <button type="button" className={btn} onClick={onMenu} aria-label={p.menu}>
          <Menu className="h-6 w-6" strokeWidth={1.5} />
        </button>
        <Link to="/" className="ml-1 shrink-0" aria-label="Aimo Annos home">
          <AimoWordmark size="sm" />
        </Link>
        <div className="flex-1" />
        <button type="button" className={btn} onClick={onToggleSearch} aria-label={p.openSearch} aria-expanded={searchOpen}>
          <Search className="h-6 w-6" strokeWidth={1.5} />
        </button>
        {counts.showApprovals && (
          <Link to="/approvals" className={btn} aria-label={p.approvalsAria(counts.approvals)} data-testid="mobile-approvals">
            <Bell className="h-6 w-6" strokeWidth={1.5} />
            {counts.approvals > 0 && (
              <span className="absolute right-0 top-0">
                <CountBadge count={counts.approvals} />
              </span>
            )}
          </Link>
        )}
        {variant === "webshop" && (
          <Link to="/cart" className="flex h-10 shrink-0 items-center gap-1 px-1 text-navy" aria-label={`${p.deliveryDate}: ${formatDate(cart.requestedDeliveryDate)}`}>
            <CalendarDays className="h-6 w-6" strokeWidth={1.5} />
            <span className="tabular-nums text-[12px] font-bold">{formatShortDate(cart.requestedDeliveryDate)}</span>
          </Link>
        )}
        <Link to="/cart" className={btn} aria-label={p.cartAria(counts.cart, cart.currentQuote ? formatEur(cart.currentQuote.total_excl_vat_eur) : "—")}>
          <ShoppingCart className="h-6 w-6" strokeWidth={1.5} />
          {counts.cart > 0 && (
            <span className="absolute right-0 top-0">
              <CountBadge count={counts.cart} />
            </span>
          )}
        </Link>
      </div>
      {searchOpen && (
        <div className="border-t border-white/60 px-4 pb-3 pt-2">
          <HeaderSearch autoFocus onDone={onToggleSearch} />
        </div>
      )}
    </div>
  );
}
