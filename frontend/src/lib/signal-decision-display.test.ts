import { describe, expect, it } from "vitest";

import {
  calculate_decision_accessibility_label,
  calculate_signal_badge_variant,
  calculate_signal_filter_match,
  format_decision_strength_arrows,
} from "./signal-decision-display";

describe("format_decision_strength_arrows", () => {
  it("pairs strong tiers with stacked glyphs", () => {
    expect(format_decision_strength_arrows("Strong Buy")).toBe("↑↑");
    expect(format_decision_strength_arrows("Strong Sell")).toBe("↓↓");
  });

  it("uses directional arrows for single-step signals", () => {
    expect(format_decision_strength_arrows("Buy")).toBe("↑");
    expect(format_decision_strength_arrows("Sell")).toBe("▼");
  });
});

describe("calculate_signal_badge_variant", () => {
  it("covers every enum member", () => {
    expect(calculate_signal_badge_variant("Buy")).toBe("buy");
    expect(calculate_signal_badge_variant("No Position")).toBe("no-position");
  });
});

describe("calculate_signal_filter_match", () => {
  it("targets actionable flows only", () => {
    expect(calculate_signal_filter_match("Strong Buy")).toBe(true);
    expect(calculate_signal_filter_match("Hold")).toBe(false);
  });
});

describe("calculate_decision_accessibility_label", () => {
  it("returns conversational strings", () => {
    expect(calculate_decision_accessibility_label("Buy").toLowerCase()).toContain("buy");
  });
});
