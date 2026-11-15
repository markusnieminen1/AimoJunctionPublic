import type { ButtonHTMLAttributes, ReactNode } from "react";
import { formatNumber } from "../lib/format";

// Portal pills (DESIGN_VALIO §4.6). Round shapes are reserved for pills, icon buttons and count badges.

/** Filter pill ("Not paid 1"): petrol-light, h 36, bold 14 petrol, grey count. Active = petrol with white text. */
export function FilterPill({ active = false, count, children, className = "", ...rest }: ButtonHTMLAttributes<HTMLButtonElement> & { active?: boolean; count?: number | null }) {
  return (
    <button
      type="button"
      aria-pressed={active}
      className={`inline-flex h-9 shrink-0 items-center gap-1.5 whitespace-nowrap rounded-full px-4 text-[14px] font-bold transition-colors disabled:opacity-40 ${
        active ? "bg-navy text-white" : "bg-primary-soft text-navy hover:bg-[#E2EBEF]"
      } ${className}`}
      {...rest}
    >
      {children}
      {count != null && <span className={`tabular-nums font-normal ${active ? "text-white/80" : "text-ink-muted"}`}>{formatNumber(count)}</span>}
    </button>
  );
}

/** Category pill ("Fruits and Vegetables 657"): white pill on a petrol-light band, h 32, bold 14 petrol. */
export function CategoryPill({ active = false, count, children, className = "", ...rest }: ButtonHTMLAttributes<HTMLButtonElement> & { active?: boolean; count?: number | null }) {
  return (
    <button
      type="button"
      aria-pressed={active}
      className={`inline-flex h-8 shrink-0 items-center gap-2 whitespace-nowrap rounded-full border-2 px-4 text-[14px] font-bold transition-colors ${
        active ? "border-navy bg-navy text-white" : "border-white bg-white text-navy hover:border-navy"
      } ${className}`}
      {...rest}
    >
      {children}
      {count != null && <span className={`tabular-nums font-normal ${active ? "text-white/80" : "text-ink-muted"}`}>{formatNumber(count)}</span>}
    </button>
  );
}

/** Example/suggestion pill (Ask A.I. "Examples:"): white, radius 20, 14 px petrol. */
export function SuggestionPill({ children, className = "", ...rest }: ButtonHTMLAttributes<HTMLButtonElement> & { children: ReactNode }) {
  return (
    <button
      type="button"
      className={`max-w-full rounded-[20px] bg-white px-3 py-1 text-left text-[14px] leading-5 text-navy transition-colors hover:underline disabled:cursor-not-allowed disabled:opacity-50 ${className}`}
      {...rest}
    >
      {children}
    </button>
  );
}
