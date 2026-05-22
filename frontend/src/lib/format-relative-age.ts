/** Age helpers for websocket ISO timestamps surfaced as human copy. */

export function calculate_age_ms_from_iso(timestamp_iso: string | null | undefined): number | null {
  if (timestamp_iso === null || timestamp_iso === undefined || timestamp_iso.length === 0) {
    return null;
  }

  const parsed = Date.parse(timestamp_iso);
  if (!Number.isFinite(parsed)) {
    return null;
  }

  const deltaMs = Date.now() - parsed;
  if (!Number.isFinite(deltaMs)) {
    return null;
  }
  return deltaMs >= 0 ? deltaMs : 0;
}

export function calculate_age_minutes_from_iso(timestamp_iso: string | null | undefined): number | null {
  const deltaMs = calculate_age_ms_from_iso(timestamp_iso);
  if (deltaMs === null) {
    return null;
  }

  const minutes = Math.floor(delta_ms_to_minutes(deltaMs));
  return minutes;
}

function delta_ms_to_minutes(delta_ms: number): number {
  return delta_ms / 60_000;
}

export function format_last_compact_from_iso(timestamp_iso: string | null | undefined): string {
  const minutes = calculate_age_minutes_from_iso(timestamp_iso);
  return format_minutes_since_label(minutes);
}

export function format_minutes_since_label(minutes: number | null, unknown_label = "—"): string {
  if (minutes === null || !Number.isFinite(minutes)) {
    return unknown_label;
  }
  if (minutes < 1) {
    return "Just now";
  }
  if (minutes === 1) {
    return "1m ago";
  }
  if (minutes < 120) {
    return `${minutes}m ago`;
  }

  const hours = Math.round(minutes / 60);
  if (hours < 48) {
    return `${hours}h ago`;
  }

  const days = Math.round(hours / 24);
  return `${days}d ago`;
}

export function calculate_dashboard_stale_best_iso(
  price_iso: string | null | undefined,
  scores_iso: string | null | undefined,
): string | null {
  const stamps = [price_iso, scores_iso].filter((v): v is string => typeof v === "string" && v.trim().length > 0);

  let latest: number | null = null;
  for (const iso of stamps) {
    const parsed = Date.parse(iso);
    if (!Number.isFinite(parsed)) {
      continue;
    }
    if (latest === null || parsed > latest) {
      latest = parsed;
    }
  }
  if (latest === null) {
    return null;
  }
  return new Date(latest).toISOString();
}

export function calculate_is_dashboard_card_stale(
  price_iso: string | null | undefined,
  scores_iso: string | null | undefined,
  stale_minutes = 5,
): boolean {
  const best_iso = calculate_dashboard_stale_best_iso(price_iso, scores_iso);
  const minutes = calculate_age_minutes_from_iso(best_iso);
  if (minutes === null) {
    return false;
  }
  return minutes >= stale_minutes;
}

/** True when the score cycle timestamp is strictly older than `multiplier × cycle_interval_seconds` (same basis as “Last: … ago”). */
export function calculate_is_score_cycle_stale(
  cycle_iso: string | null | undefined,
  cycle_interval_seconds: number,
  multiplier: number,
): boolean {
  if (cycle_interval_seconds <= 0 || multiplier <= 0) {
    return false;
  }

  const age_ms = calculate_age_ms_from_iso(cycle_iso);
  if (age_ms === null) {
    return false;
  }

  const threshold_ms = multiplier * cycle_interval_seconds * 1000;
  return age_ms > threshold_ms;
}
