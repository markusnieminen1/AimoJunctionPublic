import type { Config } from "tailwindcss";

// Colors map to the CSS variables in src/styles/tokens.css (DESIGN_VALIO.md §2: Valio Aimo portal look).
// Token names are kept from DESIGN.md (`navy` = petrol, `primary-soft` = petrol-light, `accent` = lime,
// `surface-warm` = lavender); the portal-only colours have their own names (`lime`, `yellow`, `red`, `lavender`, `link`).
// Use either `bg-navy` or `bg-[var(--color-navy)]`; both resolve to the same token.
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    // Portal breakpoints (Bootstrap rem values): lg = 992 switches the desktop header on.
    screens: { sm: "576px", md: "768px", lg: "992px", xl: "1200px", "2xl": "1440px" },
    extend: {
      fontFamily: {
        sans: ["var(--font-sans)"],
        mono: ["var(--font-mono)"],
        display: ["var(--font-display)"],
      },
      colors: {
        navy: { DEFAULT: "var(--color-navy)", 900: "var(--color-navy-900)" },
        petrol: { DEFAULT: "var(--color-navy)", dark: "var(--color-navy-900)", light: "var(--color-primary-soft)" },
        primary: { DEFAULT: "var(--color-primary)", hover: "var(--color-primary-hover)", soft: "var(--color-primary-soft)" },
        accent: { DEFAULT: "var(--color-accent)", hover: "var(--color-accent-hover)", soft: "var(--color-accent-soft)" },
        lime: { DEFAULT: "var(--color-accent)", hover: "var(--color-accent-hover)", soft: "var(--color-accent-soft)" },
        lavender: { DEFAULT: "var(--color-lavender)", strong: "var(--color-lavender-strong)" },
        link: "var(--color-link)",
        yellow: { DEFAULT: "var(--color-yellow)", soft: "var(--color-yellow-soft)" },
        red: { DEFAULT: "var(--color-red)", strong: "var(--color-red-strong)", soft: "var(--color-red-soft)" },
        topbar: { muted: "var(--color-topbar-muted)" },
        canvas: "var(--color-bg)",
        surface: { DEFAULT: "var(--color-surface)", muted: "var(--color-surface-muted)", warm: "var(--color-surface-warm)" },
        line: { DEFAULT: "var(--color-border)", strong: "var(--color-border-strong)" },
        ink: { DEFAULT: "var(--color-text)", secondary: "var(--color-text-secondary)", muted: "var(--color-text-muted)" },
        success: { DEFAULT: "var(--color-success)", bg: "var(--color-success-bg)", border: "var(--color-success-border)" },
        warning: { DEFAULT: "var(--color-warning)", bg: "var(--color-warning-bg)", border: "var(--color-warning-border)" },
        danger: { DEFAULT: "var(--color-danger)", bg: "var(--color-danger-bg)", border: "var(--color-danger-border)" },
        unknown: { DEFAULT: "var(--color-unknown)", bg: "var(--color-unknown-bg)" },
      },
      // Nothing is rounder than 4 px except pills (`rounded-full`) and circles. CTAs use `rounded-cta` (1 px).
      borderRadius: {
        cta: "var(--radius-cta)",
        sm: "var(--radius-sm)",
        DEFAULT: "var(--radius-md)",
        md: "var(--radius-md)",
        lg: "var(--radius-md)",
        xl: "var(--radius-md)",
        "2xl": "var(--radius-md)",
      },
      boxShadow: {
        popover: "var(--shadow-popover)",
        sticky: "var(--shadow-sticky)",
        "portal-sm": "var(--shadow-sm)",
        // Content has no shadows in the portal: neutralise the generic utilities.
        sm: "none",
        DEFAULT: "none",
        md: "none",
        lg: "var(--shadow-popover)",
        xl: "var(--shadow-popover)",
      },
    },
  },
  plugins: [],
} satisfies Config;
