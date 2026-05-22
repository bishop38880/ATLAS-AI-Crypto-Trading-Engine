/** Pure helpers for Agent Zero Escore distribution bars. */

export function calculate_escore_bar_fill_percent(count: number, max_count: number): number {
  if (!Number.isFinite(count) || count < 0) {
    return 0;
  }
  if (!Number.isFinite(max_count) || max_count <= 0) {
    return 0;
  }
  const ratio = count / max_count;
  return Math.min(100, Math.round(ratio * 1000) / 10);
}

export function calculate_escore_band_track_class(status: string): string {
  const u = status.toUpperCase();
  if (u.includes("BORDERLINE")) {
    return "bg-[var(--hold)]";
  }
  if (u.includes("NOT_RETAINED") || u.includes("NOT RETAINED")) {
    return "bg-[var(--text-tertiary)]";
  }
  if (u.includes("ARCHIVED")) {
    return "bg-[var(--sell)]";
  }
  if (u.includes("RETAINED")) {
    return "bg-[var(--success)]";
  }
  return "bg-[var(--text-tertiary)]";
}
