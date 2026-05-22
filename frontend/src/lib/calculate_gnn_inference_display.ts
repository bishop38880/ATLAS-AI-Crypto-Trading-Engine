export interface GnnInferenceStatsViewModel {
  periodLabel: string;
  totalInferences: string;
  avgLatencyLine: string;
  p99LatencyLine: string;
  budgetLine: string;
  avgWalletNodes: string;
  avgTransferEdges: string;
  avgCorrelationEdges: string;
}

function fmt_int(n: number): string {
  return Math.round(n).toLocaleString("en-US");
}

function fmt_ms(n: number): string {
  return `${Math.round(n)}ms`;
}

function coerce_number(value: unknown): number | undefined {
  if (typeof value === "number" && Number.isFinite(value)) {
    return value;
  }
  return undefined;
}

export function calculate_gnn_inference_display(
  inference_stats: Record<string, unknown>,
): GnnInferenceStatsViewModel {
  const period =
    typeof inference_stats.periodLabel === "string"
      ? inference_stats.periodLabel
      : typeof inference_stats.window === "string"
        ? inference_stats.window
        : "Last 1h";

  const total =
    coerce_number(inference_stats.totalInferences1h) ??
    coerce_number(inference_stats.totalInferences) ??
    coerce_number(inference_stats.total_inferences);

  const avg_asset =
    coerce_number(inference_stats.avgLatencyMsPerAsset) ??
    coerce_number(inference_stats.avgLatencyMs) ??
    coerce_number(inference_stats.latencyMs);

  const p99 =
    coerce_number(inference_stats.p99LatencyMs) ??
    coerce_number(inference_stats.p99_latency_ms);

  const budget_pct =
    coerce_number(inference_stats.budgetCompliancePct) ??
    coerce_number(inference_stats.budgetCompliance);

  const wallet_nodes =
    coerce_number(inference_stats.avgWalletNodes) ??
    coerce_number(inference_stats.avg_wallet_nodes) ??
    coerce_number(inference_stats.nodeCount);

  const transfer_edges =
    coerce_number(inference_stats.avgTransferEdges) ??
    coerce_number(inference_stats.avg_transfer_edges) ??
    coerce_number(inference_stats.edgeCount);

  const corr_edges = coerce_number(inference_stats.avgCorrelationEdges);

  return {
    periodLabel: period,
    totalInferences: total !== undefined ? fmt_int(total) : "—",
    avgLatencyLine:
      avg_asset !== undefined ? `${fmt_ms(avg_asset)} / asset` : "—",
    p99LatencyLine: p99 !== undefined ? `${fmt_ms(p99)} / asset` : "—",
    budgetLine:
      budget_pct !== undefined
        ? `${Math.round(budget_pct)}% under 500ms`
        : "—",
    avgWalletNodes: wallet_nodes !== undefined ? fmt_int(wallet_nodes) : "—",
    avgTransferEdges: transfer_edges !== undefined ? fmt_int(transfer_edges) : "—",
    avgCorrelationEdges: corr_edges !== undefined ? fmt_int(corr_edges) : "—",
  };
}
