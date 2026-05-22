import { describe, expect, it } from "vitest";

import { calculate_sparkline_polyline_points } from "./calculate_sparkline_polyline";

describe("calculate_sparkline_polyline_points", () => {
  it("returns empty polyline when no samples exist", () => {
    expect(calculate_sparkline_polyline_points([]).pointsAttr).toBe("");
  });

  it("emits coordinates for monotonic ladder totals", () => {
    const { pointsAttr } = calculate_sparkline_polyline_points([100, 120, 115]);
    expect(pointsAttr.length).toBeGreaterThan(8);
    expect(pointsAttr).toContain(",");
  });
});
