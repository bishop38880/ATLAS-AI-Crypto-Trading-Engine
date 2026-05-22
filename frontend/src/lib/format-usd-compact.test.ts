import { describe, expect, it } from "vitest";

import { format_usd_compact_display } from "./format-usd-compact";

describe("format_usd_compact_display", () => {
  it("formats billions succinctly", () => {
    expect(format_usd_compact_display("1234000000")).toBe("$1.23B");
  });

  it("handles explicit dollar strings", () => {
    expect(format_usd_compact_display("$42.5")).toBe("$42.5");
  });

  it("formats thousands with K suffix", () => {
    expect(format_usd_compact_display("15000")).toBe("$15K");
  });

  it("handles empty strings as placeholder dash", () => {
    expect(format_usd_compact_display("   ")).toBe("—");
  });
});
