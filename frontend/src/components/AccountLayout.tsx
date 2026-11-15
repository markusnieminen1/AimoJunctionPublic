import type { ReactNode } from "react";
import { NavLink } from "react-router-dom";
import { usePendingApprovalsCount } from "../api/hooks";
import { useAuth } from "../auth/AuthContext";
import { useStrings } from "../i18n";
import { CountBadge } from "./Badge";
import { PageHeader, type Crumb } from "./Card";

// Account layouts (DESIGN_VALIO §2.4, §4.6): 200 px sidebar + 24 px gap + content at ≥ 768; below 768 the sidebar becomes
// a full-bleed horizontally scrolling underline-tab strip above the breadcrumb. Pages pass breadcrumb + title.

export type SideItem = { to: string; label: string; end?: boolean; count?: number };

function Sidebar({ items, ariaLabel }: { items: SideItem[]; ariaLabel: string }) {
  return (
    <>
      <nav aria-label={ariaLabel} className="no-scrollbar -mx-4 mb-6 overflow-x-auto md:hidden" data-testid="account-tabs">
        <div className="flex min-w-max border-b-4 border-line px-4">
          {items.map((it) => (
            <NavLink
              key={it.to}
              to={it.to}
              end={it.end}
              className={({ isActive }) =>
                `-mb-1 inline-flex items-center gap-1.5 whitespace-nowrap border-b-4 px-4 py-2 text-[16px] leading-[21px] text-ink ${isActive ? "border-navy font-bold" : "border-transparent"}`
              }
            >
              {it.label}
              <CountBadge count={it.count ?? 0} />
            </NavLink>
          ))}
        </div>
      </nav>
      <nav aria-label={ariaLabel} className="hidden w-[200px] shrink-0 pt-[52px] md:block">
        <ul className="space-y-1">
          {items.map((it) => (
            <li key={it.to}>
              <NavLink
                to={it.to}
                end={it.end}
                className={({ isActive }) =>
                  `flex items-center justify-between gap-2 border-l-4 py-2 pl-4 pr-2 text-[16px] leading-[21px] text-ink hover:underline ${isActive ? "border-navy font-bold" : "border-transparent"}`
                }
              >
                <span className="min-w-0">{it.label}</span>
                <CountBadge count={it.count ?? 0} />
              </NavLink>
            </li>
          ))}
        </ul>
      </nav>
    </>
  );
}

/** Generic account layout: sidebar items + breadcrumb + display h1 + content. */
export function AccountLayout({ items, ariaLabel, breadcrumb, title, subtitle, actions, children }: {
  items: SideItem[];
  ariaLabel: string;
  breadcrumb?: Crumb[];
  title: ReactNode;
  subtitle?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
}) {
  return (
    <div className="md:flex md:gap-6">
      <Sidebar items={items} ariaLabel={ariaLabel} />
      <div className="min-w-0 flex-1">
        <PageHeader breadcrumb={breadcrumb} title={title} subtitle={subtitle} actions={actions} />
        {children}
      </div>
    </div>
  );
}

/** Webshop orders sidebar: Order Management · Approvals (red count) · Cart & quote. */
export function OrdersLayout(props: Omit<Parameters<typeof AccountLayout>[0], "items" | "ariaLabel">) {
  const { isUser } = useAuth();
  const p = useStrings().portal;
  const pending = usePendingApprovalsCount({ enabled: isUser });
  const items: SideItem[] = [
    { to: "/orders", label: p.mega.orderManagement, end: true },
    { to: "/approvals", label: p.mega.approvals, count: pending.data ?? 0 },
    { to: "/cart", label: p.mega.cartQuote },
  ];
  return <AccountLayout items={items} ariaLabel={p.webshopNav.orders} {...props} />;
}

/** My Aimo client company settings sidebar: Company settings · Agents & permissions · Audit & A2A. */
export function SettingsLayout(props: Omit<Parameters<typeof AccountLayout>[0], "items" | "ariaLabel">) {
  const strings = useStrings();
  const p = strings.portal;
  const items: SideItem[] = [
    { to: "/account", label: p.companySettings },
    { to: "/agents", label: p.agentsPermissions },
    { to: "/audit", label: strings.audit.title },
  ];
  return <AccountLayout items={items} ariaLabel={p.settings} {...props} />;
}
