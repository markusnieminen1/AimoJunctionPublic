import { AlertTriangle } from "lucide-react";
import { toApiError } from "../api/client";
import { CopyChip } from "./CopyChip";
import { Button } from "./Button";

/** Inline error stripe for pages that opt out of toasts: message, code pill, copyable request_id, optional retry. */
export function ErrorPanel({ error, onRetry, title }: { error: unknown; onRetry?: () => void; title?: string }) {
  const e = toApiError(error);
  return (
    <div role="alert" className="rounded-[3px] bg-red-soft px-4 py-3">
      <div className="flex items-start gap-3">
        <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0 text-red-strong" strokeWidth={1.75} />
        <div className="min-w-0 flex-1">
          <p className="text-[14px] font-bold text-ink">{title ?? e.message}</p>
          {title && <p className="mt-0.5 text-[13px] text-ink">{e.message}</p>}
          <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
            <span className="font-mono-code rounded-sm bg-white/60 px-1.5 py-0.5 text-[11px] font-medium text-danger">{e.code}</span>
            {e.request_id && <CopyChip value={e.request_id} />}
          </div>
        </div>
        {onRetry && (
          <Button size="sm" variant="outline" onClick={onRetry}>
            Try again
          </Button>
        )}
      </div>
    </div>
  );
}

/** Compact "CODE · req_…" line for empty/not-found states that still come from an API error (AGENTS.md rule 9). */
export function ErrorMeta({ error, className = "" }: { error: unknown; className?: string }) {
  const e = toApiError(error);
  return (
    <span className={`mt-2 flex flex-wrap items-center justify-center gap-1.5 ${className}`}>
      <span className="font-mono-code rounded-sm bg-primary-soft px-1.5 py-0.5 text-[11px] font-medium text-ink-secondary">{e.code}</span>
      {e.request_id && <CopyChip value={e.request_id} />}
    </span>
  );
}
