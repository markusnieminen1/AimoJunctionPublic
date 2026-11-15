import type { ReactNode } from "react";
import { Link } from "react-router-dom";

// Underline tabs (DESIGN_VALIO §4.6, My Aimo "Bulletins"): 16 px black, active bold with a 4 px petrol bottom border on a
// 4 px #DEE4E6 track. Horizontally scrollable on phones.

export type TabItem = { id: string; label: ReactNode; to?: string; count?: number };

export function UnderlineTabs({ items, active, onChange, className = "", ariaLabel }: {
  items: TabItem[];
  active: string;
  onChange?: (id: string) => void;
  className?: string;
  ariaLabel?: string;
}) {
  return (
    <div className={`no-scrollbar overflow-x-auto ${className}`}>
      <div role="tablist" aria-label={ariaLabel} className="flex min-w-max border-b-4 border-line">
        {items.map((it) => {
          const on = it.id === active;
          const cls = `-mb-1 inline-flex items-center gap-1.5 whitespace-nowrap border-b-4 px-4 py-2 text-[16px] leading-[21px] ${
            on ? "border-navy font-bold text-ink" : "border-transparent text-ink hover:border-line-strong"
          }`;
          const body = (
            <>
              {it.label}
              {it.count != null && it.count > 0 && (
                <span className="tabular-nums inline-flex h-5 min-w-[20px] items-center justify-center rounded-full bg-red px-1 text-[10px] font-bold text-white">{it.count}</span>
              )}
            </>
          );
          return it.to ? (
            <Link key={it.id} to={it.to} role="tab" aria-selected={on} className={cls}>
              {body}
            </Link>
          ) : (
            <button key={it.id} type="button" role="tab" aria-selected={on} className={cls} onClick={() => onChange?.(it.id)}>
              {body}
            </button>
          );
        })}
      </div>
    </div>
  );
}
