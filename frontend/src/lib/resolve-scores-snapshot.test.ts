import { describe, expect, it } from "vitest";

import type { ScoresPayload } from "../store/index";
import { resolve_scores_snapshot_for_base } from "./resolve-scores-snapshot";

function make_snapshot(asset: string, total: number): ScoresPayload {
  return {
    asset,
    totalScore: total,
    normalizedScore: 50,
    decision: "Hold",
    confidence: 0.5,
    categoryScores: {
      derivatives: 10,
      onchain: 10,
      technical: 10,
      sentiment: 10,
      marketContext: 10,
    },
    passesGate: true,
    gateThreshold: 100,
    r1LlmInvoked: false,
    llmModelLabel: "test",
    llmTierLabel: "test",
    llmLatencySeconds: null,
    vetoActive: false,
    obtiSummary: null,
    obtiSide: null,
    marlRevision: null,
    cycleNumber: null,
    cycleTimestamp: "2026-01-01T00:00:00Z",
    cycleTs: "2026-01-01T00:00:00Z",
    price: "0",
    fundingRate: "0",
    openInterest: "0",
    reasoningSummary: "",
  };
}

describe("resolve_scores_snapshot_for_base", () => {
  it("matches BTCUSDT when selector is BTC", () => {
    const m = new Map<string, ScoresPayload>();
    m.set("BTCUSDT", make_snapshot("BTCUSDT", 80));
    const row = resolve_scores_snapshot_for_base(m, "BTC");
    expect(row?.totalScore).toBe(80);
  });

  it("matches lowercase map keys when dashboard pair is BASE/USDT", () => {
    const m = new Map<string, ScoresPayload>();
    m.set("btcusdt", make_snapshot("btcusdt", 72));
    const row = resolve_scores_snapshot_for_base(m, "BTC/USDT");
    expect(row?.totalScore).toBe(72);
  });

  it("does not fall back to a different asset", () => {
    const m = new Map<string, ScoresPayload>();
    m.set("BTCUSDT", make_snapshot("BTCUSDT", 80));
    const row = resolve_scores_snapshot_for_base(m, "ETH");
    expect(row).toBeUndefined();
  });

  it("prefers newest cycle when multiple keys alias the same base", () => {
    const m = new Map<string, ScoresPayload>();
    const older = make_snapshot("BTCUSDT", 80);
    const newer = {
      ...make_snapshot("BTC/USDT", 95),
      cycleTs: "2026-01-01T01:00:00Z",
    };
    m.set("BTCUSDT", older);
    m.set("BTC/USDT", newer);
    const row = resolve_scores_snapshot_for_base(m, "BTC");
    expect(row?.totalScore).toBe(95);
  });

  it("ignores aggregate snapshots when resolving per-asset base", () => {
    const m = new Map<string, ScoresPayload>();
    m.set("__aggregate__", make_snapshot("__aggregate__", 99));
    m.set("ETHUSDT", make_snapshot("ETHUSDT", 42));
    const row_btc = resolve_scores_snapshot_for_base(m, "BTC");
    const row_eth = resolve_scores_snapshot_for_base(m, "ETH");
    expect(row_btc).toBeUndefined();
    expect(row_eth?.totalScore).toBe(42);
  });

  it("falls back to payload asset when map key shape differs", () => {
    const m = new Map<string, ScoresPayload>();
    m.set("stream:last", make_snapshot("SOLUSDT", 77));
    const row = resolve_scores_snapshot_for_base(m, "SOL");
    expect(row?.totalScore).toBe(77);
  });
});
