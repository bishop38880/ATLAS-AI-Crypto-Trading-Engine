import { useEffect } from "react";

import { wsManager } from "../lib/ws-manager";

/**
 * Optional Prometheus portfolio stream — feeds nothing into POLARIS stores yet.
 * Connection still flows through {@link wsManager} so URL resolution stays centralised.
 */
export function usePortfolioStream(onMessage?: (data: unknown) => void): void {
  useEffect(() => {
    if (!onMessage) {
      return () => undefined;
    }
    return wsManager.connectPath("portfolio-stream", "/ws/portfolio/stream", onMessage);
  }, [onMessage]);
}
