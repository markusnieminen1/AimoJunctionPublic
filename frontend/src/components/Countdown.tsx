import { useServerNow } from "../lib/useServerNow";
import { formatDuration } from "../lib/dates";
import { useStrings } from "../i18n";

/**
 * Live countdown to an ISO timestamp using server time. Amber under `warnBelowMs` (default 1 h), red when expired.
 * On dark petrol headers (`onDark`), uses white text normally and `#FDDB32` yellow under `warnBelowMs`.
 * `prefix` defaults to "Expires in"; pass "" for just the duration.
 */
export function Countdown({ to, prefix, expiredLabel, warnBelowMs = 3_600_000, onDark = false, className = "" }: {
  to: string | null | undefined;
  prefix?: string;
  expiredLabel?: string;
  warnBelowMs?: number;
  onDark?: boolean;
  className?: string;
}) {
  const strings = useStrings();
  const effectivePrefix = prefix ?? strings.common.expiresIn;
  const effectiveExpired = expiredLabel ?? strings.common.expired;
  const now = useServerNow();
  if (!to) return null;
  const left = Date.parse(to) - now;
  if (Number.isNaN(left)) return null;
  const tone = onDark
    ? left <= 0
      ? "text-red-soft"
      : left < warnBelowMs
        ? "text-yellow"
        : "text-white"
    : left <= 0
      ? "text-danger"
      : left < warnBelowMs
        ? "text-warning"
        : "text-ink-secondary";
  return (
    <span className={`tabular-nums whitespace-nowrap text-xs font-semibold ${tone} ${className}`}>
      {left <= 0 ? effectiveExpired : `${effectivePrefix ? `${effectivePrefix} ` : ""}${formatDuration(left)}`}
    </span>
  );
}
