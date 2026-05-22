/** Canonical confluence ladder cap for UI normalization (distinct from veto agents). */
export const CONFLUENCE_SCORE_CAP = 220;

/**
 * Raw-score trade ladder thresholds (position sizing bands).
 * UI segments: [0–T1) no trade, [T1–T2) 2% @ 2×, [T2–T3) 3% @ 3×, [T3–cap] 5% @ 5×.
 */
export const CONFLUENCE_LADDER_THRESHOLD_T1 = 120;
export const CONFLUENCE_LADDER_THRESHOLD_T2 = 150;
export const CONFLUENCE_LADDER_THRESHOLD_T3 = 180;

/** Default gate threshold when the snapshot omits `gateThreshold` (mirrors PolarisSettings.router_gated_threshold). */
export const CONFLUENCE_GATE_THRESHOLD_DEFAULT = 140;

/** Seconds between autonomous scoring waves — keep in sync with PolarisSettings.confluence_cycle_interval_seconds (default 900). */
export const CONFLUENCE_CYCLE_INTERVAL_SECONDS = 900;

/**
 * Confluence score is treated as stale for dashboard emphasis when its cycle timestamp
 * is older than this multiple of {@link CONFLUENCE_CYCLE_INTERVAL_SECONDS} (matches “Last: …” copy).
 */
export const CONFLUENCE_SCORE_STALE_CYCLE_MULTIPLIER = 2;

/** Per-pillar caps for the five-ladder breakdown — mirrors `ConfluenceScoringEngine.CATEGORY_MAXES`. */
export const CONFLUENCE_CATEGORY_MAX = {
  derivatives: 75,
  onchain: 65,
  technical: 15,
  sentiment: 35,
  marketContext: 30,
} as const;

export type ConfluenceCategoryScoreKey = keyof typeof CONFLUENCE_CATEGORY_MAX;
