import { describe, expect, it } from "vitest";

import {
  format_memory_decimal,
  format_memory_integer,
  format_memory_percent,
  format_memory_result_aria,
} from "./format-memory-dashboard";

describe("format_memory_integer", () => {
  it("groups thousands", () => {
    expect(format_memory_integer(45892)).toBe("45,892");
  });

  it("handles non-finite", () => {
    expect(format_memory_integer(Number.NaN)).toBe("—");
  });
});

describe("format_memory_decimal", () => {
  it("fixes fraction digits", () => {
    expect(format_memory_decimal(0.789, 2)).toBe("0.79");
  });
});

describe("format_memory_percent", () => {
  it("suffixes percent", () => {
    expect(format_memory_percent(6.2, 1)).toBe("6.2%");
  });
});

describe("format_memory_result_aria", () => {
  it("strips check glyph for phrasing", () => {
    expect(format_memory_result_aria("✓ Stored")).toBe("Stored");
  });

  it("unknown empty", () => {
    expect(format_memory_result_aria("   ")).toBe("Unknown result");
  });
});
