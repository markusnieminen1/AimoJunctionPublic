// Display helper for long server IDs (the real backend uses `ord_` + 26-char ULID, e.g.
// `ord_01M7C5DMP4B3DS0DZ3C41GN3G5`). Tables show a short form; links, copy chips and `title` keep the full ID.

/** `ord_01M7C5DMP4B3DS0DZ3C41GN3G5` → `ord_…C41GN3G5`. Short IDs (mocks) are returned unchanged. */
export function shortId(id: string, tail = 8): string {
  const us = id.indexOf("_");
  const prefix = us > 0 && us <= 5 ? id.slice(0, us + 1) : "";
  const rest = id.slice(prefix.length);
  if (rest.length <= 16) return id;
  return `${prefix}…${rest.slice(-tail)}`;
}
