import { describe, expect, it } from "vitest";
import { defaultDeliveryDate, formatDate, formatDateTime, formatDayDate, formatDuration, formatShortDate, helsinkiDate } from "./dates";

describe("defaultDeliveryDate (next weekday ≥ serverNow + 1 day, Helsinki)", () => {
  it.each([
    ["2026-11-14T10:00:00+02:00", "2026-11-16"], // Sat → Mon (demo clock)
    ["2026-11-13T10:00:00+02:00", "2026-11-16"], // Fri → Sat → Mon
    ["2026-11-15T23:30:00+02:00", "2026-11-16"], // Sun late evening → Mon
    ["2026-11-16T08:00:00+02:00", "2026-11-17"], // Mon → Tue
    ["2026-11-18T12:00:00+02:00", "2026-11-19"], // Wed → Thu
    ["2026-11-16T23:30:00+02:00", "2026-11-17"], // Mon 23:30 Helsinki (= Mon 21:30Z) → Tue
    ["2026-11-15T22:30:00Z", "2026-11-17"], // = Mon 00:30 Helsinki → Tue (uses Helsinki calendar, not UTC)
  ])("%s → %s", (now, expected) => {
    expect(defaultDeliveryDate(new Date(now))).toBe(expected);
  });

  it("accepts epoch ms", () => {
    expect(defaultDeliveryDate(Date.parse("2026-11-14T10:00:00+02:00"))).toBe("2026-11-16");
  });
});

describe("date formatting", () => {
  it("helsinkiDate uses the Helsinki calendar", () => {
    expect(helsinkiDate(new Date("2026-11-14T23:30:00Z"))).toBe("2026-11-15");
  });
  it("formatDayDate", () => {
    expect(formatDayDate("2026-11-16")).toBe("Mon 16.11.2026");
    expect(formatDate("2026-11-06")).toBe("6.11.2026");
    expect(formatShortDate("2026-11-16")).toBe("16.11.");
    expect(formatDateTime("2026-11-14T06:38:00+02:00")).toBe("14.11.2026 at 06:38");
  });
  it("formatDuration", () => {
    expect(formatDuration((2 * 3600 + 14 * 60) * 1000)).toBe("2h 14m");
    expect(formatDuration(65_000)).toBe("1m 05s");
    expect(formatDuration(-5)).toBe("0s");
  });
});

describe("helsinkiEndOfDayIso", () => {
  it("uses the winter and summer offsets", async () => {
    const { helsinkiEndOfDayIso } = await import("./dates");
    expect(helsinkiEndOfDayIso("2026-12-31")).toBe("2026-12-31T23:59:00+02:00");
    expect(helsinkiEndOfDayIso("2026-07-01")).toBe("2026-07-01T23:59:00+03:00");
  });
});
