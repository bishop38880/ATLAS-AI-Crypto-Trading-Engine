import { useRealTimeData } from "./useRealTimeData";
import { parse_gnn_shadow_payload, type GnnShadowSnapshot } from "../lib/parse-gnn-shadow-payload";

const STALE_MS = 120_000;

/** `/ws/gnn` + REST fallback `/api/gnn/shadow` (5s when WS stale/down). */
export function useGnnShadowChannel() {
  return useRealTimeData<GnnShadowSnapshot>("gnn_channel", "/ws/gnn", null, {
    parse: parse_gnn_shadow_payload,
    pollUrl: "/api/gnn/shadow",
    staleMs: STALE_MS,
    pollMs: 5_000,
  });
}
