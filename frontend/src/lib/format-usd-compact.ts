/**
 * Fiat notionals transmitted as Decimal strings stay strings until formatted for display.
 */
export function format_usd_compact_display(raw: string, placeholder = "—"): string {
  const trimmed = raw.trim();
  if (trimmed.length === 0 || trimmed === placeholder) {
    return placeholder;
  }

  if (trimmed.startsWith("$")) {
    return trimmed;
  }

  const sanitized = trimmed.replace(/,/g, "");
  const value = Number(sanitized);
  if (!Number.isFinite(value)) {
    return trimmed;
  }

  const sign = value < 0 ? "-" : "";
  const magnitude = Math.abs(value);

  if (magnitude >= 1e12) {
    return `${sign}$${trim_trailing_zeros(magnitude / 1e12)}T`;
  }
  if (magnitude >= 1e9) {
    return `${sign}$${trim_trailing_zeros(magnitude / 1e9)}B`;
  }
  if (magnitude >= 1e6) {
    return `${sign}$${trim_trailing_zeros(magnitude / 1e6)}M`;
  }
  if (magnitude >= 1e3) {
    return `${sign}$${trim_trailing_zeros(magnitude / 1e3)}K`;
  }
  return `${sign}$${trim_trailing_zeros(magnitude)}`;
}

function trim_trailing_zeros(value: number): string {
  const text = value.toFixed(2);
  if (text.endsWith(".00")) {
    return text.slice(0, -3);
  }
  if (text.endsWith("0")) {
    return text.replace(/\.0+$/, "").replace(/\.$/, "");
  }
  return text;
}
