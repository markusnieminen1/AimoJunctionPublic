import { useEffect, useState } from "react";
import { useStrings } from "../i18n";

/** "Live · updated 4s ago" (elapsed since the last successful fetch, browser clock; not a server countdown). */
export function LiveIndicator({ updatedAt, fetching }: { updatedAt: number; fetching: boolean }) {
  const t = useStrings().approvals;
  const [, setTick] = useState(0);
  useEffect(() => {
    const id = window.setInterval(() => setTick((n) => n + 1), 1000);
    return () => window.clearInterval(id);
  }, []);
  const secs = updatedAt ? Math.max(0, Math.round((Date.now() - updatedAt) / 1000)) : 0;
  return (
    <span className="inline-flex items-center gap-1.5 text-[12px] font-medium text-ink-muted" aria-live="off">
      <span className="relative flex h-2 w-2">
        <span className={`absolute inline-flex h-full w-full rounded-full bg-lime opacity-70 ${fetching ? "animate-ping" : ""}`} />
        <span className="relative inline-flex h-2 w-2 rounded-full bg-lime" />
      </span>
      <span className="font-bold text-ink">{t.live}</span>
      <span aria-hidden="true">·</span>
      <span className="tabular-nums">{t.updatedAgo(secs)}</span>
    </span>
  );
}
