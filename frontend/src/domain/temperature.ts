// temperature_condition code → zone (display only). Source: valio_rules.py TEMPERATURE.

export type TempZone = "chilled" | "frozen" | "ambient" | "unknown";

export type TempInfo = { zone: TempZone; label: string; range: string | null };

const MAP: Record<number, TempInfo> = {
  1: { zone: "chilled", label: "Chilled", range: "0..+3 °C" },
  2: { zone: "chilled", label: "Chilled", range: "+2..+4 °C" },
  3: { zone: "chilled", label: "Chilled", range: "+2..+6 °C" },
  4: { zone: "chilled", label: "Chilled", range: "+4..+8 °C" },
  5: { zone: "chilled", label: "Cool produce", range: "fresh produce" },
  6: { zone: "ambient", label: "Cool ambient", range: null },
  7: { zone: "frozen", label: "Frozen", range: "−25..−18 °C" },
  8: { zone: "ambient", label: "Ambient", range: "+10..+22 °C" },
};

export function temperatureInfo(code: number | null | undefined): TempInfo {
  if (code == null) return { zone: "unknown", label: "Temp. unknown", range: null };
  return MAP[code] ?? { zone: "unknown", label: "Temp. unknown", range: null };
}
