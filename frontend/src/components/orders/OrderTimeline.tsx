// Vertical order event timeline (DESIGN.md §6F): 2px rail, server timestamps, actor pill, message, copyable request_id chip.
import type { OrderEvent } from "../../api/types";
import { useStrings } from "../../i18n";
import { formatDateTime } from "../../lib/dates";
import { ActorPill } from "../ActorPill";
import { RequestIdChip } from "../RequestIdChip";

/** Dot colour by event type keyword (display only). */
function dotClass(type: string): string {
  const s = type.toLowerCase();
  if (/(reject|fail|block|error|cancel)/.test(s)) return "bg-danger";
  if (/(timeout|unknown|revalidat|stale|expire|pending)/.test(s)) return "bg-[#FDDB32]";
  if (/(approv|placed|confirm)/.test(s)) return "bg-lime";
  return "bg-navy";
}

export function OrderTimeline({ events }: { events: OrderEvent[] }) {
  const strings = useStrings();
  if (!events.length) return <p className="text-sm text-ink-muted">{strings.orders.timelineEmpty}</p>;
  return (
    <ol className="relative" aria-label={strings.orders.timelineTitle} data-testid="order-timeline">
      {events.map((e, i) => (
        <li key={`${e.at}-${i}`} className="relative pb-5 pl-6 last:pb-0" data-testid="timeline-event">
          {i < events.length - 1 && <span className="absolute left-[4px] top-3 h-full w-[2px] bg-[#DEE4E6]" aria-hidden="true" />}
          <span className={`absolute left-0 top-1.5 h-2.5 w-2.5 rounded-full ${dotClass(e.type)}`} aria-hidden="true" />
          <div className="flex flex-wrap items-start justify-between gap-2">
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1">
                <time dateTime={e.at} className="tabular-nums text-[14px] font-semibold text-ink">
                  {formatDateTime(e.at)}
                </time>
                <span className="font-mono-code text-[11px] text-ink-muted">{e.type}</span>
                <ActorPill actor={e.actor} />
              </div>
              <p className="mt-1 text-[14px] leading-[1.45] text-ink tabular-nums">{e.message}</p>
            </div>
            <div className="shrink-0">
              <RequestIdChip id={e.request_id} />
            </div>
          </div>
        </li>
      ))}
    </ol>
  );
}
