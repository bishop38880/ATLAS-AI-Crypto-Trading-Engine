import { describe, expect, it } from "vitest";

import {
  calculate_lead_lag_matrix_view,
  calculate_parse_lead_lag_coefficients,
  calculate_seesaw_banner,
} from "./calculate_lead_lag_matrix";

describe("calculate_parse_lead_lag_coefficients", () => {
  it("parses coefficient pair list", () => {
    const pairs = calculate_parse_lead_lag_coefficients([
      { predictor: "BTC", target: "DOGE", coefficient: -0.14 },
      { predictor: "ETH", target: "SOL", value: 0.18 },
    ]);
    expect(pairs).toHaveLength(2);
    expect(pairs[0]).toEqual({ predictor: "BTC", target: "DOGE", coefficient: -0.14 });
    expect(pairs[1]?.coefficient).toBeCloseTo(0.18);
  });

  it("parses labels + matrix form", () => {
    const pairs = calculate_parse_lead_lag_coefficients({
      labels: ["BTC", "ETH"],
      matrix: [
        [1, 0.1],
        [0.2, 1],
      ],
    });
    expect(pairs.some((p) => p.predictor === "BTC" && p.target === "ETH" && p.coefficient === 0.1)).toBe(true);
  });
});

describe("calculate_seesaw_banner", () => {
  it("detects large-cap negative leadership into alts", () => {
    const banner = calculate_seesaw_banner(["BTC", "ETH", "DOGE"], [
      { predictor: "BTC", target: "DOGE", coefficient: -0.14 },
    ]);
    expect(banner).not.toBeNull();
    expect(banner?.headline).toContain("Seesaw");
  });

  it("returns null when no qualifying pairs", () => {
    const banner = calculate_seesaw_banner(["BTC", "DOGE"], [
      { predictor: "BTC", target: "DOGE", coefficient: 0.2 },
    ]);
    expect(banner).toBeNull();
  });
});

describe("calculate_lead_lag_matrix_view", () => {
  it("marks strong coefficients bold flag", () => {
    const vm = calculate_lead_lag_matrix_view(["BTC", "ETH"], [
      { predictor: "BTC", target: "ETH", coefficient: 0.2 },
    ]);
    const cell = vm.rows[0]?.[1];
    expect(cell?.isStrong).toBe(true);
    expect(cell?.coefficient).toBeCloseTo(0.2);
  });
});
