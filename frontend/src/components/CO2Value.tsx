import { Leaf } from "lucide-react";
import { formatCo2 } from "../lib/format";
import { Tooltip } from "./Tooltip";

/** CO₂ value with its basis in a tooltip (never a bare precise number). `basis` null → "basis not provided". */
export function CO2Value({ kg, basis, className = "" }: { kg: number | null | undefined; basis: string | null | undefined; className?: string }) {
  if (kg == null) return <span className={`text-xs text-ink-muted ${className}`}>CO₂ n/a</span>;
  return (
    <Tooltip content={<>Estimate · {basis ?? "basis not provided by the API"}</>}>
      <span tabIndex={0} className={`tabular-nums inline-flex cursor-help items-center gap-1 whitespace-nowrap text-ink-secondary underline decoration-dotted underline-offset-2 ${className}`}>
        <Leaf className="h-3.5 w-3.5 text-success" strokeWidth={1.75} />~{formatCo2(kg)}
      </span>
    </Tooltip>
  );
}
