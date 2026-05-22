import { describe, expect, it } from "vitest";

import { formatDecisionGlyph } from "./format-decision-glyph";

describe("formatDecisionGlyph", () => {
  it("uses up glyph for buys", () => {
    expect(formatDecisionGlyph("Buy")).toBe("▲");
    expect(formatDecisionGlyph("Strong Buy")).toBe("▲");
  });

  it("uses down glyph for sells", () => {
    expect(formatDecisionGlyph("Sell")).toBe("▼");
    expect(formatDecisionGlyph("Strong Sell")).toBe("▼");
  });

  it("uses neutral arrow for hold and no position", () => {
    expect(formatDecisionGlyph("Hold")).toBe("→");
    expect(formatDecisionGlyph("No Position")).toBe("→");
  });
});
