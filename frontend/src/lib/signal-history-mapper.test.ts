import { describe, expect, it } from "vitest";

import { map_signal_history_payload } from "./signal-history-mapper";

describe("map_signal_history_payload", () => {
  it("returns empty list for non-array payloads", () => {
    expect(map_signal_history_payload(null)).toEqual([]);
    expect(map_signal_history_payload({})).toEqual([]);
  });

  it("maps valid rows and rounds scores", () => {
    const raw = [
      {
        id: 1,
        asset: "BTC",
        timestamp: "2026-01-02T12:00:00+00:00",
        totalScore: 141.7,
        decision: "Buy",
        conviction: 0.82,
        passesGate: true,
      },
    ];
    expect(map_signal_history_payload(raw)).toEqual([
      {
        id: "1",
        asset: "BTC",
        timestampIso: "2026-01-02T12:00:00+00:00",
        totalScore: 142,
        decision: "Buy",
        conviction: 0.82,
        passesGate: true,
      },
    ]);
  });

  it("skips malformed records", () => {
    const raw = [{ id: 1, asset: "BTC" }, { not: "ok" }];
    expect(map_signal_history_payload(raw)).toEqual([]);
  });
});
