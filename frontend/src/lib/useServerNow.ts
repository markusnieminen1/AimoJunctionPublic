import { useEffect, useState } from "react";
import { serverNowMs, subscribeServerClock } from "./serverClock";

/** Server "now" in epoch ms, re-rendering every `tickMs` (default 1 s) and on clock re-sync. */
export function useServerNow(tickMs = 1000): number {
  const [now, setNow] = useState(() => serverNowMs());
  useEffect(() => {
    const update = () => setNow(serverNowMs());
    const id = window.setInterval(update, tickMs);
    const unsub = subscribeServerClock(update);
    return () => {
      window.clearInterval(id);
      unsub();
    };
  }, [tickMs]);
  return now;
}
