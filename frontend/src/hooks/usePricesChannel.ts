import { useEffect } from "react";

import { is_ws_parse_failure_frame, map_prices_ws_payload } from "../lib/channel-mappers";
import { wsManager } from "../lib/ws-manager";
import { usePricesStore } from "../store/index";

/**
 * Subscribes to `/ws/prices` (3s push) and fans updates into {@link usePricesStore}.
 */
export function usePricesChannel(): void {
  const updatePrice = usePricesStore((state) => state.updatePrice);
  const setConnected = usePricesStore((state) => state.setConnected);

  useEffect(() => {
    const unsubscribe = wsManager.connect(
      "prices",
      (data) => {
        if (is_ws_parse_failure_frame(data)) {
          return;
        }
        const rows = map_prices_ws_payload(data);
        for (const row of rows) {
          updatePrice(row.symbol, row);
        }
        setConnected(true);
      },
      () => setConnected(true),
    );

    return () => {
      unsubscribe();
      setConnected(false);
    };
  }, [updatePrice, setConnected]);
}
