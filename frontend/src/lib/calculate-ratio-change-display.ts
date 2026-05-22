export interface RatioChangeDisplay {
  glyph: "▲" | "▼" | "→";
  label: string;
}

/** Formats a dimensionless 24h ratio string (e.g. `0.032`) into glyph + percent label. */
export function calculate_ratio_change_display(ratioString: string): RatioChangeDisplay {
  const trimmed = ratioString.trim();
  if (trimmed.length === 0 || trimmed === "—") {
    return { glyph: "→", label: "—" };
  }

  const n = Number(trimmed);
  if (!Number.isFinite(n)) {
    return { glyph: "→", label: "—" };
  }
  const pct = n * 100;
  const glyph: RatioChangeDisplay["glyph"] = pct > 0 ? "▲" : pct < 0 ? "▼" : "→";
  const sign = pct > 0 ? "+" : "";
  return { glyph, label: `${sign}${pct.toFixed(1)}%` };
}
