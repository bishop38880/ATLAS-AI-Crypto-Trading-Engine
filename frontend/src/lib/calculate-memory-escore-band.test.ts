import { describe, expect, it } from "vitest";

import {
  calculate_escore_bar_fill_percent,
  calculate_escore_band_track_class,
} from "./calculate-memory-escore-band";

describe("calculate_escore_bar_fill_percent", () => {
  it("returns 50 when count is half of max", () => {
    expect(calculate_escore_bar_fill_percent(5, 10)).toBe(50);
  });

  it("caps at 100", () => {
    expect(calculate_escore_bar_fill_percent(20, 10)).toBe(100);
  });

  it("returns 0 when max is zero", () => {
    expect(calculate_escore_bar_fill_percent(3, 0)).toBe(0);
  });
});

describe("calculate_escore_band_track_class", () => {
  it("prefers retained color", () => {
    expect(calculate_escore_band_track_class("RETAINED")).toContain("success");
  });

  it("detects borderline", () => {
    expect(calculate_escore_band_track_class("ARCHIVED/BORDERLINE")).toContain("hold");
  });

  it("uses archived when no borderline token", () => {
    expect(calculate_escore_band_track_class("ARCHIVED")).toContain("sell");
  });

  it("does not treat NOT_RETAINED as retained", () => {
    expect(calculate_escore_band_track_class("NOT_RETAINED")).not.toContain("success");
  });
});
