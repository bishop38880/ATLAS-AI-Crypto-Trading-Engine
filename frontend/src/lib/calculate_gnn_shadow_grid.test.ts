import { describe, expect, it } from "vitest";

import {
  calculate_gnn_shadow_heatmap_columns,
  calculate_parse_shadow_score_row,
  calculate_shadow_gnn_points_from_fused,
  GNN_SHADOW_POINTS_MAX,
} from "./calculate_gnn_shadow_grid";
import { calculate_dashboard_pairs } from "./dashboard-universe";

describe("calculate_shadow_gnn_points_from_fused", () => {
  it("scales 0–1 fused score to 0–23 points", () => {
    expect(calculate_shadow_gnn_points_from_fused(0)).toBe(0);
    expect(calculate_shadow_gnn_points_from_fused(1)).toBe(GNN_SHADOW_POINTS_MAX);
    expect(calculate_shadow_gnn_points_from_fused(0.5)).toBe(12);
  });
});

describe("calculate_parse_shadow_score_row", () => {
  it("parses BTCUSDT redis row", () => {
    const parsed = calculate_parse_shadow_score_row({
      asset: "BTCUSDT",
      fused_shadow_score: 0.8,
      obti_summary: "moderate",
      obti_side: "bid",
      wallet_cluster_anomaly: true,
      stress_triggered: false,
      computed_at: "2026-01-01T00:00:00+00:00",
    });
    expect(parsed?.assetBase).toBe("BTC");
    expect(parsed?.scorePoints).toBe(18);
    expect(parsed?.walletClusterAnomaly).toBe(true);
  });

  it("returns null when fused score missing", () => {
    expect(
      calculate_parse_shadow_score_row({
        asset: "ETHUSDT",
      }),
    ).toBeNull();
  });
});

describe("calculate_gnn_shadow_heatmap_columns", () => {
  it("aligns ladder with parsed rows and leaves skeletons for gaps", () => {
    const pairs = calculate_dashboard_pairs(null);
    const cols = calculate_gnn_shadow_heatmap_columns(pairs, [
      {
        asset: "BTCUSDT",
        fused_shadow_score: 1,
        obti_summary: "extreme",
        obti_side: "ask",
        wallet_cluster_anomaly: false,
        computed_at: "2026-01-01T00:00:00+00:00",
      },
    ]);
    expect(cols).toHaveLength(33);
    const btc = cols.find((c) => c.base === "BTC");
    expect(btc?.scorePoints).toBe(GNN_SHADOW_POINTS_MAX);
    expect(btc?.obtiVisual).toBe("●");
    const eth = cols.find((c) => c.base === "ETH");
    expect(eth?.scorePoints).toBeNull();
    expect(eth?.detailRow).toBeUndefined();
  });
});
