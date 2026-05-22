import type { TelemetryRailEntry } from "../stores/telemetryRailStore";

export function is_critical_or_degraded_entry(row: TelemetryRailEntry): boolean {
  const upper = row.message.toUpperCase();
  return (
    upper.includes("CRITICAL") ||
    upper.includes("DEGRADED") ||
    row.level === "error" ||
    row.level === "warn"
  );
}

export function find_last_critical_entry(entries: readonly TelemetryRailEntry[]): TelemetryRailEntry | null {
  for (let index = entries.length - 1; index >= 0; index -= 1) {
    const row = entries[index];
    if (is_critical_or_degraded_entry(row)) {
      return row;
    }
  }
  return null;
}
