/** Co-branded white Junction wordmark paired with a 0px #C0E021 EDITION tag for dark petrol bars. */
export function JunctionEditionBadge({ size = "sm", testId }: { size?: "sm" | "md"; testId?: string }) {
  const logoH = size === "md" ? "h-[18px]" : "h-[14px]";
  return (
    <span className="inline-flex shrink-0 items-center gap-1.5" data-testid={testId}>
      <img src="/junction-logo.png" alt="Junction" className={`${logoH} w-auto object-contain`} />
      <span className="rounded-none bg-lime px-1.5 py-0.5 text-[10px] font-bold uppercase leading-none tracking-[0.06em] text-navy">
        EDITION
      </span>
    </span>
  );
}
