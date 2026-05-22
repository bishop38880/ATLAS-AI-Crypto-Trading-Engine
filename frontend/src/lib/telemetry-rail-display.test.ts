import { describe, expect, it } from "vitest";

import type { TelemetryRailEntry } from "../stores/telemetryRailStore";
import {
  group_telemetry_entries_by_time,
  is_regime_context_entry,
  matches_telemetry_filter,
} from "./telemetry-rail-display";

function make_entry(overrides: Partial<TelemetryRailEntry> & Pick<TelemetryRailEntry, "createdAtMs">): TelemetryRailEntry {
  return {
    id: "test-id",
    isoTime: new Date(overrides.createdAtMs).toISOString(),
    level: "info",
    source: "system",
    message: "Platform OK → DEGRADED",
    ...overrides,
  };
}

describe("matches_telemetry_filter", () => {
  it("includes regime and system transitions for regime filter", () => {
    const regime_row = make_entry({ createdAtMs: 1, source: "regime", message: "Regime BULL → BEAR" });
    const system_row = make_entry({ createdAtMs: 2, source: "system", message: "Platform OK → HALTED" });
    const scores_row = make_entry({
      createdAtMs: 3,
      source: "scores",
      message: "Confluence snapshots updated",
    });

    expect(matches_telemetry_filter(regime_row, "regime")).toBe(true);
    expect(matches_telemetry_filter(system_row, "regime")).toBe(true);
    expect(matches_telemetry_filter(scores_row, "regime")).toBe(false);
  });
});

describe("is_regime_context_entry", () => {
  it("ignores non-transition system rows", () => {
    const row = make_entry({ createdAtMs: 1, source: "system", message: "Platform heartbeat" });
    expect(is_regime_context_entry(row)).toBe(false);
  });
});

describe("group_telemetry_entries_by_time", () => {
  it("places rows into Last 60s and Earlier buckets", () => {
    const now = 1_000_000;
    const recent = make_entry({ createdAtMs: now - 5_000, id: "recent" });
    const older = make_entry({ createdAtMs: now - 120_000, id: "older" });

    const buckets = group_telemetry_entries_by_time([older, recent], now);

    expect(buckets).toHaveLength(2);
    expect(buckets[0]?.label).toBe("Earlier");
    expect(buckets[0]?.entries.map((row) => row.id)).toEqual(["older"]);
    expect(buckets[1]?.label).toBe("Last 60s");
    expect(buckets[1]?.entries.map((row) => row.id)).toEqual(["recent"]);
  });
});
