import { describe, expect, it } from "vitest";

import {
  coerce_signal_decision_label,
  is_ws_parse_failure_frame,
  map_provider_row,
  map_scores_ws_payload,
  map_system_ws_payload,
} from "./channel-mappers";
import { CONFLUENCE_GATE_THRESHOLD_DEFAULT } from "./confluence-score-constants";

describe("is_ws_parse_failure_frame", () => {
  it("returns true for ws-manager parse_error envelope", () => {
    expect(is_ws_parse_failure_frame({ type: "parse_error", raw: "{bad" })).toBe(true);
  });

  it("returns false for normal payloads", () => {
    expect(is_ws_parse_failure_frame(null)).toBe(false);
    expect(is_ws_parse_failure_frame([])).toBe(false);
    expect(is_ws_parse_failure_frame({ asset: "BTC" })).toBe(false);
    expect(is_ws_parse_failure_frame({ type: "snapshot" })).toBe(false);
  });
});

describe("map_system_ws_payload", () => {
  it("maps regime context camelCase and snake_case", () => {
    const row = map_system_ws_payload({
      overallStatus: "HEALTHY",
      currentRegime: "volatile",
      regimeConfidence: 55,
      activeCycle: true,
      nextCycleSeconds: 120,
      openPositions: 0,
      portfolioPnl24h: "0",
      regime_context_asset: "BTC",
      regimeContextTimeframe: "4h",
      regime_context_as_of: "2026-05-15T12:00:00+00:00",
      regimeContextDurationBars: 7,
      regimeContextRunnerUp: "BULL next at ~30% HMM mass",
      regime_context_transition_hint: "HMM hint line",
    });
    expect(row.currentRegime).toBe("RANGING");
    expect(row.hmmRegimeNative).toBe("volatile");
    expect(row.regimeContextAsset).toBe("BTC");
    expect(row.regimeContextTimeframe).toBe("4h");
    expect(row.regimeContextAsOf).toBe("2026-05-15T12:00:00+00:00");
    expect(row.regimeContextDurationBars).toBe(7);
    expect(row.regimeContextRunnerUp).toContain("BULL");
    expect(row.regimeContextTransitionHint).toBe("HMM hint line");
  });
});

describe("map_provider_row", () => {
  it("maps camelCase REST payload", () => {
    const row = map_provider_row({
      name: "Coinalyze",
      tier: 1,
      trustRank: 1,
      state: "CLOSED",
      healthScore: 0.94,
      failureRate: 0.01,
      slowCallRate: 0.03,
      avgLatencyMs: 200,
      p50Ms: 180,
      p95Ms: 420,
      p99Ms: 890,
      requestsPerMin: 198,
      rateLimitMax: 300,
      lastFetchMs: 1_700_000_000_000,
      cacheStatus: "HIT",
      windowSize: 20,
      windowFailures: 0,
      windowSlowCalls: 1,
    });
    expect(row).not.toBeNull();
    expect(row?.p99Ms).toBe(890);
    expect(row?.trustRank).toBe(1);
    expect(row?.windowSlowCalls).toBe(1);
  });
});

describe("coerce_signal_decision_label", () => {
  it("derives from ladder when backend sends HOLD instead of trusting stale neutral tag", () => {
    expect(coerce_signal_decision_label("HOLD", 186)).toBe("Buy");
    expect(coerce_signal_decision_label("hold", 186)).toBe("Buy");
    expect(coerce_signal_decision_label("FLAT", 99)).toBe("Sell");
    expect(coerce_signal_decision_label("FLAT", 135)).toBe("Hold");
  });

  it("preserves directional labels from backend", () => {
    expect(coerce_signal_decision_label("LONG", 40)).toBe("Buy");
    expect(coerce_signal_decision_label("SHORT", 190)).toBe("Sell");
    expect(coerce_signal_decision_label("Strong Buy", 90)).toBe("Strong Buy");
  });
});

describe("map_scores_ws_payload", () => {
  it("defaults veto inactive and omit obti mapping when absent", () => {
    const row = map_scores_ws_payload({ asset: "SOL", totalScore: 140, decision: "Buy", confidence: 0.9 });
    expect(row.vetoActive).toBe(false);
    expect(row.obtiSummary).toBeNull();
  });

  it("treats balanced obti_summary as absent", () => {
    const row = map_scores_ws_payload({
      asset: "BTC",
      totalScore: 120,
      decision: "Hold",
      confidence: 0.5,
      obti_summary: "balanced",
    });
    expect(row.obtiSummary).toBeNull();
  });

  it("maps obti_summary and veto flags when present", () => {
    const row = map_scores_ws_payload({
      asset: "ETH",
      totalScore: 200,
      decision: "Strong Buy",
      veto_active: true,
      obti_summary: "extreme",
    });
    expect(row.vetoActive).toBe(true);
    expect(row.obtiSummary).toBe("extreme");
  });

  it("maps reasoning_summary and camelCase reasoningSummary for entry thesis", () => {
    const snake = map_scores_ws_payload({
      asset: "ETHUSDT",
      totalScore: 120,
      normalizedScore: 55,
      decision: "Buy",
      confidence: 0.66,
      categoryScores: { derivatives: 10, onchain: 10, technical: 10, sentiment: 10, marketContext: 10 },
      passesGate: true,
      reasoning_summary: "Scale in after funding flush stabilizes.",
    });
    expect(snake.reasoningSummary).toBe("Scale in after funding flush stabilizes.");

    const camel = map_scores_ws_payload({
      asset: "BTCUSDT",
      totalScore: 80,
      decision: "Hold",
      confidence: 0.5,
      reasoningSummary: "Range-bound until macro print.",
    });
    expect(camel.reasoningSummary).toBe("Range-bound until macro print.");
    expect(camel.decision).toBe("Strong Sell");
  });

  it("maps gateThreshold with default fallback from Polaris calibration default", () => {
    const implicit = map_scores_ws_payload({ asset: "BTC", totalScore: 120 });
    expect(implicit.gateThreshold).toBe(CONFLUENCE_GATE_THRESHOLD_DEFAULT);

    const explicit = map_scores_ws_payload({ asset: "BTC", totalScore: 120, gate_threshold: 155 });
    expect(explicit.gateThreshold).toBe(155);
  });

  it("rolls up granular category_scores into five pillars like REST signal detail", () => {
    const row = map_scores_ws_payload({
      asset: "SUIUSDT",
      totalScore: 98,
      category_scores: {
        derivatives: 42,
        liquidation: 30,
        funding: 21,
        onchain: 20,
        whale: 10,
        technical: 9,
        sentiment: 5,
        regime: 3,
        correlation: 0,
        news_macro: 0,
        macro: 0,
        context: 0,
      },
    });
    expect(row.categoryScores.derivatives).toBe(93);
    expect(row.categoryScores.onchain).toBe(30);
    expect(row.categoryScores.technical).toBe(9);
    expect(row.categoryScores.sentiment).toBe(5);
    expect(row.categoryScores.marketContext).toBe(3);
  });
});
