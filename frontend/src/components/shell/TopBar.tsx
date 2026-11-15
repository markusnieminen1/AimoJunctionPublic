// 40 px petrol portal switcher (DESIGN_VALIO §3.2).
// Real internal routes ONLY (/catalog, /, and Valio Aimo Internal /admin) — 0 external Valio links & 0 placeholder tabs.
import { useEffect, useRef, useState, type ComponentType } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import {
  Bot,
  Check,
  ChevronDown,
  ClipboardList,
  LineChart,
  LogOut,
  ScrollText,
  Settings,
  SlidersHorizontal,
  Truck,
  UserCog,
  type LucideProps,
} from "lucide-react";
import { useAuth } from "../../auth/AuthContext";
import { useI18n, useStrings, type Locale } from "../../i18n";
import { JunctionEditionBadge } from "../JunctionEditionBadge";
import { NewEnvTopButton } from "./NewEnvControl";
import { ROW_BG, type HeaderVariant } from "./shared";

type Tab = {
  key: string;
  label: string;
  icon: ComponentType<LucideProps>;
  to: string;
  active: boolean;
  badge?: string;
  testId?: string;
};

function StoreTabs({ variant }: { variant: HeaderVariant }) {
  const p = useStrings().portal;
  const loc = useLocation();
  const onAdmin = loc.pathname === "/admin" || loc.pathname.startsWith("/admin/");
  const tabs: Tab[] = [
    {
      key: "delivery",
      label: p.storeTabs.delivery,
      icon: Truck,
      to: "/catalog",
      active: !onAdmin && variant === "webshop",
    },
    {
      key: "myaimo",
      label: p.storeTabs.myAimo,
      icon: LineChart,
      to: "/",
      active: !onAdmin && variant === "myaimo",
    },
    {
      key: "aimo-admin",
      label: p.storeTabs.aimoAdmin,
      icon: SlidersHorizontal,
      to: "/admin",
      active: onAdmin,
      badge: p.storeTabs.internalBadge,
      testId: "topbar-aimo-admin-link",
    },
  ];
  return (
    <ul className="flex min-w-0 items-end gap-0 self-end overflow-hidden" aria-label={p.storeTabsLabel} data-testid="store-tabs">
      {tabs.map((t) => {
        const Icon = t.icon;
        const base = "flex h-8 items-center gap-1.5 whitespace-nowrap px-2 text-[14px] font-bold leading-none lg:px-3";
        return (
          <li key={t.key}>
            <Link
              to={t.to}
              data-testid={t.testId}
              aria-current={t.active ? "page" : undefined}
              aria-label={t.active ? undefined : t.label}
              className={`${base} rounded-t ${t.active ? `${ROW_BG[variant]} text-navy` : "text-white hover:underline"}`}
            >
              <Icon className="h-4 w-4 shrink-0" strokeWidth={1.75} aria-hidden="true" />
              <span className={t.active ? "" : "hidden lg:inline"}>{t.label}</span>
              {t.badge && (
                <span className="hidden rounded-none bg-lime px-1.5 py-0.5 text-[9.5px] font-extrabold uppercase leading-none tracking-[0.05em] text-navy sm:inline">
                  {t.badge}
                </span>
              )}
            </Link>
          </li>
        );
      })}
    </ul>
  );
}

