/**
 * Aimo Annos wordmark for the portal's logo slot (DESIGN_VALIO §3.6). Our own mark, not the Valio Aimo logo:
 * "Aimo Annos" in Figtree 700 italic, petrol, with a tiny display-font "AGENT GATEWAY" superscript in the position of the
 * real logo's "VALIO". `tone="white"` for the footer and the drawer head. `size="sm"` fits the ≤ 90 px mobile slot.
 */
export function AimoWordmark({ showPill = true, tone = "petrol", size = "lg", className = "" }: {
  showPill?: boolean;
  tone?: "petrol" | "white";
  size?: "lg" | "sm";
  className?: string;
}) {
  const color = tone === "white" ? "text-white" : "text-navy";
  const text = size === "sm" ? "text-[19px]" : "text-[26px]";
  const sup = size === "sm" ? "text-[6.5px] -top-[7px]" : "text-[8px] -top-[9px]";
  return (
    <span className={`relative inline-flex select-none items-baseline ${color} ${className}`} data-testid="wordmark">
      <span className={`font-sans font-bold italic leading-none tracking-[-0.02em] ${text}`}>Aimo Annos</span>
      {showPill && (
        <span aria-hidden="true" className={`font-sans font-bold uppercase absolute right-0 whitespace-nowrap leading-none tracking-[0.08em] ${sup}`}>
          Agent gateway
        </span>
      )}
    </span>
  );
}
