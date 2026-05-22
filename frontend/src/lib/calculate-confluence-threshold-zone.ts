import {
  CONFLUENCE_LADDER_THRESHOLD_T1,
  CONFLUENCE_LADDER_THRESHOLD_T2,
  CONFLUENCE_LADDER_THRESHOLD_T3,
  CONFLUENCE_SCORE_CAP,
} from "./confluence-score-constants";

export type ConfluenceThresholdZoneId = "none" | "tier_2x" | "tier_3x" | "tier_5x";

export interface ConfluenceThresholdZoneInfo {
  zone: ConfluenceThresholdZoneId;
  /** Short label for UI / aria. */
  label: string;
}

/**
 * Returns the sizing tier band for a raw confluence score on the 220-point ladder.
 */
export function calculate_confluence_threshold_zone(
  value: number,
  cap: number = CONFLUENCE_SCORE_CAP,
): ConfluenceThresholdZoneInfo {
  const safe_cap = cap > 0 ? cap : CONFLUENCE_SCORE_CAP;
  const v = Math.min(safe_cap, Math.max(0, value));

  if (v < CONFLUENCE_LADDER_THRESHOLD_T1) {
    return { zone: "none", label: "Below trade threshold (no automatic tier)" };
  }
  if (v < CONFLUENCE_LADDER_THRESHOLD_T2) {
    return { zone: "tier_2x", label: "2% at 2× leverage tier" };
  }
  if (v < CONFLUENCE_LADDER_THRESHOLD_T3) {
    return { zone: "tier_3x", label: "3% at 3× leverage tier" };
  }
  return { zone: "tier_5x", label: "5% at 5× leverage tier" };
}

/**
 * Needle horizontal position as percent of track width (0–100).
 */
export function calculate_confluence_needle_left_percent(
  value: number,
  cap: number = CONFLUENCE_SCORE_CAP,
): number {
  const safe_cap = cap > 0 ? cap : CONFLUENCE_SCORE_CAP;
  const t = Math.min(safe_cap, Math.max(0, value)) / safe_cap;
  return t * 100;
}
