import { useEffect, type ReactNode } from "react";
import { X } from "lucide-react";

/**
 * Accessible modal dialog (DESIGN_VALIO §4.9): petrol-tinted overlay, white 4 px panel, display-font title.
 * Phones get a full-screen sheet with a sticky footer. Footer children are laid out primary-first: pass them as
 * [cancel, confirm] (as before) and the confirm button renders on the left.
 * Closes on Esc / backdrop click unless `dismissible=false` (e.g. one-time secret).
 */
export function Modal({ open, onClose, title, children, footer, size = "md", dismissible = true }: {
  open: boolean;
  onClose: () => void;
  title: ReactNode;
  children: ReactNode;
  footer?: ReactNode;
  size?: "sm" | "md" | "lg";
  dismissible?: boolean;
}) {
  useEffect(() => {
    if (!open || !dismissible) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, dismissible, onClose]);
  if (!open) return null;
  const width = size === "sm" ? "md:max-w-[480px]" : size === "lg" ? "md:max-w-[700px]" : "md:max-w-[560px]";
  return (
    <div className="fixed inset-0 z-50 flex items-stretch justify-center bg-[rgba(0,32,42,0.55)] md:items-center md:p-4" onMouseDown={() => dismissible && onClose()}>
      <div
        role="dialog"
        aria-modal="true"
        className={`flex h-full w-full ${width} flex-col bg-white shadow-popover md:h-auto md:max-h-[92vh] md:rounded`}
        onMouseDown={(e) => e.stopPropagation()}
      >
        <div className="flex items-start justify-between gap-3 px-5 pb-2 pt-5 md:px-8 md:pt-8">
          <h2 className="font-display text-[28px] leading-[32px] tracking-[0.01em] text-navy md:text-[32px] md:leading-[35px]">{title}</h2>
          {dismissible && (
            <button type="button" onClick={onClose} className="-mr-1 shrink-0 rounded-full p-1 text-navy hover:bg-primary-soft" aria-label="Close">
              <X className="h-6 w-6" strokeWidth={1.5} />
            </button>
          )}
        </div>
        <div className="flex-1 overflow-y-auto px-5 pb-5 pt-2 md:px-8 md:pb-6">{children}</div>
        {footer && (
          <div className="sticky bottom-0 flex flex-row-reverse flex-wrap justify-end gap-3 border-t border-line bg-white px-5 py-4 md:border-t-0 md:px-8 md:pb-8 md:pt-0">{footer}</div>
        )}
      </div>
    </div>
  );
}
