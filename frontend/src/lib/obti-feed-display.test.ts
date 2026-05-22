import { describe, expect, it } from "vitest";

import { calculate_obti_feed_cell } from "./obti-feed-display";

describe("calculate_obti_feed_cell", () => {
  it("renders dash for balanced, low, or missing summary", () => {
    expect(calculate_obti_feed_cell(null, null)).toEqual({ visual: "─", ariaLabel: undefined });
    expect(calculate_obti_feed_cell("balanced", "bid")).toEqual({ visual: "─", ariaLabel: undefined });
    expect(calculate_obti_feed_cell("low", null)).toEqual({ visual: "─", ariaLabel: undefined });
  });

  it("renders moderate glyph with side-aware aria label", () => {
    expect(calculate_obti_feed_cell("moderate", "ask")).toEqual({
      visual: "◐ mod",
      ariaLabel: "Moderate order-book toxicity, ask-side",
    });
  });

  it("renders extreme glyph with side-aware aria label", () => {
    expect(calculate_obti_feed_cell("extreme", "bid")).toEqual({
      visual: "● ext",
      ariaLabel: "Extreme order-book toxicity, bid-side",
    });
  });

  it("falls back to unknown side wording when side absent", () => {
    expect(calculate_obti_feed_cell("moderate", null).ariaLabel).toBe(
      "Moderate order-book toxicity, unknown side",
    );
  });
});
