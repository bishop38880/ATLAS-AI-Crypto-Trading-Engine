import { describe, expect, it } from "vitest";

import type { SignalHistoryTableRow } from "./signal-history-mapper";
import { calculate_signal_timeline_ticks } from "./signal-timeline";

describe("calculate_signal_timeline_ticks", () => {
  it("keeps rows inside the window and sorts ascending by time", () => {
    const now = Date.parse("2026-01-10T12:00:00Z");
    const rows: SignalHistoryTableRow[] = [
      {
        id: "2",
        asset: "ETH",
        timestampIso: "2026-01-10T11:00:00Z",
        totalScore: 120,
        decision: "Hold",
        conviction: 0.5,
        passesGate: false,
      },
      {
        id: "1",
        asset: "BTC",
        timestampIso: "2026-01-09T10:00:00Z",
        totalScore: 99,
        decision: "Buy",
        conviction: 0.6,
        passesGate: true,
      },
    ];
    const ticks = calculate_signal_timeline_ticks(rows, 24, now);
    expect(ticks).toHaveLength(1);
    expect(ticks[0]?.asset).toBe("ETH");
  });
});
