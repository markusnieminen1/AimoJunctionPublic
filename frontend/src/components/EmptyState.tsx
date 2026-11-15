import type { ReactNode } from "react";
import { Inbox } from "lucide-react";

/** Empty state: icon in a petrol-light circle, one line, one CTA (use an `outline` button). */
export function EmptyState({ icon, title, description, action }: { icon?: ReactNode; title: ReactNode; description?: ReactNode; action?: ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center px-4 py-10 text-center">
      <div className="mb-4 flex h-14 w-14 items-center justify-center rounded-full bg-primary-soft text-navy [&_svg]:h-6 [&_svg]:w-6">
        {icon ?? <Inbox strokeWidth={1.5} />}
      </div>
      <p className="text-[16px] font-bold leading-[21px] text-ink">{title}</p>
      {description && <p className="mt-1 max-w-md text-[14px] leading-5 text-ink-secondary">{description}</p>}
      {action && <div className="mt-5">{action}</div>}
    </div>
  );
}
