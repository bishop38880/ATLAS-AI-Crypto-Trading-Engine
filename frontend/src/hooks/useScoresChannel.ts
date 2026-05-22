import { useEffect } from "react";

import { is_ws_parse_failure_frame, map_scores_ws_payload } from "../lib/channel-mappers";
import { wsManager } from "../lib/ws-manager";
import { useScoresStore } from "../store/index";

/** Avoid spamming console if telemetry sends an unexpected nested row forever. */
let logged_scores_ws_row_failure = false;

/**
 * Subscribes to `/ws/scores` and writes score snapshots into {@link useScoresStore}.
 */
export function useScoresChannel(): void {
  const setScores = useScoresStore((state) => state.setScores);
  const setConnected = useScoresStore((state) => state.setConnected);

  useEffect(() => {
    const unsubscribe = wsManager.connect(
      "scores",
      (data) => {
        if (is_ws_parse_failure_frame(data)) {
          return;
        }
        if (Array.isArray(data)) {
          for (const row of data) {
            try {
              const payload = map_scores_ws_payload(row);
              setScores(payload.asset, payload);
            } catch {
              if (!logged_scores_ws_row_failure) {
                logged_scores_ws_row_failure = true;
                console.warn(
                  "[polaris] scores WebSocket skipped at least one malformed row — subsequent skips suppressed",
                );
              }
            }
          }
        } else {
          try {
            const payload = map_scores_ws_payload(data);
            setScores(payload.asset, payload);
          } catch {
            if (!logged_scores_ws_row_failure) {
              logged_scores_ws_row_failure = true;
              console.warn(
                "[polaris] scores WebSocket message could not be mapped — reconnect may recover",
              );
            }
          }
        }
        setConnected(true);
      },
      () => setConnected(true),
    );

    return () => {
      unsubscribe();
      setConnected(false);
    };
  }, [setScores, setConnected]);
}
