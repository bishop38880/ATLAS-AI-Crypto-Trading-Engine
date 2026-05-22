import type { SignalHistoryTableRow } from "./signal-history-mapper";

export interface SignalTimelineTick {
  id: string;
  asset: string;
  timestampIso: string;
  decision: string;
  totalScore: number;
}

/**
 * Filters persisted history rows into chronological ticks for the 24h ribbon.
 */
export function calculate_signal_timeline_ticks(
  rows: readonly SignalHistoryTableRow[],
  windowHours = 24,
  nowMs = Date.now(),
): SignalTimelineTick[] {
  const cutoff = nowMs - windowHours * 60 * 60 * 1000;
  const ticks: SignalTimelineTick[] = [];

  for (const row of rows) {
    const parsed = Date.parse(row.timestampIso);
    if (!Number.isFinite(parsed) || parsed < cutoff) {
      continue;
    }
    ticks.push({
      id: row.id,
      asset: row.asset,
      timestampIso: row.timestampIso,
      decision: row.decision,
      totalScore: row.totalScore,
    });
  }

  ticks.sort((a, b) => Date.parse(a.timestampIso) - Date.parse(b.timestampIso));
  return ticks;
}
