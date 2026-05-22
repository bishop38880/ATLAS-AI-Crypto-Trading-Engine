import { useEffect, useRef, useState } from "react";

import { useSystemStore } from "../store/index";

export function format_countdown_label(total_seconds: number): string {
  const minutes = Math.floor(Math.max(0, total_seconds) / 60);
  const seconds = Math.max(0, total_seconds) % 60;
  return `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
}

/** Server-authoritative next-cycle countdown with local tick-down between WS updates. */
export function useNextCycleCountdown(): string {
  const next_server_seconds = Math.max(0, useSystemStore((state) => state.health?.nextCycleSeconds ?? 0));
  const server_seconds_ref = useRef(next_server_seconds);
  const [display_seconds, set_display_seconds] = useState(next_server_seconds);

  useEffect(() => {
    server_seconds_ref.current = next_server_seconds;
  }, [next_server_seconds]);

  useEffect(() => {
    const timer_id = window.setInterval(() => {
      set_display_seconds((previous_display) => {
        const server_snapshot = Math.max(0, server_seconds_ref.current);

        if (server_snapshot > previous_display) {
          return server_snapshot;
        }

        if (Math.abs(server_snapshot - previous_display) > 2) {
          return server_snapshot;
        }

        return Math.max(0, previous_display - 1);
      });
    }, 1000);

    return () => window.clearInterval(timer_id);
  }, []);

  return format_countdown_label(display_seconds);
}
