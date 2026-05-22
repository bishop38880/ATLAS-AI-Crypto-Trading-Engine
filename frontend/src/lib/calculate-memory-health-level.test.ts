import { describe, expect, it } from "vitest";

import { calculate_service_health_level } from "./calculate-memory-health-level";

describe("calculate_service_health_level", () => {
  it("maps HEALTHY", () => {
    expect(calculate_service_health_level("HEALTHY")).toBe("healthy");
  });

  it("maps DEGRADED", () => {
    expect(calculate_service_health_level("DEGRADED")).toBe("degraded");
  });

  it("defaults unknown to offline", () => {
    expect(calculate_service_health_level("MYSTERY")).toBe("offline");
  });
});