export function UserMenu() {
  const { principal, company, logout } = useAuth();
  const p = useStrings().portal;
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const navigate = useNavigate();
  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => ref.current && !ref.current.contains(e.target as Node) && setOpen(false);
    const esc = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", esc);
    return () => {
      document.removeEventListener("mousedown", close);
      document.removeEventListener("keydown", esc);
    };
  }, [open]);
  const item = "flex w-full items-center gap-2.5 px-4 py-2 text-left text-[14px] text-ink hover:bg-primary-soft";
  const companyMeta = [company?.city, company?.business_id].filter(Boolean).join(" · ");

  return (
    <div className="relative" ref={ref}>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="flex h-8 items-center gap-1.5 rounded border border-white/25 bg-white/10 px-2 text-[13px] text-white transition-colors hover:bg-white/20"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={p.userMenu}
        data-testid="company-profile-menu-trigger"
      >
        <Settings className="h-3.5 w-3.5 shrink-0 text-lime" strokeWidth={1.75} aria-hidden="true" />
        <span className="hidden max-w-[150px] truncate font-bold text-white sm:inline">
          {company?.name ?? principal?.name}
        </span>
        {company && principal && (
          <span className="hidden max-w-[130px] truncate text-white/85 xl:inline">
            · {principal.name}
          </span>
        )}
        <ChevronDown className="h-3.5 w-3.5 shrink-0" strokeWidth={1.75} aria-hidden="true" />
      </button>
      {open && (
        <div
          role="menu"
          data-testid="company-profile-menu"
          className="absolute right-0 z-50 mt-1.5 w-[min(296px,calc(100vw-2rem))] rounded bg-white py-2 shadow-popover"
        >
          <div className="border-b border-line px-4 pb-2.5">
            <p className="truncate text-[14px] font-bold text-navy">{company?.name ?? principal?.kind}</p>
            {companyMeta && <p className="truncate text-[11.5px] text-ink-muted">{companyMeta}</p>}
            <p className="mt-0.5 truncate text-[13px] font-medium text-ink">
              {principal?.name}
              {principal?.email ? ` (${principal.email})` : ""}
            </p>
          </div>

          <Link role="menuitem" to="/account" data-testid="menu-company-settings" className={item} onClick={() => setOpen(false)}>
            <UserCog className="h-4 w-4 shrink-0 text-navy" strokeWidth={1.5} />
            <span>{p.myInformation}</span>
          </Link>
          <Link role="menuitem" to="/agents" data-testid="menu-agent-settings" className={item} onClick={() => setOpen(false)}>
            <Bot className="h-4 w-4 shrink-0 text-navy" strokeWidth={1.5} />
            <span>{p.agentSettingsMenu}</span>
          </Link>
          <Link role="menuitem" to="/orders" data-testid="menu-orders-approvals" className={item} onClick={() => setOpen(false)}>
            <ClipboardList className="h-4 w-4 shrink-0 text-navy" strokeWidth={1.5} />
            <span>{p.ordersApprovalsMenu}</span>
          </Link>
          <Link role="menuitem" to="/audit" data-testid="menu-audit-a2a" className={item} onClick={() => setOpen(false)}>
            <ScrollText className="h-4 w-4 shrink-0 text-navy" strokeWidth={1.5} />
            <span>{p.auditA2aMenu}</span>
          </Link>

          <div className="my-1.5 border-t border-line pt-1.5">
            <p className="px-4 pb-1 text-[10.5px] font-extrabold uppercase tracking-[0.06em] text-navy">
              {p.aimoInternalMenuHeading}
            </p>
            <Link role="menuitem" to="/admin" data-testid="menu-aimo-admin" className={item} onClick={() => setOpen(false)}>
              <SlidersHorizontal className="h-4 w-4 shrink-0 text-navy" strokeWidth={1.5} />
              <span>{p.admin}</span>
            </Link>
          </div>

          <div className="mt-1 border-t border-line pt-1">
            <button
              type="button"
              role="menuitem"
              data-testid="menu-logout"
              className={item}
              onClick={() => {
                setOpen(false);
                logout();
                navigate("/login", { replace: true });
              }}
            >
              <LogOut className="h-4 w-4 shrink-0 text-navy" strokeWidth={1.5} />
              <span>{p.logout}</span>
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

const LANGUAGES: { code: Locale; label: string }[] = [
  { code: "en", label: "English (EN)" },
  { code: "fi", label: "Suomi (FI)" },
];

function LanguageMenu() {
  const { locale, setLocale, strings } = useI18n();
  const p = strings.portal;
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => ref.current && !ref.current.contains(e.target as Node) && setOpen(false);
    const esc = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", esc);
    return () => {
      document.removeEventListener("mousedown", close);
      document.removeEventListener("keydown", esc);
    };
  }, [open]);

  return (
    <div className="relative hidden lg:block" ref={ref}>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="flex h-10 items-center gap-1 text-[14px] uppercase text-topbar-muted hover:text-white"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={locale === "fi" ? "Kieli: Suomi" : "Language: English"}
        data-testid="language-switcher"
      >
        {p.language}
        <ChevronDown className="h-3.5 w-3.5" strokeWidth={1.75} />
      </button>
      {open && (
        <div role="menu" className="absolute right-0 z-50 mt-1 w-40 rounded bg-white py-1.5 shadow-popover">
          {LANGUAGES.map((lang) => (
            <button
              key={lang.code}
              type="button"
              role="menuitemradio"
              aria-checked={locale === lang.code}
              onClick={() => {
                setLocale(lang.code);
                setOpen(false);
              }}
              className="flex w-full items-center justify-between px-3.5 py-2 text-left text-[14px] text-ink hover:bg-primary-soft"
            >
              <span>{lang.label}</span>
              {locale === lang.code && <Check className="h-4 w-4 text-navy" strokeWidth={2} />}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

/** The petrol switcher bar. `signedIn=false` (login/register) shows only tabs + language. */
export function TopBar({ variant, signedIn = true }: { variant: HeaderVariant; signedIn?: boolean }) {
  const { isUser } = useAuth();
  const p = useStrings().portal;
  const mocks = import.meta.env.VITE_USE_MOCKS === "true";
  return (
    <div className="h-10 bg-navy text-white" data-testid="topbar">
      <div className="container-portal flex h-10 items-stretch justify-between gap-2">
        <StoreTabs variant={variant} />
        <div className="flex shrink-0 items-center gap-2.5 lg:gap-4">
          <span className="hidden sm:inline-flex">
            <JunctionEditionBadge size="sm" testId="junction-edition-topbar" />
          </span>
          {mocks && (
            <span title={p.mockModeTitle} className="label-display hidden rounded-sm bg-yellow px-1.5 py-0.5 text-[13px] leading-none text-black xl:inline">
              {p.mockMode}
            </span>
          )}
          {signedIn && isUser && <NewEnvTopButton variant="topbar" />}
          {signedIn && <UserMenu />}
          <LanguageMenu />
        </div>
      </div>
    </div>
  );
}
