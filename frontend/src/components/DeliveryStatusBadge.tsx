import type { DeliveryStatus } from "../api/types";
import { useStrings } from "../i18n";
import { formatPercent } from "../lib/format";
import { Dot, type DotTone } from "./Badge";

// Portal stock/delivery dot line (DESIGN_VALIO §4.4): lime on time, yellow at risk, red late / not deliverable.
const TONE: Record<DeliveryStatus, DotTone> = {
  ON_TIME: "lime",
  AT_RISK: "yellow",
  LATE: "red",
  NOT_DELIVERABLE: "red",
  UNKNOWN: "grey",
};

/** Delivery status dot + label; optional on-time probability ("On time · 98 %"). */
export function DeliveryStatusBadge({ status, probability }: { status: DeliveryStatus; probability?: number | null }) {
  const strings = useStrings();
  return (
    <span className="inline-flex items-center gap-2 whitespace-nowrap text-[13px] leading-4 text-ink" data-delivery={status}>
      <Dot tone={TONE[status] ?? "grey"} dashed={status === "UNKNOWN"} />
      <span>{strings.deliveryStatus[status] ?? status}</span>
      {probability != null && <span className="tabular-nums text-ink-muted">· {formatPercent(probability)}</span>}
    </span>
  );
}
