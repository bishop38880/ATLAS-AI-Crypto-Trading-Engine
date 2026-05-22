import {
  CONFLUENCE_LADDER_THRESHOLD_T1,
  CONFLUENCE_LADDER_THRESHOLD_T2,
  CONFLUENCE_LADDER_THRESHOLD_T3,
} from "./confluence-score-constants";

/**
 * Confluence card score tiers — left-edge accent for dashboard grid scanning.
 *
 * Bounds match {@link CONFLUENCE_LADDER_THRESHOLD_T1|T1}–{@link CONFLUENCE_LADDER_THRESHOLD_T3|T3}:
 * - below T1: muted grey · T1–(T2−1): amber · T2–(T3−1): orange · T3+: success + pulse
 */

export type ConfluenceCardScoreTier = "muted" | "amber" | "orange" | "elite";

export function calculate_confluence_card_score_tier(total_score_capped: number): ConfluenceCardScoreTier {
  if (total_score_capped >= CONFLUENCE_LADDER_THRESHOLD_T3) {
    return "elite";
  }

  if (total_score_capped >= CONFLUENCE_LADDER_THRESHOLD_T2) {
    return "orange";
  }

  if (total_score_capped >= CONFLUENCE_LADDER_THRESHOLD_T1) {
    return "amber";
  }

  return "muted";
}

const tier_left_border: Record<ConfluenceCardScoreTier, string> = {
  muted: "border-l-[4px] border-l-[rgba(148,163,184,0.38)]",
  amber: "border-l-[4px] border-l-[var(--accent-amber)]",
  orange: "border-l-[4px] border-l-[var(--degraded)]",
  elite: "border-l-[4px] border-l-[var(--success)] health-pulse",
};

/**
 * Tailwind classes for left border accent + optional elite pulse. Veto replaces tier with danger.
 */
export function calculate_confluence_card_tier_border_classes(params: {
  total_score_capped: number;
  veto_active: boolean;
}): string {
  if (params.veto_active) {
    return "border-l-[4px] border-l-[var(--danger)]";
  }

  const tier = calculate_confluence_card_score_tier(params.total_score_capped);
  return tier_left_border[tier];
}
