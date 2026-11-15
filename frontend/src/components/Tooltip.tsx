import { useState, type ReactNode } from "react";

/** Hover/focus/tap tooltip. Wraps `children`; `content` shows in a small popover above. */
export function Tooltip({ content, children, className = "" }: { content: ReactNode; children: ReactNode; className?: string }) {
  const [open, setOpen] = useState(false);
  return (
    <span
      className={`relative inline-flex ${className}`}
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => setOpen(false)}
      onFocus={() => setOpen(true)}
      onBlur={() => setOpen(false)}
      onClick={() => setOpen((o) => !o)}
    >
      {children}
      {open && (
        <span
          role="tooltip"
          className="absolute bottom-full left-1/2 z-30 mb-1.5 w-max max-w-[260px] -translate-x-1/2 rounded border border-line bg-white px-3 py-2 text-left font-sans text-[13px] font-normal normal-case leading-[18px] tracking-normal text-ink shadow-popover"
        >
          {content}
        </span>
      )}
    </span>
  );
}
