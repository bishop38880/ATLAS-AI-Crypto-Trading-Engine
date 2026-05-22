import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { calculate_is_score_cycle_stale, format_minutes_since_label } from "./format-relative-age";

describe("format_minutes_since_label", () => {
  it("renders shorthand minutes", () => {
    expect(format_minutes_since_label(4)).toBe("4m ago");
  });

  it("handles singular minute", () => {
    expect(format_minutes_since_label(1)).toBe("1m ago");
  });

  it("renders fallback when minutes unknown", () => {
    expect(format_minutes_since_label(null, "idle")).toBe("idle");
  });
});

describe("calculate_is_score_cycle_stale", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-05-15T12:00:00.000Z"));
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("returns false when age is within 2× the cycle interval", () => {
    const cycle_seconds = 900;
    const younger_than_2x = new Date(Date.now() - 29 * 60 * 1000).toISOString();
    expect(calculate_is_score_cycle_stale(younger_than_2x, cycle_seconds, 2)).toBe(false);
  });

  it("returns false at exactly 2× interval (strictly older than threshold)", () => {
    const cycle_seconds = 900;
    const exactly_2x = new Date(Date.now() - 30 * 60 * 1000).toISOString();
    expect(calculate_is_score_cycle_stale(exactly_2x, cycle_seconds, 2)).toBe(false);
  });

  it("returns true when age strictly exceeds 2× the cycle interval", () => {
    const cycle_seconds = 900;
    const older = new Date(Date.now() - 31 * 60 * 1000).toISOString();
    expect(calculate_is_score_cycle_stale(older, cycle_seconds, 2)).toBe(true);
  });

  it("returns false for missing timestamp", () => {
    expect(calculate_is_score_cycle_stale(undefined, 900, 2)).toBe(false);
  });
});
