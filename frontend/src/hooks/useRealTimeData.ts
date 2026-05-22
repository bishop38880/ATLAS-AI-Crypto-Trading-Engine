import { useEffect, useRef, useState } from "react";

import type { PipelineAgentPulseUi } from "../lib/channel-mappers";
import {
  apply_agent_ping_timeout,
  map_agents_to_pipeline_pulse,
  map_agents_ws_payload,
} from "../lib/channel-mappers";
import { apiUrl } from "../lib/url";
import { wsManager } from "../lib/ws-manager";

export type SocketUiStatus = "connecting" | "open" | "closed" | "error";

const DEFAULT_STALE_MS = 300_000;
const POLL_MS_FALLBACK = 5_000;

export interface RealtimeChannelResult<T> {
  data: T | null;
  status: SocketUiStatus;
  error: string | null;
}

function map_managed_state(wsState: ReturnType<typeof wsManager.getState>): SocketUiStatus {
  if (wsState === "CONNECTING") {
    return "connecting";
  }
  if (wsState === "OPEN") {
    return "open";
  }
  if (wsState === "CLOSED" || wsState === "CLOSING") {
    return "closed";
  }
  return "closed";
}

/**
 * WebSocket JSON channel via {@link wsManager} with optional REST polling every 5s when the socket is down or payloads are stale (5 minutes since last RX).
 *
 * **Contract:** callers must supply `parse`; there is no unsafe default coercion from `unknown` to `T`.
 */
export function useRealTimeData<T>(
  connectionKey: string,
  wsRelativePath: string,
  initialData: T | null,
  options: {
    parse: (raw: unknown) => T | null;
    pollUrl?: string;
    staleMs?: number;
    pollMs?: number;
  },
): RealtimeChannelResult<T> {
  const pollUrlLen = options.pollUrl?.length ?? 0;
  const staleMs = options.staleMs ?? DEFAULT_STALE_MS;
  const pollMs = options.pollMs ?? POLL_MS_FALLBACK;

  const [data, setData] = useState<T | null>(initialData);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<SocketUiStatus>("connecting");
  const lastRxRef = useRef<number>(0);
  const parseRef = useRef(options.parse);

  useEffect(() => {
    parseRef.current = options.parse;
  }, [options.parse]);

  useEffect(() => {
    if (initialData !== null) {
      lastRxRef.current = Date.now();
    } else {
      lastRxRef.current = 0;
    }
  }, [initialData]);

  useEffect(() => {
    const unsubscribe = wsManager.connectPath(connectionKey, wsRelativePath, (raw: unknown) => {
      lastRxRef.current = Date.now();

      const is_parse_error_record =
        typeof raw === "object" &&
        raw !== null &&
        "type" in raw &&
        (raw as { type: unknown }).type === "parse_error";

      if (is_parse_error_record) {
        setError(`Malformed frame on ${wsRelativePath}`);
        setStatus("error");
        return;
      }

      try {
        const next = parseRef.current(raw);
        if (next !== null) {
          setData(next);
          setError(null);
          setStatus("open");
        }
      } catch {
        setError(`Failed to parse ${wsRelativePath}`);
        setStatus("error");
      }
    });

    const tick = window.setInterval(() => {
      setStatus((prevErr) =>
        wsManager.getState(connectionKey) === "OPEN"
          ? "open"
          : prevErr === "error"
            ? "error"
            : map_managed_state(wsManager.getState(connectionKey)),
      );
    }, 400);

    return () => {
      window.clearInterval(tick);
      unsubscribe();
    };
  }, [connectionKey, wsRelativePath]);

  useEffect(() => {
    if (pollUrlLen === 0 || options.pollUrl === undefined) {
      return undefined;
    }

    const pollPath = options.pollUrl;

    const runPoll = (): void => {
      const wsOpen = wsManager.getState(connectionKey) === "OPEN";
      const no_rx_yet = lastRxRef.current === 0;
      const rx_stale = Date.now() - lastRxRef.current > staleMs;

      const should_fallback = !wsOpen || no_rx_yet || rx_stale;

      if (!should_fallback) {
        return;
      }

      void (async () => {
        try {
          const response = await fetch(apiUrl(pollPath), { headers: { Accept: "application/json" } });
          if (!response.ok) {
            return;
          }
          const payload: unknown = await response.json();
          const next = parseRef.current(payload);
          if (next !== null) {
            lastRxRef.current = Date.now();
            setData(next);
            setError(null);
          }
        } catch {
          setStatus("error");
          setError(`poll_failed ${pollPath}`);
        }
      })();
    };

    const intervalId = window.setInterval(runPoll, pollMs);
    runPoll();

    return () => window.clearInterval(intervalId);
  }, [connectionKey, options.pollUrl, pollMs, pollUrlLen, staleMs]);

  return { data, status, error };
}

/** Agents WS → pipeline traffic-light rows (ping timeout applied). */
export function parse_agents_for_pipeline(raw: unknown): PipelineAgentPulseUi[] {
  const normalized = apply_agent_ping_timeout(map_agents_ws_payload(raw));
  return map_agents_to_pipeline_pulse(normalized);
}
