// Mobile drawer (DESIGN_VALIO §3.4): slides in from the left, 85 vw, petrol head with the white logo,
// client groups (Delivery wholesale + My Aimo) plus separated Valio Aimo Internal group, 48 px rows, Log out at the bottom.
import { Link, NavLink, useNavigate } from "react-router-dom";
import { LogOut, Settings, X } from "lucide-react";
import { useAuth } from "../../auth/AuthContext";
import { useI18n } from "../../i18n";
import { AimoWordmark } from "../AimoWordmark";
import { CountBadge } from "../Badge";
import { NewEnvTopButton } from "./NewEnvControl";
import { useShellCounts } from "./shared";

type DrawerItem = {
  to: string;
  label: string;
  end?: boolean;
  count?: number;
  badge?: string;
  testId?: string;
};

export function Drawer({ onClose }: { onClose: () => void }) {
  const { principal, company, isUser, logout } = useAuth();
  const { locale, setLocale, strings } = useI18n();
  const p = strings.portal;
  const d = p.drawer;
  const counts = useShellCounts();
  const navigate = useNavigate();
  const groups: { title: string; items: DrawerItem[] }[] = [
    {
      title: p.drawerWebshop,
      items: [
        { to: "/catalog", label: d.products },
        { to: "/cart", label: d.cartQuote, count: 0 },
        { to: "/orders", label: d.orderManagement },
        { to: "/approvals", label: d.approvals, count: counts.approvals },
        { to: "/assistant", label: d.assistant },
      ],
    },
    {
      title: p.drawerMyAimo,
      items: [
        { to: "/", label: d.overview, end: true },
        { to: "/account", label: d.myInformation },
        { to: "/agents", label: d.agents },
        { to: "/audit", label: d.audit },
      ],
    },
    {
      title: p.drawerInternal,
      items: [
        {
          to: "/admin",
          label: d.admin,
          badge: p.storeTabs.internalBadge,
          testId: "drawer-aimo-admin-link",
        },
      ],
    },
  ];
  return (
    <div className="fixed inset-0 z-50 lg:hidden" role="dialog" aria-modal="true" aria-label={p.menu}>
      <div className="absolute inset-0 bg-[rgba(0,32,42,0.55)]" onClick={onClose} />
      <div className="absolute inset-y-0 left-0 flex w-[85vw] max-w-[360px] flex-col bg-white shadow-popover">
        <div className="flex h-14 shrink-0 items-center justify-between bg-navy px-4">
          <AimoWordmark tone="white" size="sm" />
          <button type="button" onClick={onClose} className="flex h-10 w-10 items-center justify-center text-white" aria-label={p.closeMenu}>
            <X className="h-6 w-6" strokeWidth={1.5} />
          </button>
        </div>
        <div className="flex-1 overflow-y-auto">
          {(principal || company) && (
            <div className="border-b border-line bg-primary-soft px-4 py-3">
              <Link
                to="/account"
                onClick={onClose}
                className="flex items-center justify-between gap-2 rounded border border-navy/20 bg-white px-3 py-2 hover:bg-surface"
              >
                <div className="min-w-0">
                  {company && <p className="truncate text-[14px] font-bold text-navy">{company.name}</p>}
                  <p className="truncate text-[12.5px] text-ink-secondary">{principal?.name}</p>
                </div>
                <Settings className="h-4 w-4 shrink-0 text-navy" strokeWidth={1.75} aria-hidden="true" />
              </Link>
              {isUser && (
                <NewEnvTopButton
                  variant="menu"
                  className="mt-2.5 inline-flex items-center gap-1.5 text-[13px] font-bold text-navy hover:underline"
                />
              )}
            </div>
          )}
          <nav aria-label="Mobile">
            {groups.map((g) => (
              <section key={g.title} className="pt-5">
                <h2 className="label-display px-4 pb-2 text-[18px] leading-[19px] text-navy">{g.title}</h2>
                <ul className="border-t border-line">
                  {g.items.map((it) => (
                    <li key={it.to} className="border-b border-line">
                      <NavLink
                        to={it.to}
                        end={it.end}
                        data-testid={it.testId}
                        onClick={onClose}
                        className={({ isActive }) =>
                          `flex h-12 items-center justify-between gap-2 border-l-4 px-4 text-[16px] text-ink ${isActive ? "border-navy font-bold" : "border-transparent"}`
                        }
                      >
                        <span className="truncate">{it.label}</span>
                        <span className="flex shrink-0 items-center gap-1.5">
                          {it.badge && (
                            <span className="rounded-none bg-lime px-1.5 py-0.5 text-[10px] font-extrabold uppercase tracking-[0.04em] text-navy">
                              {it.badge}
                            </span>
                          )}
                          <CountBadge count={it.count ?? 0} />
                        </span>
                      </NavLink>
                    </li>
                  ))}
                </ul>
              </section>
            ))}
          </nav>
        </div>
        <div className="flex shrink-0 items-center justify-between gap-2 border-t border-line p-2">
          <button
            type="button"
            onClick={() => {
              onClose();
              logout();
              navigate("/login", { replace: true });
            }}
            className="flex h-12 items-center gap-2 px-2 text-[16px] font-bold text-navy"
          >
            <LogOut className="h-5 w-5" strokeWidth={1.5} />
            {p.logout}
          </button>
          <div className="flex items-center gap-1 pr-2" role="group" aria-label="Language">
            {(["en", "fi"] as const).map((code) => (
              <button
                key={code}
                type="button"
                aria-pressed={locale === code}
                onClick={() => setLocale(code)}
                className={`rounded px-2.5 py-1.5 text-[13px] font-bold uppercase ${
                  locale === code ? "bg-navy text-white" : "border border-line text-navy hover:bg-primary-soft"
                }`}
              >
                {code}
              </button>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
