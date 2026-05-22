export interface GnnObtiGlyphCell {
  visual: string;
  ariaLabel: string | undefined;
  levelLabel: string;
}

/**
 * Maps flattened OBTI summary strings into single-glyph row visuals (─ / ◐ / ●).
 * Frontend never derives toxicity from raw book data — only displays backend summaries.
 */
export function calculate_gnn_obti_glyph_cell(
  summary: string | null | undefined,
  side: string | null | undefined,
  assetBase: string,
): GnnObtiGlyphCell {
  const normalized = typeof summary === "string" ? summary.toLowerCase().trim() : "";
  const sym = assetBase.trim().toUpperCase();

  if (normalized.length === 0 || normalized === "balanced" || normalized === "low") {
    return { visual: "─", ariaLabel: undefined, levelLabel: "balanced" };
  }

  const side_token = side === "bid" || side === "ask" ? side : null;
  const side_label = side_token !== null ? `${side_token}-side` : "unknown-side";

  if (normalized === "moderate") {
    return {
      visual: "◐",
      ariaLabel: `Asset ${sym}: OBTI moderate, ${side_label}`,
      levelLabel: "moderate",
    };
  }

  if (normalized === "extreme") {
    return {
      visual: "●",
      ariaLabel: `Asset ${sym}: OBTI extreme, ${side_label}`,
      levelLabel: "extreme",
    };
  }

  return { visual: "─", ariaLabel: undefined, levelLabel: "balanced" };
}
