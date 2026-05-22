/** Normalize backend portfolio PnL strings (fraction or explicit percent). */

export interface PortfolioPnlDisplay {
  text: string;
  /** Hue bias for green/red pairings paired with glyphs elsewhere. */
  is_positive_bias: boolean;
}

export function format_portfolio_pnl_bias_display(raw: string): PortfolioPnlDisplay {
  const trimmed = raw.trim();
  if (trimmed.length === 0 || trimmed === "0") {
    return { text: "0%", is_positive_bias: true };
  }

  if (trimmed.includes("%")) {
    const stripped = trimmed.replace(/%/g, "").replace(",", "").trim();
    const numeric_signed = Number.parseFloat(stripped);
    const bias = Number.isFinite(numeric_signed) ? numeric_signed >= 0 : true;
    return { text: trimmed, is_positive_bias: bias };
  }

  let numeric = Number.parseFloat(trimmed.replace(/,/g, ""));
  if (!Number.isFinite(numeric)) {
    return { text: trimmed, is_positive_bias: true };
  }

  if (numeric !== 0 && Math.abs(numeric) <= 1) {
    numeric *= 100;
  }

  const text = `${numeric >= 0 ? "+" : ""}${numeric.toFixed(1)}%`;
  return { text, is_positive_bias: numeric >= 0 };
}
