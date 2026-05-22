import { describe, expect, it } from "vitest";

import { calculate_gnn_inference_display } from "./calculate_gnn_inference_display";

describe("calculate_gnn_inference_display", () => {
  it("formats populated telemetry fields", () => {
    const vm = calculate_gnn_inference_display({
      periodLabel: "Last 1h",
      totalInferences1h: 120,
      avgLatencyMsPerAsset: 187,
      p99LatencyMs: 412,
      budgetCompliancePct: 100,
      avgWalletNodes: 847,
      avgTransferEdges: 12441,
      avgCorrelationEdges: 289,
    });
    expect(vm.totalInferences).toBe("120");
    expect(vm.avgLatencyLine).toContain("187ms");
    expect(vm.p99LatencyLine).toContain("412ms");
    expect(vm.budgetLine).toContain("100%");
  });

  it("emits dashes when fields absent", () => {
    const vm = calculate_gnn_inference_display({});
    expect(vm.totalInferences).toBe("—");
    expect(vm.avgCorrelationEdges).toBe("—");
  });
});
