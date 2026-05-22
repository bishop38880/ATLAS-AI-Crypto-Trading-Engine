import { describe, expect, it } from "vitest";

import {
  calculate_confluence_needle_left_percent,
  calculate_confluence_threshold_zone,
} from "./calculate-confluence-threshold-zone";

describe("calculate_confluence_threshold_zone", () => {
  it("returns none below first threshold", () => {
    expect(calculate_confluence_threshold_zone(0).zone).toBe("none");
    expect(calculate_confluence_threshold_zone(106).zone).toBe("none");
    expect(calculate_confluence_threshold_zone(119).zone).toBe("none");
  });

  it("returns tier_2x in 120–149 band", () => {
    expect(calculate_confluence_threshold_zone(120).zone).toBe("tier_2x");
    expect(calculate_confluence_threshold_zone(130).zone).toBe("tier_2x");
    expect(calculate_confluence_threshold_zone(149).zone).toBe("tier_2x");
  });

  it("returns tier_3x in 150–179 band", () => {
    expect(calculate_confluence_threshold_zone(150).zone).toBe("tier_3x");
    expect(calculate_confluence_threshold_zone(160).zone).toBe("tier_3x");
  });

  it("returns tier_5x at and above 180", () => {
    expect(calculate_confluence_threshold_zone(180).zone).toBe("tier_5x");
    expect(calculate_confluence_threshold_zone(185).zone).toBe("tier_5x");
    expect(calculate_confluence_threshold_zone(220).zone).toBe("tier_5x");
  });

  it("clamps to cap", () => {
    expect(calculate_confluence_threshold_zone(500).zone).toBe("tier_5x");
    expect(calculate_confluence_threshold_zone(-10).zone).toBe("none");
  });
});

describe("calculate_confluence_needle_left_percent", () => {
  it("maps value to width percent", () => {
    expect(calculate_confluence_needle_left_percent(0)).toBe(0);
    expect(calculate_confluence_needle_left_percent(110)).toBe(50);
    expect(calculate_confluence_needle_left_percent(220)).toBe(100);
  });

  it("clamps to cap", () => {
    expect(calculate_confluence_needle_left_percent(440)).toBe(100);
    expect(calculate_confluence_needle_left_percent(-5)).toBe(0);
  });
});
