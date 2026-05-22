import { useEffect } from "react";

import { is_ws_parse_failure_frame, map_system_ws_payload } from "../lib/channel-mappers";
import { wsManager } from "../lib/ws-manager";
import { useSystemStore } from "../store/index";

/**
 * Subscribes to `/ws/system` for regime, portfolio, and cycle telemetry.
 */
export function useSystemChannel(): void {
  const setHealth = useSystemStore((state) => state.setHealth);

  useEffect(() => {
    const unsubscribe = wsManager.connect("system", (data) => {
      if (is_ws_parse_failure_frame(data)) {
        return;
      }
      setHealth(map_system_ws_payload(data));
    });

    return () => {
      unsubscribe();
    };
  }, [setHealth]);
}
