import { Snowflake, Sun, Thermometer } from "lucide-react";
import { temperatureInfo } from "../domain/temperature";

const STYLE = {
  chilled: "bg-[var(--color-temp-chilled-bg)] text-[var(--color-temp-chilled)]",
  frozen: "bg-[var(--color-temp-frozen-bg)] text-[var(--color-temp-frozen)]",
  ambient: "bg-[var(--color-temp-ambient-bg)] text-[var(--color-temp-ambient)]",
  unknown: "bg-unknown-bg text-unknown",
};

/** Temperature zone pill from `temperature_condition` (Chilled +4..+8 °C / Frozen / Ambient). */
export function TemperatureBadge({ code, showRange = true }: { code: number | null | undefined; showRange?: boolean }) {
  const t = temperatureInfo(code);
  const Icon = t.zone === "frozen" ? Snowflake : t.zone === "ambient" ? Sun : Thermometer;
  return (
    <span className={`inline-flex items-center gap-1 whitespace-nowrap rounded-sm px-1.5 py-0.5 text-[12px] font-bold leading-4 ${STYLE[t.zone]}`}>
      <Icon className="h-3 w-3" strokeWidth={1.75} />
      {t.label}
      {showRange && t.range && <span className="tabular-nums font-medium">{t.range}</span>}
    </span>
  );
}
