import { describe, expect, it } from "vitest";

import { parse_gnn_shadow_payload } from "./parse-gnn-shadow-payload";

describe("parse_gnn_shadow_payload", () => {
  it("normalises minimal API bodies", () => {
    const snap = parse_gnn_shadow_payload({});
    expect(snap).not.toBeNull();
    expect(snap?.shadowScores).toEqual([]);
    expect(snap?.leadLag.coefficients).toEqual([]);
  });

  it("preserves shadow rows", () => {
    const snap = parse_gnn_shadow_payload({
      shadowScores: [{ asset: "BTCUSDT", fused_shadow_score: 0.5 }],
      leadLag: { coefficients: [{ predictor: "BTC", target: "ETH", coefficient: 0.1 }] },
    });
    expect(snap?.shadowScores).toHaveLength(1);
    expect(Array.isArray(snap?.leadLag.coefficients)).toBe(true);
  });
});
