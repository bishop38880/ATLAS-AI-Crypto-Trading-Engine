import { describe, expect, it } from "vitest";

import { parse_provider_test_result } from "./provider-test-result";

describe("parse_provider_test_result", () => {
  it("parses valid backend payloads", () => {
    expect(
      parse_provider_test_result({
        provider: "Coinalyze",
        status: "healthy",
        latency_ms: 42,
        error: null,
        circuit_state: "CLOSED",
        tested_at: "2026-01-02T03:04:05+00:00",
      }),
    ).toMatchObject({
      provider: "Coinalyze",
      latency_ms: 42,
    });
  });

  it("floors fractional latency_ms to integer", () => {
    expect(
      parse_provider_test_result({
        provider: "Pyth",
        status: "degraded",
        latency_ms: 12.9,
        error: null,
        circuit_state: "DEGRADED",
        tested_at: "x",
      })?.latency_ms,
    ).toBe(12);
  });

  it("returns null when required fields drift", () => {
    expect(
      parse_provider_test_result({
        provider: "Coinalyze",
        status: "unknown",
        latency_ms: 1,
        error: null,
        circuit_state: "CLOSED",
        tested_at: "x",
      }),
    ).toBeNull();
  });
});
