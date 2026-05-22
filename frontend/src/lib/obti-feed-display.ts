export interface ObtiFeedCell {
  /** Cell text including directional shape glyphs per FE invariant. */
  visual: string;
  /** Defined when the shape glyph carries toxicity meaning (moderate / extreme). */
  ariaLabel: string | undefined;
}

/**
 * Maps flattened OBTI summary fields from `/api/signals/feed` into table cell copy.
 * Balanced / absent payloads render a neutral dash — never raw order-book toxicity math client-side.
 */
export function calculate_obti_feed_cell(
  summary: string | null | undefined,
  side: string | null | undefined,
): ObtiFeedCell {
  const normalized = typeof summary === "string" ? summary.toLowerCase().trim() : "";
  if (normalized.length === 0 || normalized === "balanced" || normalized === "low") {
    return { visual: "─", ariaLabel: undefined };
  }

  const sideToken = side === "bid" || side === "ask" ? side : null;
  const sideLabel = sideToken !== null ? `${sideToken}-side` : "unknown side";

  if (normalized === "moderate") {
    return {
      visual: "◐ mod",
      ariaLabel: `Moderate order-book toxicity, ${sideLabel}`,
    };
  }

  if (normalized === "extreme") {
    return {
      visual: "● ext",
      ariaLabel: `Extreme order-book toxicity, ${sideLabel}`,
    };
  }

  return { visual: "─", ariaLabel: undefined };
}
