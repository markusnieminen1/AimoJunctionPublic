import type { ReactNode } from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { formatNumber } from "../lib/format";
import { Button } from "./Button";

/**
 * Portal pagination bar (DESIGN_VALIO §4.6): petrol-light strip, "Products 1 - 24 / 57" left, optional `right` content
 * (e.g. a "Showing" select), prev/next as icon-light circles. Sits above the list. Figures come from the API's totals.
 */
export function PaginationBar({ noun, page, pageSize, total, onPage, right, className = "" }: {
  noun: string;
  page: number;
  pageSize: number;
  total: number;
  onPage?: (page: number) => void;
  right?: ReactNode;
  className?: string;
}) {
  const pages = Math.max(1, Math.ceil(total / pageSize));
  const from = total === 0 ? 0 : (page - 1) * pageSize + 1;
  const to = Math.min(total, page * pageSize);
  return (
    <nav aria-label="Pagination" className={`flex flex-wrap items-center justify-between gap-2 rounded bg-primary-soft p-2 text-[14px] ${className}`}>
      <span className="tabular-nums px-2 text-ink" data-testid="pagination-range">
        {noun} {formatNumber(from)} - {formatNumber(to)} / {formatNumber(total)}
      </span>
      <div className="flex items-center gap-2">
        {right}
        {onPage && pages > 1 && (
          <>
            <Button variant="icon-light" aria-label="Previous page" disabled={page <= 1} onClick={() => onPage(page - 1)} className="bg-white">
              <ChevronLeft className="h-4 w-4" strokeWidth={1.75} />
            </Button>
            <span className="tabular-nums text-[13px] text-ink-muted">
              {page} / {pages}
            </span>
            <Button variant="icon-light" aria-label="Next page" disabled={page >= pages} onClick={() => onPage(page + 1)} className="bg-white">
              <ChevronRight className="h-4 w-4" strokeWidth={1.75} />
            </Button>
          </>
        )}
      </div>
    </nav>
  );
}
