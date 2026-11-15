import type { HTMLAttributes, ReactNode } from "react";
import { Link } from "react-router-dom";
import { ChevronRight } from "lucide-react";

/** Bordered box (1 px #DEE4E6, 4 px radius, no shadow). `padded=false` for tables that run edge to edge. */
export function Card({ className = "", padded = true, children, ...rest }: HTMLAttributes<HTMLDivElement> & { padded?: boolean }) {
  return (
    <div className={`rounded border border-line bg-white ${padded ? "p-4 md:p-5" : ""} ${className}`} {...rest}>
      {children}
    </div>
  );
}

/** Box header row: bold petrol h2 (sans 18), optional description and right-side actions. */
export function CardHeader({ title, description, actions, className = "" }: { title: ReactNode; description?: ReactNode; actions?: ReactNode; className?: string }) {
  return (
    <div className={`flex flex-wrap items-start justify-between gap-2 ${className}`}>
      <div className="min-w-0">
        <h2 className="text-[18px] font-bold leading-[22px] text-navy">{title}</h2>
        {description && <p className="mt-1 text-[14px] leading-5 text-ink-secondary">{description}</p>}
      </div>
      {actions && <div className="flex max-w-full flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}

export type Crumb = { label: ReactNode; to?: string };

/** Portal breadcrumb: "Front page › Products", 12 px bold, links #0D637D, current black. */
export function Breadcrumb({ items, className = "" }: { items: Crumb[]; className?: string }) {
  return (
    <nav aria-label="Breadcrumb" className={`min-w-0 ${className}`}>
      <ol className="flex flex-wrap items-center gap-x-1 text-[12px] font-bold leading-6">
        {items.map((c, i) => {
          const last = i === items.length - 1;
          return (
            <li key={i} className="flex min-w-0 items-center gap-1">
              {c.to && !last ? (
                <Link to={c.to} className="truncate text-link hover:underline">
                  {c.label}
                </Link>
              ) : (
                <span className="truncate text-ink" aria-current={last ? "page" : undefined}>
                  {c.label}
                </span>
              )}
              {!last && <ChevronRight className="h-3 w-3 shrink-0 text-line-strong" strokeWidth={2} aria-hidden="true" />}
            </li>
          );
        })}
      </ol>
    </nav>
  );
}

/** Page title block: optional breadcrumb, display h1 (56 px petrol, 40 px on phones), subtitle and actions. */
export function PageHeader({ title, subtitle, actions, breadcrumb }: { title: ReactNode; subtitle?: ReactNode; actions?: ReactNode; breadcrumb?: Crumb[] }) {
  return (
    <div className="mb-6 md:mb-8">
      {breadcrumb && <Breadcrumb items={breadcrumb} className="mb-3 md:mb-4" />}
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div className="min-w-0">
          <h1 className="h-page">{title}</h1>
          {subtitle && <p className="mt-2 max-w-[760px] text-[16px] leading-[21px] text-ink">{subtitle}</p>}
        </div>
        {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
      </div>
    </div>
  );
}
