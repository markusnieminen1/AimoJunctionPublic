import { ScrollText } from "lucide-react";
import { Link } from "react-router-dom";
import { CopyChip } from "./CopyChip";

/** Copyable request_id chip plus a small link that opens `/audit?request_id=…` (deep link into the audit log). */
export function RequestIdChip({ id, className = "", link = true }: { id: string | null | undefined; className?: string; link?: boolean }) {
  if (!id) return null;
  return (
    <span className={`inline-flex max-w-full items-center gap-0.5 ${className}`}>
      <CopyChip value={id} />
      {link && (
        <Link
          to={`/audit?request_id=${encodeURIComponent(id)}`}
          title={`Open ${id} in the audit log`}
          aria-label={`Open ${id} in the audit log`}
          className="inline-flex h-[22px] w-[22px] shrink-0 items-center justify-center rounded text-ink-muted hover:bg-primary-soft hover:text-primary"
        >
          <ScrollText className="h-3.5 w-3.5" strokeWidth={1.75} />
        </Link>
      )}
    </span>
  );
}
