import { describe, expect, it } from "vitest";

import { calculate_dashboard_attention_items } from "./calculate-dashboard-attention";
import type { ScoresPayload, SystemHealth } from "../store/index";

function base_health(overrides: Partial<SystemHealth> = {}): SystemHealth {
  return {
    overallStatus: "HEALTHY",
    currentRegime: "RANGING",
    regimeConfidence: 55,
    activeCycle: true,
    nextCycleSeconds: 120,
    openPositions: 0,
    portfolioPnl24h: "0",
    ...overrides,
  };
}

function score_row(asset: string, overrides: Partial<ScoresPayload> = {}): ScoresPayload {
  return {
    asset,
    totalScore: 160,
    normalizedScore: 0.7,
    decision: "Buy",
    confidence: 0.7,
    categoryScores: {
      derivatives: 10,
      onchain: 10,
      technical: 5,
      sentiment: 5,
      marketContext: 5,
    },
    passesGate: true,
    gateThreshold: 140,
    r1LlmInvoked: true,
    llmModelLabel: "deepseek",
    llmTierLabel: "R1",
    llmLatencySeconds: 1,
    vetoActive: false,
    obtiSummary: null,
    obtiSide: null,
    marlRevision: null,
    cycleNumber: 1,
    cycleTimestamp: "2026-05-22T12:00:00.000Z",
    cycleTs: "2026-05-22T12:00:00.000Z",
    price: "100",
    fundingRate: "0.01",
    openInterest: "1M",
    reasoningSummary: "",
    ...overrides,
  };
}

describe("calculate_dashboard_attention_items", () => {
  it("flags halted platform and volatile regime mask", () => {
    const rows = calculate_dashboard_attention_items({
      health: base_health({
        overallStatus: "HALTED",
        hmmRegimeNative: "volatile",
        currentRegime: "RANGING",
      }),
      pairs: ["BTC"],
      scores_by_asset: new Map(),
      slot_snapshots: [],
      scores_connected: true,
      prices_connected: true,
    });

    expect(rows.some((row) => row.id === "platform-halted")).toBe(true);
    expect(rows.some((row) => row.id === "regime-volatile-mask")).toBe(true);
  });

  it("groups veto and ready-unslotted signals", () => {
    const rows = calculate_dashboard_attention_items({
      health: base_health(),
      pairs: ["BTC", "ETH"],
      scores_by_asset: new Map([
        ["BTC", score_row("BTC", { vetoActive: true })],
        ["ETH", score_row("ETH", { decision: "Strong Buy" })],
      ]),
      slot_snapshots: [{ slot_index: 1, asset: "BTC", direction: "LONG", unrealized_pnl_percent: "1.2" }],
      scores_connected: true,
      prices_connected: true,
    });

    expect(rows.some((row) => row.id === "veto-active" && row.label.includes("BTC"))).toBe(true);
    expect(rows.some((row) => row.id === "signals-ready")).toBe(true);
  });
});
