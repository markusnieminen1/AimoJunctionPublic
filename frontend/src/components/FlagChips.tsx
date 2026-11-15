import { Info } from "lucide-react";
import type { LineFlag } from "../api/types";
import { useStrings } from "../i18n";
import { Tooltip } from "./Tooltip";

const DANGER: LineFlag[] = ["CUTOFF_PASSED", "LATE", "NOT_DELIVERABLE", "SALES_BLOCKED", "NO_PRICE"];
const WARNING: LineFlag[] = ["PARTIAL", "UNIT_MISMATCH", "SUPPLIER_QTY_UNKNOWN", "LOW_RELIABILITY"];

function toneFor(f: LineFlag): string {
  if (DANGER.includes(f)) return "bg-red-soft text-danger";
  if (WARNING.includes(f)) return "bg-yellow-soft text-warning";
  return "bg-primary-soft text-ink-secondary";
}

/** One small tag per LineFlag with a plain-words tooltip. CO2_ESTIMATED shows the line's `co2_basis`. Unknown flags render raw. */
export function FlagChips({ flags, co2Basis, className = "" }: { flags: LineFlag[] | string[]; co2Basis?: string | null; className?: string }) {
  const strings = useStrings();
  if (!flags?.length) return null;
  return (
    <span className={`inline-flex flex-wrap gap-1 ${className}`}>
      {(flags as string[]).map((f) => {
        const known = (strings.flags as Record<string, { label: string; help: string }>)[f];
        const help = f === "CO2_ESTIMATED" && co2Basis ? `${known?.help ?? ""} Basis: ${co2Basis}` : known?.help ?? f;
        return (
          <Tooltip key={f} content={help}>
            <span
              tabIndex={0}
              data-flag={f}
              className={`inline-flex cursor-help items-center gap-1 whitespace-nowrap rounded-sm px-1.5 py-0.5 text-[12px] font-bold leading-4 ${toneFor(f as LineFlag)}`}
            >
              {known?.label ?? f}
              <Info className="h-3 w-3 opacity-70" strokeWidth={1.75} />
            </span>
          </Tooltip>
        );
      })}
    </span>
  );
}
