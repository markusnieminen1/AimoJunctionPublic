/**
 * Minimal inline terracotta starburst icon representing Claude MCP compatibility (no external CDN fetch).
 * Marked aria-hidden for decorative use alongside descriptive "Claude / MCP" labels.
 */
export function ClaudeMark({ className = "h-4 w-4 shrink-0" }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      aria-hidden="true"
      className={className}
    >
      <path
        d="M12 2v6.2l4.4-4.4 1.4 1.4-4.4 4.4H19.6v2h-6.2l4.4 4.4-1.4 1.4-4.4-4.4V20h-2v-6.2l-4.4 4.4-1.4-1.4 4.4-4.4H2.4v-2h6.2L4.2 5.2l1.4-1.4L10 8.2V2h2z"
        fill="#D97757"
      />
    </svg>
  );
}
