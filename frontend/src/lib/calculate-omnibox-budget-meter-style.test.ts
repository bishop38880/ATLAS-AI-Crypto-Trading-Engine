import { describe, expect, it } from "vitest";

import {
  calculate_omnibox_budget_meter_tone,
  calculate_omnibox_cost_cap_percent_used,
  calculate_omnibox_minute_limit_hit,
} from "./calculate-omnibox-budget-meter-style";

describe("calculate_omnibox_cost_cap_percent_used", () => {
  it("returns ratio capped at 100", () => {
    expect(calculate_omnibox_cost_cap_percent_used("25", "50")).toBe(50);
    expect(calculate_omnibox_cost_cap_percent_used("120", "50")).toBe(100);
  });

  it("returns 0 on invalid numbers", () => {
    expect(calculate_omnibox_cost_cap_percent_used("x", "50")).toBe(0);
    expect(calculate_omnibox_cost_cap_percent_used("10", "0")).toBe(0);
  });
});

describe("calculate_omnibox_budget_meter_tone", () => {
  it("maps bands correctly", () => {
    expect(calculate_omnibox_budget_meter_tone(10)).toBe("green");
    expect(calculate_omnibox_budget_meter_tone(50)).toBe("amber");
    expect(calculate_omnibox_budget_meter_tone(81)).toBe("red");
  });
});

describe("calculate_omnibox_minute_limit_hit", () => {
  it("detects throttle boundary", () => {
    expect(calculate_omnibox_minute_limit_hit(11, 12)).toBe(false);
    expect(calculate_omnibox_minute_limit_hit(12, 12)).toBe(true);
  });
});
