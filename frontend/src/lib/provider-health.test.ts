import { describe, expect, it } from "vitest";

import {
  calculate_breaker_timeline,
  calculate_health_bar_ratio,
  calculate_tier_health_counts,
  calculate_total_requests_per_min,
  latency_color_class,
  list_tier1_not_closed,
} from "./provider-health";
import type { ProviderHealth } from "../store/index";

function p(over: Partial<ProviderHealth>): ProviderHealth {
  return {
    name: "Test",
    tier: 1,
    trustRank: 1,
    state: "CLOSED",
    healthScore: 1,
    failureRate: 0,
    slowCallRate: 0,
    avgLatencyMs: 0,
    p50Ms: 0,
    p95Ms: 0,
    p99Ms: 0,
    requestsPerMin: 0,
    rateLimitMax: 0,
    lastFetchMs: 0,
    cacheStatus: "HIT",
    windowSize: 0,
    windowFailures: 0,
    windowSlowCalls: 0,
    lastFailureTs: null,
    ...over,
  };
}

describe("latency_color_class", () => {
  it("uses tertiary for undefined or non-finite", () => {
    expect(latency_color_class(undefined)).toContain("text-tertiary");
    expect(latency_color_class(Number.NaN)).toContain("text-tertiary");
  });

  it("uses success for fast p50", () => {
    expect(latency_color_class(0)).toContain("success");
    expect(latency_color_class(199)).toContain("success");
  });

  it("uses warning in the mid band", () => {
    expect(latency_color_class(200)).toContain("warning");
    expect(latency_color_class(799)).toContain("warning");
  });

  it("uses danger for very slow p50", () => {
    expect(latency_color_class(800)).toContain("danger");
  });
});

describe("calculate_tier_health_counts", () => {
  it("counts closed vs total per tier", () => {
    const rows = [p({ name: "A", tier: 1, state: "CLOSED" }), p({ name: "B", tier: 1, state: "OPEN" })];
    expect(calculate_tier_health_counts(rows)).toEqual({
      tier1Closed: 1,
      tier1Total: 2,
      tier2Closed: 0,
      tier2Total: 0,
    });
  });
});

describe("calculate_total_requests_per_min", () => {
  it("sums finite request rates", () => {
    const sum = calculate_total_requests_per_min([p({ requestsPerMin: 10 }), p({ requestsPerMin: 5.5 })]);
    expect(sum).toBeCloseTo(15.5);
  });
});

describe("list_tier1_not_closed", () => {
  it("returns tier-1 names not in CLOSED state", () => {
    const rows = [
      p({ name: "Z", tier: 1, state: "DEGRADED" }),
      p({ name: "A", tier: 1, state: "CLOSED" }),
      p({ name: "M", tier: 2, state: "OPEN" }),
    ];
    expect(list_tier1_not_closed(rows)).toEqual(["Z"]);
  });
});

describe("calculate_health_bar_ratio", () => {
  it("clamps score to 0–1", () => {
    expect(calculate_health_bar_ratio(-1)).toBe(0);
    expect(calculate_health_bar_ratio(2)).toBe(1);
    expect(calculate_health_bar_ratio(0.6)).toBeCloseTo(0.6);
  });
});

describe("calculate_breaker_timeline", () => {
  it("returns null for CLOSED", () => {
    expect(calculate_breaker_timeline("CLOSED", null, Date.now())).toBeNull();
  });

  it("builds OPEN timeline from lastFailureTs", () => {
    const opened = new Date("2026-01-01T00:00:00.000Z").getTime();
    const now = opened + 10_000;
    const model = calculate_breaker_timeline("OPEN", "2026-01-01T00:00:00.000Z", now);
    expect(model).not.toBeNull();
    expect(model?.phase).toBe("open_to_half");
    expect(model?.remainingSec).toBeGreaterThan(18);
    expect(model?.remainingSec).toBeLessThanOrEqual(21);
  });
});
