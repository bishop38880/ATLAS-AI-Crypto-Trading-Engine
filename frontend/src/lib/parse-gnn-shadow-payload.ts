/**
 * Normalises `/api/gnn/shadow` and `/ws/gnn` JSON into a stable frontend snapshot.
 */

export interface GnnLeadLagPayload {
  coefficients: unknown;
  seesaw: unknown;
}

export interface GnnShadowSnapshot {
  shadowScores: unknown[];
  walletAnalysis: Record<string, unknown>;
  leadLag: GnnLeadLagPayload;
  inferenceStats: Record<string, unknown>;
}

export function parse_gnn_shadow_payload(raw: unknown): GnnShadowSnapshot | null {
  if (typeof raw !== "object" || raw === null) {
    return null;
  }
  const record = raw as Record<string, unknown>;
  const shadow_scores_raw = record.shadowScores;
  const shadow_scores = Array.isArray(shadow_scores_raw) ? shadow_scores_raw : [];

  const wallet_raw = record.walletAnalysis;
  const wallet_analysis =
    typeof wallet_raw === "object" && wallet_raw !== null && !Array.isArray(wallet_raw)
      ? (wallet_raw as Record<string, unknown>)
      : {};

  const lead_raw = record.leadLag;
  let lead_lag: GnnLeadLagPayload = { coefficients: [], seesaw: null };
  if (typeof lead_raw === "object" && lead_raw !== null && !Array.isArray(lead_raw)) {
    const lr = lead_raw as Record<string, unknown>;
    lead_lag = {
      coefficients: "coefficients" in lr ? lr.coefficients : [],
      seesaw: "seesaw" in lr ? lr.seesaw : null,
    };
  }

  const inf_raw = record.inferenceStats;
  const inference_stats =
    typeof inf_raw === "object" && inf_raw !== null && !Array.isArray(inf_raw)
      ? (inf_raw as Record<string, unknown>)
      : {};

  return {
    shadowScores: shadow_scores,
    walletAnalysis: wallet_analysis,
    leadLag: lead_lag,
    inferenceStats: inference_stats,
  };
}
