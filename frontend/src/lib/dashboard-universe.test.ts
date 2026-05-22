import { describe, expect, it } from "vitest";

import {
  FALLBACK_ACTIVE_33_BASES_ORDER,
  calculate_dashboard_pairs,
  calculate_is_tier_one_pair,
} from "./dashboard-universe";

describe("calculate_dashboard_pairs", () => {
  it("pads rotation API output to thirty-three slash pairs", () => {
    const thin = calculate_dashboard_pairs(["BTCUSDT", "SOL"]);
    expect(thin).toHaveLength(33);
    expect(thin[0]).toBe("BTC/USDT");
    expect(thin[1]).toBe("SOL/USDT");
  });

  it("uses canonical fallback ladder when Redis is empty", () => {
    const rows = calculate_dashboard_pairs(null);
    expect(rows).toHaveLength(33);
    expect(rows[0]).toMatch(/\/USDT$/);
    expect(FALLBACK_ACTIVE_33_BASES_ORDER).toHaveLength(33);
  });
});

describe("calculate_is_tier_one_pair", () => {
  it("flags ALWAYS_ON equivalents", () => {
    expect(calculate_is_tier_one_pair("BTC/USDT")).toBe(true);
    expect(calculate_is_tier_one_pair("ARB/USDT")).toBe(false);
  });
});
