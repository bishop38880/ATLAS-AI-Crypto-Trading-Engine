import { describe, expect, it } from "vitest";

import {
  alert_level_status,
  alert_level_variant,
  calculate_ratio_percent,
  latency_history_values,
  parse_monitoring_number,
  startup_status_level,
  step_status_level,
  test_floor_history_values,
} from "./monitoring-dashboard";

describe("parse_monitoring_number", () => {
  it("parses finite numbers and numeric strings", () => {
    expect(parse_monitoring_number(42)).toBe(42);
    expect(parse_monitoring_number("1,234.5")).toBe(1234.5);
  });

  it("returns zero for missing and non-finite values", () => {
    expect(parse_monitoring_number(undefined)).toBe(0);
    expect(parse_monitoring_number(Number.NaN)).toBe(0);
    expect(parse_monitoring_number("not-a-number")).toBe(0);
  });
});

describe("calculate_ratio_percent", () => {
  it("clamps percent to the 0-100 range", () => {
    expect(calculate_ratio_percent("2", "4")).toBe(50);
    expect(calculate_ratio_percent(10, 4)).toBe(100);
    expect(calculate_ratio_percent(-1, 4)).toBe(0);
  });
});

describe("monitoring status mapping", () => {
  it("maps startup and step statuses to status dots", () => {
    expect(startup_status_level("ALL_PASSED")).toBe("healthy");
    expect(startup_status_level("HAS_FAILURES")).toBe("error");
    expect(startup_status_level("HAS_DEGRADED")).toBe("degraded");
    expect(step_status_level("PASSED")).toBe("healthy");
    expect(step_status_level("FAILED")).toBe("error");
    expect(step_status_level("SKIPPED")).toBe("offline");
  });
});

describe("alert level mapping", () => {
  it("maps alert levels to badge and dot variants", () => {
    expect(alert_level_variant({ level: "ERROR" })).toBe("sell");
    expect(alert_level_status({ level: "ERROR" })).toBe("error");
    expect(alert_level_variant({ level: "WARN" })).toBe("degraded");
    expect(alert_level_status({ level: "INFO" })).toBe("healthy");
  });
});

describe("history value extraction", () => {
  it("extracts latency values from supported fields in chronological order", () => {
    const values = latency_history_values([
      { latencyMs: 30 },
      { durationMs: 20 },
      { totalMs: Number.NaN },
      { value: 10 },
    ]);
    expect(values).toEqual([10, 20, 30]);
  });

  it("extracts test floor values from supported fields in chronological order", () => {
    const values = test_floor_history_values([
      { currentFloor: 12 },
      { floor: 11 },
      { passed: 10 },
    ]);
    expect(values).toEqual([10, 11, 12]);
  });
});
