import type { ReactNode } from "react";

// Tags, badges and dots (DESIGN_VALIO §4.4 + Marketing brand-kit). Enforce sharp 0px square tags across portal.

export type BadgeTone = "success" | "warning" | "danger" | "info" | "neutral" | "navy" | "unknown";

const tones: Record<BadgeTone, string> = {
  success: "bg-lime text-navy border-[#9AB418]",
  warning: "bg-[#FFF5DF] text-[#7A5200] border-[#D99B26]",
  danger: "bg-[#FDECEB] text-[#B41E17] border-[#B41E17]/40",
  info: "bg-[#F2F6F8] text-navy border-[#005670]/40",
  neutral: "bg-[#F2F6F8] text-ink-secondary border-line",
  navy: "bg-[#EEE8F5] text-[#4B3570] border-[#C5B3DF]",
  unknown: "bg-[#FFF5DF] text-[#7A5200] border-[#D99B26]",
};

/** Sharp square Valio tag (`0px` radius, 11 px uppercase bold). `dashed` for uncertain states. */
export function Badge({ tone = "neutral", icon, dashed, className = "", title, "data-testid": testId, children }: {
  tone?: BadgeTone;
  icon?: ReactNode;
  dashed?: boolean;
  className?: string;
  title?: string;
  "data-testid"?: string;
  children: ReactNode;
}) {
  return (
    <span
      title={title}
      data-testid={testId}
      className={`inline-flex items-center gap-1 whitespace-nowrap rounded-none border px-2 py-0.5 text-[11px] font-bold uppercase tracking-[0.04em] leading-4 ${tones[tone]} ${dashed ? "border-dashed" : ""} ${className}`}
    >
      {icon}
      {children}
    </span>
  );
}

export type TagKind = "offer" | "seasonal" | "deviation" | "tip" | "agent" | "approval" | "blocked" | "auto" | "new" | "neutral";

const tagKinds: Record<TagKind, string> = {
  offer: "bg-yellow text-black",
  seasonal: "bg-lavender text-black",
  deviation: "bg-yellow-soft text-navy",
  tip: "bg-lime-soft text-navy",
  agent: "bg-lavender text-navy",
  approval: "bg-yellow text-black",
  blocked: "bg-red-soft text-danger",
  auto: "bg-lime-soft text-navy",
  new: "bg-lime text-navy",
  neutral: "bg-primary-soft text-navy",
};

/** Portal tag: rectangular, display font 16 px UPPERCASE (`OFFER`, `APPROVAL NEEDED`, `🤖 AGENT`). */
export function Tag({ kind = "neutral", icon, className = "", title, children }: { kind?: TagKind; icon?: ReactNode; className?: string; title?: string; children: ReactNode }) {
  return (
    <span title={title} className={`label-display inline-flex items-center gap-1 whitespace-nowrap px-2 py-1 text-[16px] leading-[19px] ${tagKinds[kind]} ${className}`}>
      {icon}
      {children}
    </span>
  );
}

/** 20 px red count circle (cart, approvals). `navy` = petrol variant for neutral counts. */
export function CountBadge({ count, tone = "danger", className = "" }: { count: number; tone?: "warning" | "navy" | "danger"; className?: string }) {
  if (!count) return null;
  const cls = tone === "navy" ? "bg-navy text-white" : "bg-red text-white";
  return (
    <span className={`tabular-nums inline-flex h-5 min-w-[20px] items-center justify-center rounded-full px-1 text-[10px] font-bold leading-none ${cls} ${className}`}>
      {count > 99 ? "99+" : count}
    </span>
  );
}

export type DotTone = "lime" | "yellow" | "red" | "grey" | "petrol";
const dotTones: Record<DotTone, string> = {
  lime: "bg-lime",
  yellow: "bg-yellow",
  red: "bg-red",
  grey: "bg-line-strong",
  petrol: "bg-navy",
};

/** 10 px status dot (portal "● Not paid" style). `dashed` adds the amber dashed ring of uncertain states. */
export function Dot({ tone, dashed = false, className = "" }: { tone: DotTone; dashed?: boolean; className?: string }) {
  return (
    <span
      aria-hidden="true"
      className={`inline-block h-2.5 w-2.5 shrink-0 rounded-full ${dotTones[tone]} ${dashed ? "outline outline-1 outline-offset-1 outline-dashed outline-warning" : ""} ${className}`}
    />
  );
}
