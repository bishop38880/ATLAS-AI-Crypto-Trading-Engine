import { useEffect, useState } from "react";

import type { ManagedWsChannel } from "../lib/ws-manager";
import { wsManager } from "../lib/ws-manager";

export type JsonSocketStatus = "connecting" | "open" | "closed" | "error";

function map_socket_state(
  raw: ReturnType<typeof wsManager.getState>,
): JsonSocketStatus {
  if (raw === "OPEN") {
    return "open";
  }
  if (raw === "CONNECTING") {
    return "connecting";
  }
  if (raw === "CLOSING" || raw === "CLOSED") {
    return "closed";
  }
  return "closed";
}

function is_record(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

/**
 * Subscribe to a JSON WebSocket path via {@link wsManager}.
 */
export function useWsJsonPath(
  connectionKey: string,
  path: string,
): {
  data: unknown;
  status: JsonSocketStatus;
  error: string | null;
} {
  const [data, setData] = useState<unknown>(null);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<JsonSocketStatus>("connecting");

  useEffect(() => {
    const unsubscribe = wsManager.connectPath(connectionKey, path, (raw) => {
      if (is_record(raw) && raw.type === "parse_error") {
        setError(`Malformed frame on ${path}`);
        return;
      }
      setData(raw);
      setError(null);
    });

    const intervalId = window.setInterval(() => {
      setStatus(map_socket_state(wsManager.getState(connectionKey)));
    }, 400);

    return () => {
      window.clearInterval(intervalId);
      unsubscribe();
    };
  }, [connectionKey, path]);

  return { data, status, error };
}

/**
 * Polls {@link wsManager} state for first-party channels (agents, system, …).
 */
export function useManagedWsStatus(channel: ManagedWsChannel): JsonSocketStatus {
  const [status, setStatus] = useState<JsonSocketStatus>("closed");

  useEffect(() => {
    const intervalId = window.setInterval(() => {
      setStatus(map_socket_state(wsManager.getState(channel)));
    }, 400);
    return () => window.clearInterval(intervalId);
  }, [channel]);

  return status;
}
