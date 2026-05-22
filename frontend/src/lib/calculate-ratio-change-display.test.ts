import { describe, expect, it } from "vitest";

import { calculate_ratio_change_display } from "./calculate-ratio-change-display";

describe("calculate_ratio_change_display", () => {
  it("maps signed ratios into glyphs", () => {
    expect(calculate_ratio_change_display("0.032")).toEqual({ glyph: "▲", label: "+3.2%" });
    expect(calculate_ratio_change_display("-0.01")).toEqual({ glyph: "▼", label: "-1.0%" });
  });

  it("handles invalid input gracefully", () => {
    expect(calculate_ratio_change_display("")).toEqual({ glyph: "→", label: "—" });
  });
});
