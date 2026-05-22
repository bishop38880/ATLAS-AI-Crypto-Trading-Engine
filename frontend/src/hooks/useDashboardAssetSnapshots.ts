import { useCallback, useMemo } from "react";

import { calculate_symbol_keys_deduped } from "../lib/dashboard-symbol";
import { resolve_scores_snapshot_for_base } from "../lib/resolve-scores-snapshot";
import { usePricesStore, useScoresStore } from "../store/index";

import type { PricePayload } from "../store/index";
import type { ScoresPayload } from "../store/index";

/** Resolves websocket snapshots by base asset so map keys match prices channel (any casing / alias). */
export function useScoresSnapshotForDashboardPair(pair: string): ScoresPayload | undefined {
  return useScoresStore(
    useCallback(
      (state) => resolve_scores_snapshot_for_base(state.scoresByAsset, pair),
      [pair],
    ),
  );
}

/** Three-second prices channel multiplexed safely across synonym symbols. */
export function usePriceSnapshotForDashboardPair(pair: string): PricePayload | undefined {
  const lookup_keys = useMemo(() => calculate_symbol_keys_deduped(pair), [pair]);
  const upper_aliases = useMemo(() => lookup_keys.map((token) => token.toUpperCase()), [lookup_keys]);

  return usePricesStore(
    useCallback((state) => {
      for (const key of lookup_keys) {
        const row = state.prices.get(key);
        if (row) {
          return row;
        }
      }

      for (const [symbol, payload] of state.prices.entries()) {
        const symbol_upper = symbol.toUpperCase();
        if (upper_aliases.includes(symbol_upper)) {
          return payload;
        }
      }

      return undefined;
    }, [lookup_keys, upper_aliases]),
  );
}
