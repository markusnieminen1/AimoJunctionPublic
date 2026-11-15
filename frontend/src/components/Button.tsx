import { forwardRef, type ButtonHTMLAttributes, type ReactNode } from "react";
import { Loader2 } from "lucide-react";
import { Link, type LinkProps } from "react-router-dom";

// Portal buttons (DESIGN_VALIO §4.1). CTAs are flat rectangles (1 px radius, 2 px border) with an UPPERCASE display
// label (National 700, via CSS `uppercase`, so accessible names keep their normal case). Text-style actions use the
// sans font. Legacy variant names stay as aliases so older call sites keep working:
//   secondary → outline, ghost → text, success → lime.
export type ButtonVariant =
  | "primary"
  | "lime"
  | "outline"
  | "text"
  | "danger-text"
  | "danger"
  | "danger-outline"
  | "icon"
  | "icon-outline"
  | "icon-light"
  | "icon-lg"
  | "secondary"
  | "ghost"
  | "success";
export type ButtonSize = "sm" | "md" | "lg";

const CTA = "font-display uppercase tracking-[0.5px] font-bold rounded-cta border-2";
const TEXT = "font-sans font-bold rounded-cta border-0";
const ICON = "rounded-full shrink-0";

const variants: Record<ButtonVariant, string> = {
  primary: `${CTA} border-navy bg-navy text-white hover:border-navy-900 hover:bg-navy-900`,
  lime: `${CTA} border-lime bg-lime text-navy hover:border-lime-hover hover:bg-lime-hover`,
  outline: `${CTA} border-navy bg-transparent text-navy hover:bg-primary-soft`,
  secondary: `${CTA} border-navy bg-white text-navy hover:bg-primary-soft`,
  danger: `${CTA} border-danger bg-danger text-white hover:border-[#A30002] hover:bg-[#A30002]`,
  "danger-outline": `${CTA} border-danger bg-white text-danger hover:bg-danger-bg`,
  success: `${CTA} border-lime bg-lime text-navy hover:border-lime-hover hover:bg-lime-hover`,
  text: `${TEXT} bg-transparent text-navy hover:underline`,
  ghost: `${TEXT} bg-transparent text-navy hover:underline`,
  "danger-text": `${TEXT} bg-transparent text-red-strong hover:underline`,
  icon: `${ICON} border-2 border-navy bg-navy text-white hover:border-navy-900 hover:bg-navy-900`,
  "icon-outline": `${ICON} border-2 border-navy bg-white text-navy hover:bg-primary-soft`,
  "icon-light": `${ICON} border-0 bg-primary-soft text-navy hover:bg-[#E2EBEF]`,
  "icon-lg": `${ICON} border-0 bg-navy text-white hover:bg-navy-900`,
};

const ctaSizes: Record<ButtonSize, string> = {
  sm: "min-h-9 py-1.5 px-4 text-[16px] leading-snug gap-1.5",
  md: "min-h-10 py-2 px-[22px] text-[18px] leading-snug gap-2",
  lg: "min-h-12 py-2.5 px-[30px] text-[18px] leading-snug gap-2",
};
const textSizes: Record<ButtonSize, string> = {
  sm: "min-h-8 px-1 text-[13px] gap-1.5",
  md: "min-h-9 px-1 text-[14px] gap-1.5",
  lg: "min-h-10 px-1 text-[16px] gap-2",
};
const iconSizes: Record<ButtonVariant, string> = {
  icon: "h-10 w-10",
  "icon-outline": "h-10 w-10",
  "icon-light": "h-8 w-8",
  "icon-lg": "h-12 w-12",
} as Record<ButtonVariant, string>;

function sizeClass(variant: ButtonVariant, size: ButtonSize): string {
  if (variant.startsWith("icon")) return iconSizes[variant] ?? "h-10 w-10";
  if (variant === "text" || variant === "ghost" || variant === "danger-text") return textSizes[size];
  return ctaSizes[size];
}

const BASE =
  "inline-flex max-w-full items-center justify-center whitespace-normal text-center sm:whitespace-nowrap transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-link disabled:cursor-not-allowed disabled:opacity-40";

/**
 * Button. variant: primary | lime | outline | text | danger-text | danger | danger-outline | icon | icon-outline |
 * icon-light | icon-lg (+ legacy secondary/ghost/success). size: sm (h36) | md (h40) | lg (h48). `loading` spins + disables.
 * Icon variants render only their icon: pass it as children or `icon`, and always give an `aria-label`.
 */
export const Button = forwardRef<HTMLButtonElement, ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: ButtonVariant;
  size?: ButtonSize;
  loading?: boolean;
  icon?: ReactNode;
}>(function Button({ variant = "primary", size = "md", loading, icon, className = "", children, disabled, type = "button", ...rest }, ref) {
  return (
    <button ref={ref} type={type} disabled={disabled || loading} className={`${BASE} ${variants[variant]} ${sizeClass(variant, size)} ${className}`} {...rest}>
      {loading ? <Loader2 className="h-4 w-4 animate-spin" strokeWidth={1.75} /> : icon}
      {children}
    </button>
  );
});

/** Class string of a Button (for links styled as buttons). */
export function buttonClass(variant: ButtonVariant = "primary", size: ButtonSize = "md", extra = ""): string {
  return `${BASE} ${variants[variant]} ${sizeClass(variant, size)} ${extra}`;
}

/** Router link that looks like a Button (no <button> nested inside <a>). */
export function ButtonLink({ variant = "primary", size = "md", icon, className = "", children, ...rest }: LinkProps & { variant?: ButtonVariant; size?: ButtonSize; icon?: ReactNode }) {
  return (
    <Link className={buttonClass(variant, size, className)} {...rest}>
      {icon}
      {children}
    </Link>
  );
}
