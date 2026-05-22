import { useMemo, useState } from "react";
import type { ReactElement } from "react";

import { useDashboardAssetPairsQuery } from "../hooks/useDashboardQueries";
import { useGnnShadowChannel } from "../hooks/useGnnShadowChannel";
import { calculate_gnn_inference_display } from "../lib/calculate_gnn_inference_display";
import {
  calculate_lead_lag_matrix_view,
  calculate_parse_lead_lag_coefficients,
} from "../lib/calculate_lead_lag_matrix";
import type { GnnHeatmapColumn } from "../lib/calculate_gnn_shadow_grid";
import { calculate_gnn_shadow_heatmap_columns } from "../lib/calculate_gnn_shadow_grid";
import { calculate_dashboard_pairs } from "../lib/dashboard-universe";
import { derive_base_asset_from_pair } from "../lib/dashboard-symbol";
import { calculate_wallet_cluster_panel } from "../lib/calculate_wallet_cluster_display";
import { parse_gnn_shadow_payload, type GnnShadowSnapshot } from "../lib/parse-gnn-shadow-payload";

import { GNNInferenceStats } from "./gnn/GNNInferenceStats";
import { GNNShadowHeatmap } from "./gnn/GNNShadowHeatmap";
import { GNNStatusBanner } from "./gnn/GNNStatusBanner";
import { GnnDetailDrawer } from "./gnn/GnnDetailDrawer";
import { LASSOLeadLagHeatmap } from "./gnn/LASSOLeadLagHeatmap";
import { WalletClusterSummary } from "./gnn/WalletClusterSummary";

const EMPTY_GNN_SNAPSHOT: GnnShadowSnapshot =
  parse_gnn_shadow_payload({
    shadowScores: [],
    walletAnalysis: {},
    leadLag: { coefficients: [], seesaw: null },
    inferenceStats: {},
  }) ?? {
    shadowScores: [],
    walletAnalysis: {},
    leadLag: { coefficients: [], seesaw: null },
    inferenceStats: {},
  };

function lookup_wallet_payload(wallet_analysis: Record<string, unknown>, base: string): unknown {
  const key = base.trim().toUpperCase();
  if (Object.prototype.hasOwnProperty.call(wallet_analysis, key)) {
    return wallet_analysis[key];
  }
  const nested = wallet_analysis.assets;
  if (typeof nested === "object" && nested !== null && !Array.isArray(nested)) {
    const bucket = nested as Record<string, unknown>;
    if (Object.prototype.hasOwnProperty.call(bucket, key)) {
      return bucket[key];
    }
  }
  return undefined;
}

export function GnnPage(): ReactElement {
  const pairs_query = useDashboardAssetPairsQuery();
  const ladder_pairs = useMemo(
    () => pairs_query.data ?? calculate_dashboard_pairs(null),
    [pairs_query.data],
  );

  const [selected_base, set_selected_base] = useState<string>("BTC");
  const [drawer_base, set_drawer_base] = useState<string | null>(null);

  const channel = useGnnShadowChannel();
  const snapshot = channel.data ?? EMPTY_GNN_SNAPSHOT;

  const universe_bases = useMemo(() => {
    return ladder_pairs.map((pair) => derive_base_asset_from_pair(pair).toUpperCase());
  }, [ladder_pairs]);

  const heatmap_columns = useMemo((): GnnHeatmapColumn[] => {
    return calculate_gnn_shadow_heatmap_columns(ladder_pairs, snapshot.shadowScores);
  }, [ladder_pairs, snapshot.shadowScores]);

  const lead_lag_model = useMemo(() => {
    const pairs = calculate_parse_lead_lag_coefficients(snapshot.leadLag.coefficients);
    return calculate_lead_lag_matrix_view(universe_bases, pairs);
  }, [snapshot.leadLag.coefficients, universe_bases]);

  const wallet_panel = useMemo(() => {
    const column = heatmap_columns.find((c) => c.base === selected_base);
    const stress = column?.detailRow?.stressTriggered;
    const payload = lookup_wallet_payload(snapshot.walletAnalysis, selected_base);
    return calculate_wallet_cluster_panel(selected_base, payload, stress);
  }, [heatmap_columns, selected_base, snapshot.walletAnalysis]);

  const inference_vm = useMemo(() => {
    return calculate_gnn_inference_display(snapshot.inferenceStats);
  }, [snapshot.inferenceStats]);

  const drawer_column = drawer_base === null ? null : heatmap_columns.find((c) => c.base === drawer_base) ?? null;

  return (
    <div className="mx-auto flex w-full max-w-[1600px] flex-col gap-[var(--space-6)] px-[var(--space-4)] py-[var(--space-6)]">
      <GNNStatusBanner />

      <p
        className="rounded-[var(--radius-md)] border border-[var(--warning)] border-opacity-40 bg-[rgba(255,171,64,0.08)] px-[var(--space-4)] py-[var(--space-3)] text-center font-[family-name:var(--font-display)] text-xs font-semibold tracking-wide text-[var(--warning)]"
        role="status"
        aria-live="polite"
      >
        SHADOW MODE — Not affecting live confluence scores.
      </p>

      <div className="grid gap-[var(--space-6)] xl:grid-cols-[minmax(0,2fr)_minmax(320px,1fr)]">
        <div className="flex min-w-0 flex-col gap-[var(--space-6)]">
          <GNNShadowHeatmap
            columns={heatmap_columns}
            selectedBase={selected_base}
            onSelectAsset={set_selected_base}
            onOpenDetail={(base) => set_drawer_base(base)}
          />
          <LASSOLeadLagHeatmap model={lead_lag_model} />
        </div>

        <div className="flex min-w-0 flex-col gap-[var(--space-6)]">
          <WalletClusterSummary assetBase={selected_base} model={wallet_panel} />
          <GNNInferenceStats model={inference_vm} />
        </div>
      </div>

      <GnnDetailDrawer column={drawer_column} onClose={() => set_drawer_base(null)} />

      {channel.status === "error" && channel.error !== null ? (
        <p className="font-data text-xs text-[var(--danger)]" role="status">
          GNN channel degraded — showing last parsed payload. {channel.error}
        </p>
      ) : null}
    </div>
  );
}
