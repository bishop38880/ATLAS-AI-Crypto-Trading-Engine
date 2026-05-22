import type { ScoresPayload } from "../store/index";

import { derive_base_asset_from_pair } from "./dashboard-symbol";

function is_scores_aggregate_snapshot(snapshot: ScoresPayload): boolean {
  return snapshot.asset.trim().toUpperCase() === "__AGGREGATE__";
}

function pick_newest_by_cycle_ts(candidates: ScoresPayload[]): ScoresPayload | undefined {
  if (candidates.length === 0) {
    return undefined;
  }
  if (candidates.length === 1) {
    return candidates[0];
  }
  return candidates.reduce((best, cur) => {
    const best_ts = Date.parse(best.cycleTs);
    const cur_ts = Date.parse(cur.cycleTs);
    const best_ok = Number.isFinite(best_ts);
    const cur_ok = Number.isFinite(cur_ts);
    if (cur_ok && (!best_ok || cur_ts >= best_ts)) {
      return cur;
    }
    return best;
  });
}

/**
 * Resolve the websocket snapshot whose asset base matches `base` (e.g. BTC vs BTCUSDT).
 * Prefers map-key matches, then payload `asset` field; skips aggregate rows; if several rows
 * match (synonym keys), returns the newest by `cycleTs` so UI updates correctly when changing asset.
 */
export function resolve_scores_snapshot_for_base(
  scoresByAsset: Map<string, ScoresPayload>,
  base: string,
): ScoresPayload | undefined {
  const want = derive_base_asset_from_pair(base).toUpperCase();

  const matched_by_key: ScoresPayload[] = [];
  for (const [key, snapshot] of scoresByAsset.entries()) {
    if (is_scores_aggregate_snapshot(snapshot)) {
      continue;
    }
    if (derive_base_asset_from_pair(key).toUpperCase() === want) {
      matched_by_key.push(snapshot);
    }
  }

  const keyed = pick_newest_by_cycle_ts(matched_by_key);
  if (keyed !== undefined) {
    return keyed;
  }

  const matched_by_payload: ScoresPayload[] = [];
  for (const [, snapshot] of scoresByAsset.entries()) {
    if (is_scores_aggregate_snapshot(snapshot)) {
      continue;
    }
    if (derive_base_asset_from_pair(snapshot.asset).toUpperCase() === want) {
      matched_by_payload.push(snapshot);
    }
  }

  return pick_newest_by_cycle_ts(matched_by_payload);
}
