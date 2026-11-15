/** Neutral pulse block (petrol-light grey). Size it like the real content (no layout shift). */
export function Skeleton({ className = "h-4 w-full" }: { className?: string }) {
  return <div className={`animate-pulse rounded bg-[#E8EEF0] ${className}`} aria-hidden="true" />;
}

/** N skeleton table rows of height `rowClass`. */
export function SkeletonRows({ rows = 5, rowClass = "h-10" }: { rows?: number; rowClass?: string }) {
  return (
    <div className="space-y-2" aria-busy="true" aria-label="Loading">
      {Array.from({ length: rows }, (_, i) => (
        <Skeleton key={i} className={`${rowClass} w-full`} />
      ))}
    </div>
  );
}
