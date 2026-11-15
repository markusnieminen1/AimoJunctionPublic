import type { ReactNode } from "react";
import { AlertCircle, AlertTriangle, CheckCircle2, Info } from "lucide-react";

// Portal notification stripes (DESIGN_VALIO §4.3): warning/info yellow, error rose, success green, neutral petrol-light.
export type StripeKind = "warning" | "error" | "success" | "neutral";

const KIND: Record<StripeKind, { box: string; border: string; icon: ReactNode }> = {
  warning: { box: "bg-yellow-soft", border: "border-l-warning", icon: <AlertCircle className="h-5 w-5 text-red-strong" strokeWidth={1.75} /> },
  error: { box: "bg-red-soft", border: "border-l-danger", icon: <AlertTriangle className="h-5 w-5 text-red-strong" strokeWidth={1.75} /> },
  success: { box: "bg-success-bg", border: "border-l-success", icon: <CheckCircle2 className="h-5 w-5 text-success" strokeWidth={1.75} /> },
  neutral: { box: "bg-primary-soft", border: "border-l-navy", icon: <Info className="h-5 w-5 text-navy" strokeWidth={1.75} /> },
};

/**
 * Notification stripe. `accent` adds the 4 px left border used by policy banners and the fired-rules box.
 * `compact` = 8/16 px padding. `icon={null}` hides the icon; pass any node to replace it.
 */
export function Stripe({ kind = "neutral", title, children, actions, accent = false, compact = false, icon, role, className = "", ...rest }: {
  kind?: StripeKind;
  title?: ReactNode;
  children?: ReactNode;
  actions?: ReactNode;
  accent?: boolean;
  compact?: boolean;
  icon?: ReactNode | null;
  role?: string;
  className?: string;
  "data-testid"?: string;
}) {
  const k = KIND[kind];
  return (
    <div
      role={role}
      className={`flex items-start gap-4 rounded-[3px] text-ink ${k.box} ${accent ? `border-l-4 ${k.border}` : ""} ${compact ? "px-4 py-2" : "px-4 py-3 md:px-6 md:py-4"} ${className}`}
      {...rest}
    >
      {icon !== null && <span className="mt-0.5 shrink-0">{icon ?? k.icon}</span>}
      <div className="min-w-0 flex-1 text-[14px] leading-5 md:text-[15px] md:leading-6">
        {title && <div className="font-bold">{title}</div>}
        {children}
      </div>
      {actions && <div className="flex shrink-0 items-center gap-2 self-center">{actions}</div>}
    </div>
  );
}
