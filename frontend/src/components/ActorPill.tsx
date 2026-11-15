import { Bot, User } from "lucide-react";
import type { Principal } from "../api/types";
import { useStrings } from "../i18n";

/**
 * "🤖 Kitchen Reorder Bot · authorized by Anna Korhonen" for agents, "Anna Korhonen" for users, "System" for null.
 * `compact` drops the delegation; `stacked` keeps the agent name whole and puts "authorized by …" as wrapping text next to it (narrow screens).
 */
export function ActorPill({ actor, compact = false, stacked = false }: { actor: Principal | null | undefined; compact?: boolean; stacked?: boolean }) {
  const strings = useStrings();
  if (!actor) {
    return <span className="inline-flex items-center rounded-sm bg-primary-soft px-1.5 py-0.5 text-[12px] font-medium text-ink-secondary">System</span>;
  }
  if (actor.kind === "agent" && stacked && !compact) {
    return (
      <span className="inline-flex max-w-full flex-wrap items-center gap-x-1.5 gap-y-0.5">
        <ActorPill actor={actor} compact />
        {actor.delegated_by && (
          <span className="text-[12px] text-ink-secondary">
            {strings.actor.authorizedBy} {actor.delegated_by.name}
          </span>
        )}
      </span>
    );
  }
  if (actor.kind === "agent") {
    return (
      <span className="inline-flex max-w-full items-center gap-1 rounded-sm bg-lavender px-1.5 py-0.5 text-[12px] font-bold text-navy">
        <Bot className="h-3.5 w-3.5 shrink-0" strokeWidth={1.75} aria-label="AI agent" />
        <span className="truncate">{actor.name}</span>
        {!compact && actor.delegated_by && (
          <span className="truncate font-normal text-ink-secondary">
            · {strings.actor.authorizedBy} {actor.delegated_by.name}
          </span>
        )}
      </span>
    );
  }
  return (
    <span className="inline-flex max-w-full items-center gap-1 text-[13px] font-medium text-ink">
      <User className="h-3.5 w-3.5 shrink-0 text-ink-muted" strokeWidth={1.75} />
      <span className="truncate">{actor.name}</span>
    </span>
  );
}
