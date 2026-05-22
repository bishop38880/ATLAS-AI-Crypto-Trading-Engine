import { useEffect } from "react";

import { apply_agent_ping_timeout, is_ws_parse_failure_frame, map_agents_ws_payload } from "../lib/channel-mappers";
import { wsManager } from "../lib/ws-manager";
import { useAgentStore } from "../store/index";

/**
 * Subscribes to `/ws/agents` via the shared WebSocket manager and updates {@link useAgentStore}.
 */
export function useAgentChannel(): void {
  const setAgents = useAgentStore((state) => state.setAgents);
  const setConnected = useAgentStore((state) => state.setConnected);

  useEffect(() => {
    const unsubscribe = wsManager.connect("agents", (data) => {
      if (is_ws_parse_failure_frame(data)) {
        return;
      }
      const next = apply_agent_ping_timeout(map_agents_ws_payload(data));
      setAgents(next);
      setConnected(true);
    });

    return () => {
      unsubscribe();
      setConnected(false);
    };
  }, [setAgents, setConnected]);
}
