import type { BadgeVariant } from "../components/ui/Badge";
import type { SignalDecision } from "../store/index";

/** Extra arrows for actionable decisions (paired with glyphs for accessibility). */
export function format_decision_strength_arrows(decision: SignalDecision): string {
  if (decision === "Strong Buy") {
    return "↑↑";
  }
  if (decision === "Strong Sell") {
    return "↓↓";
  }
  if (decision === "Buy" || decision === "Sell") {
    return decision === "Buy" ? "↑" : "▼";
  }
  return "→";
}

/** Plain-language description for badges that concatenate uppercase labels + glyphs. */
export function calculate_decision_accessibility_label(decision: SignalDecision): string {
  switch (decision) {
    case "Strong Buy":
      return "Strong buy, pronounced upward directional bias.";
    case "Buy":
      return "Buy bias, directional arrow upward.";
    case "Strong Sell":
      return "Strong sell, pronounced downward directional bias.";
    case "Sell":
      return "Sell bias, directional arrow downward.";
    case "Hold":
      return "Hold, sideways directional bias.";
    default:
      return "No actionable position.";
  }
}

export function calculate_signal_badge_variant(decision: SignalDecision): BadgeVariant {
  switch (decision) {
    case "Strong Buy":
      return "strong-buy";
    case "Buy":
      return "buy";
    case "Hold":
      return "hold";
    case "Sell":
      return "sell";
    case "Strong Sell":
      return "strong-sell";
    default:
      return "no-position";
  }
}

export function calculate_signal_filter_match(decision: SignalDecision): boolean {
  return (
    decision === "Strong Buy" ||
    decision === "Buy" ||
    decision === "Strong Sell" ||
    decision === "Sell"
  );
}
