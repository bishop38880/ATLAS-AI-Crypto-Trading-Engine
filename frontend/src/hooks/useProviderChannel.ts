import { useEffect } from "react";

import { is_ws_parse_failure_frame, map_provider_ws_payload } from "../lib/channel-mappers";
import { wsManager } from "../lib/ws-manager";
import { useProviderStore } from "../store/index";

/**
 * Subscribes to `/ws/providers` for circuit-breaker and latency envelopes.
 */
export function useProviderChannel(): void {
  const setProviders = useProviderStore((state) => state.setProviders);
  const setConnected = useProviderStore((state) => state.setConnected);

  useEffect(() => {
    const unsubscribe = wsManager.connect("providers", (data) => {
      if (is_ws_parse_failure_frame(data)) {
        return;
      }
      setProviders(map_provider_ws_payload(data));
      setConnected(true);
    });

    return () => {
      unsubscribe();
      setConnected(false);
    };
  }, [setProviders, setConnected]);
}
