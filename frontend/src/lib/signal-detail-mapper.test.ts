import { describe, expect, it } from "vitest";

import {
  calculate_exchange_flow_section_open,
  calculate_obti_interpretation,
  calculate_options_flow_section_open,
  map_signal_detail_payload,
} from "./signal-detail-mapper";

describe("map_signal_detail_payload", () => {
  it("returns null for invalid payloads", () => {
    expect(map_signal_detail_payload(null)).toBeNull();
    expect(map_signal_detail_payload([])).toBeNull();
  });

  it("maps core hero fields and gate threshold from payload", () => {
    const vm = map_signal_detail_payload({
      asset: "SOL",
      price: "142.80",
      change24h: "0.032",
      cycleNumber: 1247,
      cycleTs: "2026-01-02T12:00:00Z",
      totalScore: 167,
      normalizedScore: 76,
      decision: "Buy",
      confidence: 0.78,
      passesGate: true,
      gateThreshold: 138,
      llmTierLabel: "DeepSeek V3 (FAST)",
      categoryScores: {
        derivatives: 30,
        onchain: 25,
        technical: 40,
        sentiment: 10,
        regime: 12,
      },
      obtiDetail: {
        side: "ask",
        level: "moderate",
        obtiBid: 0.18,
        obtiAsk: 0.42,
        moderateThreshold: 0.25,
        extremeThreshold: 0.55,
        samples: 120,
        minSamples: 30,
        isWarmingUp: false,
        history: [0.1, 0.2, 0.42],
        lastBookUpdateMs: 300,
      },
    });

    expect(vm).not.toBeNull();
    expect(vm?.gateThreshold).toBe(138);
    expect(vm?.llmTierLabel).toBe("DeepSeek V3 (FAST)");
    expect(vm?.obtiDetail?.obtiAsk).toBeCloseTo(0.42);
    expect(vm?.categoryScores.derivatives).toBe(30);
  });

  it("aggregates pillar contributions into five-bar buckets", () => {
    const vm = map_signal_detail_payload({
      asset: "BTC",
      price: "1",
      change24h: "0",
      cycleNumber: 1,
      cycleTs: "2026-01-02T12:00:00Z",
      totalScore: 50,
      normalizedScore: 22,
      decision: "Hold",
      confidence: 0.5,
      passesGate: false,
      gateThreshold: 140,
      categoryScores: {
        derivatives: 10,
        liquidation: 7,
        funding: 6,
        onchain: 20,
        whale: 15,
        technical: 8,
        sentiment: 12,
        regime: 5,
        correlation: 4,
        news_macro: 3,
        macro: 2,
      },
      categoryMaxScores: {
        derivatives: 75,
        onchain: 65,
        technical: 15,
        sentiment: 35,
        marketContext: 30,
      },
    });

    expect(vm?.categoryScores.derivatives).toBe(23);
    expect(vm?.categoryScores.onchain).toBe(35);
    expect(vm?.categoryScores.marketContext).toBe(14);
    expect(vm?.categoryMaxScores.derivatives).toBe(75);
  });
});

describe("calculate_obti_interpretation", () => {
  it("returns warming copy when samples accumulating", () => {
    const text = calculate_obti_interpretation({
      side: null,
      level: "balanced",
      obtiBid: 0,
      obtiAsk: 0,
      moderateThreshold: 0,
      extremeThreshold: 0,
      samples: 0,
      minSamples: 30,
      isWarmingUp: true,
      history: [],
      lastBookUpdateMs: 0,
    });
    expect(text).toContain("Accumulating samples");
  });
});

describe("provider section gates", () => {
  it("opens options section only when breaker closed", () => {
    expect(calculate_options_flow_section_open(null)).toBe(false);
    expect(
      calculate_options_flow_section_open({
        provider: { name: "Deribit", state: "OPEN" },
        dvol: null,
        atmIv: null,
        skew25d: null,
        vrp: null,
        callPutOiRatio: null,
        regime: null,
        unusualActivity: null,
      }),
    ).toBe(false);
    expect(
      calculate_options_flow_section_open({
        provider: { name: "Deribit", state: "CLOSED" },
        dvol: 68,
        atmIv: null,
        skew25d: null,
        vrp: null,
        callPutOiRatio: null,
        regime: null,
        unusualActivity: null,
      }),
    ).toBe(true);
  });

  it("opens exchange flow section only when breaker closed", () => {
    expect(
      calculate_exchange_flow_section_open({
        provider: { state: "CLOSED" },
        netFlow24h: "-1",
        flowZScore: -1,
        flowTrend: "ACCUMULATION",
        stablecoinReserves: null,
        signal: null,
      }),
    ).toBe(true);
  });
});
