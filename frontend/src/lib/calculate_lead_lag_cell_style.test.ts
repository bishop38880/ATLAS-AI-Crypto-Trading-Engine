import { describe, expect, it } from "vitest";

import { calculate_lead_lag_cell_surface_style } from "./calculate_lead_lag_cell_style";

describe("calculate_lead_lag_cell_surface_style", () => {
  it("uses neutral surface for diagonal placeholder", () => {
    const style = calculate_lead_lag_cell_surface_style({
      coefficient: null,
      displayText: "─",
      isStrong: false,
      isDiagonal: true,
    });
    expect(style.backgroundColor).toContain("0.03");
  });

  it("tints positive coefficients cyan", () => {
    const style = calculate_lead_lag_cell_surface_style({
      coefficient: 0.2,
      displayText: "0.20",
      isStrong: true,
      isDiagonal: false,
    });
    expect(String(style.backgroundColor)).toContain("229, 255");
  });
});
