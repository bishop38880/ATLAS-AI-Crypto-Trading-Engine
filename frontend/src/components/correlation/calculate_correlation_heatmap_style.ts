/** Heat-map styling for Pearson ρ cells (daily log returns). */

export function calculate_correlation_cell_background(
  rho: number | null,
  options: { readonly is_diagonal: boolean; readonly is_extreme: boolean },
): string {
  if (options.is_diagonal) {
    return "rgba(148,163,184,0.22)";
  }
  if (rho === null) {
    return "rgba(15,23,42,0.92)";
  }
  const clamped = Math.min(1, Math.max(-1, rho));
  const magnitude = Math.abs(clamped);
  if (options.is_extreme || magnitude >= 0.92) {
    const alpha = 0.42 + magnitude * 0.38;
    return `rgba(248,113,113,${alpha.toFixed(3)})`;
  }
  if (magnitude >= 0.85) {
    const alpha = 0.28 + (magnitude - 0.85) * 4.0 * 0.25;
    return `rgba(251,146,60,${Math.min(0.72, alpha).toFixed(3)})`;
  }
  const hue = 215 - magnitude * 215;
  const light = 52 - magnitude * 18;
  const alpha = 0.14 + magnitude * 0.42;
  return `hsla(${hue.toFixed(1)}, 72%, ${light.toFixed(1)}%, ${alpha.toFixed(3)})`;
}

export function calculate_correlation_axis_class(monitored: boolean): string {
  const base =
    "font-mono text-[10px] font-bold uppercase tracking-tight text-[var(--text-secondary)] px-1 text-right";
  return monitored ? `${base} text-[var(--accent-cyan)] underline decoration-dotted underline-offset-2` : base;
}
