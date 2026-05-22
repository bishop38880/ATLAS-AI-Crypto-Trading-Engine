import type { BadgeVariant } from "../components/ui/Badge";
import type { SignalDecision } from "../store/index";
import {
  CONFLUENCE_GATE_THRESHOLD_DEFAULT,
  CONFLUENCE_LADDER_THRESHOLD_T1,
  CONFLUENCE_LADDER_THRESHOLD_T3,
  CONFLUENCE_SCORE_CAP,
} from "./confluence-score-constants";
import { format_decision_strength_arrows } from "./signal-decision-display";

export interface ConfluenceActionBadgePresentation {
  /** Uppercase label with directional glyphs (e.g. `TRADE ↑↑`). */
  label: string;
  variant: BadgeVariant;
  /** Screen-reader description of ladder band + posture. */
  ariaLabel: string;
}

function calculate_directional_posture(decision: SignalDecision): "long" | "short" | "neutral" {
  if (decision === "Strong Buy" || decision === "Buy") {
    return "long";
  }
  if (decision === "Strong Sell" || decision === "Sell") {
    return "short";
  }
  return "neutral";
}

function build_label(prefix: "READY" | "TRADE" | "HOLD" | "BLOCKED" | "NO POSITION", decision: SignalDecision): string {
  if (prefix === "BLOCKED") {
    return "BLOCKED";
  }
  if (prefix === "NO POSITION") {
    return `NO POSITION ${format_decision_strength_arrows(decision)}`.trim();
  }
  const arrows = format_decision_strength_arrows(decision);
  return `${prefix} ${arrows}`.trim();
}

/**
 * Maps the 220-point confluence ladder + gate threshold to dashboard action badges so they stay
 * aligned with {@link ConfluenceThresholdScoreBar} segments (T1 → gate → T3 → cap).
 */
export function calculate_confluence_action_badge_presentation(input: {
  totalScoreCapped: number;
  gateThreshold: number;
  vetoActive: boolean;
  decision: SignalDecision;
}): ConfluenceActionBadgePresentation {
  const gate = Number.isFinite(input.gateThreshold) ? input.gateThreshold : CONFLUENCE_GATE_THRESHOLD_DEFAULT;
  const score = Math.min(CONFLUENCE_SCORE_CAP, Math.max(0, input.totalScoreCapped));
  const posture = calculate_directional_posture(input.decision);

  if (input.vetoActive) {
    return {
      label: "BLOCKED",
      variant: "degraded",
      ariaLabel: "Risk veto active; automatic ladder sizing is blocked.",
    };
  }

  if (score < CONFLUENCE_LADDER_THRESHOLD_T1) {
    return {
      label: build_label("NO POSITION", input.decision),
      variant: "no-position",
      ariaLabel:
        "Score is below the first trade ladder threshold; no automatic sizing tier applies yet.",
    };
  }

  if (score < gate) {
    return {
      label: build_label("HOLD", input.decision),
      variant: "hold",
      ariaLabel: `Watch band: score is between ${CONFLUENCE_LADDER_THRESHOLD_T1} and the conviction gate (${gate}); not in execution-ready posture.`,
    };
  }

  if (score < CONFLUENCE_LADDER_THRESHOLD_T3) {
    const variant: BadgeVariant =
      posture === "long" ? "healthy" : posture === "short" ? "sell" : "live";
    return {
      label: build_label("READY", input.decision),
      variant,
      ariaLabel: `Ready band: gate cleared (${gate}) through tier-three sizing (${CONFLUENCE_LADDER_THRESHOLD_T3}); arming posture before elite sizing.`,
    };
  }

  const elite_variant: BadgeVariant =
    posture === "long" ? "strong-buy" : posture === "short" ? "strong-sell" : "healthy";
  return {
    label: build_label("TRADE", input.decision),
    variant: elite_variant,
    ariaLabel: `Execution zone: score meets elite ladder tier (${CONFLUENCE_LADDER_THRESHOLD_T3}+); aligns with deepest sizing band on the meter.`,
  };
}
