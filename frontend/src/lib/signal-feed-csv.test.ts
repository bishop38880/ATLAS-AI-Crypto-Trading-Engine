import { describe, expect, it } from "vitest";

import { calculate_signal_feed_csv } from "./signal-feed-csv";
import type { SignalFeedRow } from "./signal-feed-mapper";

describe("calculate_signal_feed_csv", () => {
  it("includes header row and escapes commas", () => {
    const rows: SignalFeedRow[] = [
      {
        asset: "BTC",
        timestamp: "2026-01-02T12:00:00Z",
        decision: "Buy, trimmed",
        totalScore: 160,
        normalizedScore: 73,
        confidence: 0.8,
        passesGate: true,
        gateThreshold: 140,
        obtiSummary: null,
        obtiSide: null,
        llmTierLabel: null,
        cycleNumber: null,
        price: "100",
        change24h: "0.01",
      },
    ];
    const csv = calculate_signal_feed_csv(rows);
    expect(csv.startsWith("asset,timestamp,decision,")).toBe(true);
    expect(csv).toContain('"Buy, trimmed"');
  });
});
