import { describe, expect, it } from "vitest";

import { calculate_wallet_cluster_panel } from "./calculate_wallet_cluster_display";

describe("calculate_wallet_cluster_panel", () => {
  it("renders stress normal when payload missing and shadow calm", () => {
    const vm = calculate_wallet_cluster_panel("BTC", undefined, false);
    expect(vm.stressLine).toContain("NORMAL");
    expect(vm.subclusters).toHaveLength(0);
  });

  it("maps nested wallet payload", () => {
    const vm = calculate_wallet_cluster_panel(
      "BTC",
      {
        clustersDetected: 4,
        largestCluster: { wallets: 23, behavior: "distributing" },
        anomalyWallets: 3,
        smartMoneyScore: 0.71,
        clusters: [{ label: "Cluster 1", wallets: 23, behavior: "distributing" }],
        stressTriggerNormal: true,
      },
      false,
    );
    expect(vm.clustersDetected).toBe(4);
    expect(vm.largestClusterWallets).toBe(23);
    expect(vm.largestGlyph).toBe("▼");
    expect(vm.smartMoneyLabel).toContain("accumulation");
    expect(vm.subclusters).toHaveLength(1);
  });
});
