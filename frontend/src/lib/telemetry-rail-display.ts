import type { TelemetryRailEntry } from "../stores/telemetryRailStore";

export type TelemetryFilter = "all" | "scores" | "system" | "regime";

export type TelemetryTimeBucketLabel = "Last 60s" | "Earlier";

export interface TelemetryTimeBucket {
  readonly label: TelemetryTimeBucketLabel;
  readonly entries: TelemetryRailEntry[];
}

const RECENT_WINDOW_MS = 60_000;

function is_scores_entry(row: TelemetryRailEntry): boolean {
  const source = row.source.toLowerCase();
  const message = row.message.toLowerCase();
  return source === "scores" || message.includes("confluence snapshot");
}

function is_system_entry(row: TelemetryRailEntry): boolean {
  return !is_scores_entry(row);
}

/** Regime rail entries plus platform status transitions. */
export function is_regime_context_entry(row: TelemetryRailEntry): boolean {
  const source = row.source.toLowerCase();
  if (source === "regime") {
    return true;
  }
  if (source === "system" && row.message.includes("→")) {
    return true;
  }
  return false;
}

export function matches_telemetry_filter(row: TelemetryRailEntry, filter: TelemetryFilter): boolean {
  if (filter === "all") {
    return true;
  }
  if (filter === "scores") {
    return is_scores_entry(row);
  }
  if (filter === "regime") {
    return is_regime_context_entry(row);
  }
  return is_system_entry(row);
}

/** Split visible entries into recent and older buckets for the live rail. */
export function group_telemetry_entries_by_time(
  entries: TelemetryRailEntry[],
  now_ms: number = Date.now(),
): TelemetryTimeBucket[] {
  const cutoff_ms = now_ms - RECENT_WINDOW_MS;
  const recent: TelemetryRailEntry[] = [];
  const earlier: TelemetryRailEntry[] = [];

  for (const row of entries) {
    if (row.createdAtMs >= cutoff_ms) {
      recent.push(row);
    } else {
      earlier.push(row);
    }
  }

  const buckets: TelemetryTimeBucket[] = [];
  if (earlier.length > 0) {
    buckets.push({ label: "Earlier", entries: earlier });
  }
  if (recent.length > 0) {
    buckets.push({ label: "Last 60s", entries: recent });
  }
  return buckets;
}
