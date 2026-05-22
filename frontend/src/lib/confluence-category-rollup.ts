/**
 * Collapses flat orchestrator category_scores into the five dashboard pillars.
 * Must stay aligned with `atlas/api/routes/signals.py` `_signal_detail_category_max_scores`
 * and the REST `/api/signals/{asset}` payload consumed by `map_signal_detail_payload`.
 */
export interface UiFivePillarScores {
  derivatives: number;
  onchain: number;
  technical: number;
  sentiment: number;
  marketContext: number;
}

/** Accepts flat keys as emitted on Redis / WS (`derivatives`, `news_macro`, …). */
export function rollup_flat_category_scores_for_ui(extracted: Record<string, number>): UiFivePillarScores {
  const derivativesPillar =
    (extracted.derivatives ?? 0) + (extracted.liquidation ?? 0) + (extracted.funding ?? 0);
  const onchainPillar = (extracted.onchain ?? 0) + (extracted.whale ?? 0);
  const macroPillar =
    (extracted.regime ?? 0) +
    (extracted.correlation ?? 0) +
    (extracted.news_macro ?? 0) +
    (extracted.macro ?? 0) +
    (extracted.context ?? 0);

  return {
    derivatives: Math.round(derivativesPillar),
    onchain: Math.round(onchainPillar),
    technical: Math.round(extracted.technical ?? 0),
    sentiment: Math.round(extracted.sentiment ?? 0),
    marketContext: Math.round(extracted.marketContext ?? macroPillar),
  };
}
