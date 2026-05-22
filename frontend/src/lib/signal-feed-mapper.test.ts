import { describe, expect, it } from "vitest";

import { coerce_signal_feed_rows, map_signal_feed_payload } from "./signal-feed-mapper";

describe("coerce_signal_feed_rows", () => {
  it("returns empty array when value is not an array", () => {
    expect(coerce_signal_feed_rows(undefined)).toEqual([]);
    expect(coerce_signal_feed_rows(null)).toEqual([]);
    expect(coerce_signal_feed_rows({ rows: [] })).toEqual([]);
  });

  it("returns the same array reference for arrays", () => {
    const a: unknown[] = [1, 2];
    expect(coerce_signal_feed_rows(a)).toBe(a);
  });
});

describe("map_signal_feed_payload", () => {
  it("returns empty list for non-array payloads", () => {
    expect(map_signal_feed_payload(null)).toEqual([]);
    expect(map_signal_feed_payload({})).toEqual([]);
  });

  it("maps camelCase feed rows", () => {
    const raw = [
      {
        asset: "BTC",
        timestamp: "2026-01-02T12:00:00+00:00",
        decision: "Strong Buy",
        totalScore: 188.4,
        normalizedScore: 86,
        confidence: 0.91,
        passesGate: true,
        gateThreshold: 135,
        obtiSummary: "moderate",
        obtiSide: "ask",
        llmTierLabel: "DeepSeek V3 (FAST)",
        cycleNumber: 99,
        price: "98234.5",
        change24h: "0.012",
      },
    ];
    expect(map_signal_feed_payload(raw)).toEqual([
      {
        asset: "BTC",
        timestamp: "2026-01-02T12:00:00+00:00",
        decision: "Strong Buy",
        totalScore: 188,
        normalizedScore: 86,
        confidence: 0.91,
        passesGate: true,
        gateThreshold: 135,
        obtiSummary: "moderate",
        obtiSide: "ask",
        llmTierLabel: "DeepSeek V3 (FAST)",
        cycleNumber: 99,
        price: "98234.5",
        change24h: "0.012",
      },
    ]);
  });

  it("skips malformed records", () => {
    expect(map_signal_feed_payload([{ decision: "Hold" }])).toEqual([]);
  });
});
