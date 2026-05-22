/**
 * Directional glyph for accessibility (pairs with bull/bear colouring).
 */
export function formatDecisionGlyph(decision: string): "▲" | "▼" | "→" {
  if (decision.startsWith("Strong Buy") || decision === "Buy") {
    return "▲";
  }
  if (decision.startsWith("Strong Sell") || decision === "Sell") {
    return "▼";
  }
  return "→";
}
