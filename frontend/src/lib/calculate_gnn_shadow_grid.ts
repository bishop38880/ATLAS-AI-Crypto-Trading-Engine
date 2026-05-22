import { derive_base_asset_from_pair } from "./dashboard-symbol";
import { calculate_gnn_obti_glyph_cell } from "./calculate_gnn_obti_cell";

export const GNN_SHADOW_POINTS_MAX = 23;

export interface GnnShadowScoreRowParsed {
  assetBase: string;
  fused01: number;
  scorePoints: number;
  obtiSummary: string | undefined;
  obtiSide: string | undefined;
  walletClusterAnomaly: boolean;
  computedAtIso: string | undefined;
  stressTriggered: boolean;
  raw: Record<string, unknown>;
}

export interface GnnHeatmapColumn {
  base: string;
  scorePoints: number | null;
  scoreIntensity01: number;
  obtiVisual: string;
  obtiAria: string | undefined;
  clusterVisual: string;
  clusterAria: string | undefined;
  detailRow: GnnShadowScoreRowParsed | undefined;
}

function coerce_finite_number(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) {
    return value;
  }
  return null;
}

export function calculate_shadow_gnn_points_from_fused(fused01: number): number {
  const scaled = Math.round(fused01 * GNN_SHADOW_POINTS_MAX);
  return Math.max(0, Math.min(GNN_SHADOW_POINTS_MAX, scaled));
}

/** Normalise Redis/API shadow row into ladder base + display fields. */
export function calculate_parse_shadow_score_row(raw: unknown): GnnShadowScoreRowParsed | null {
  if (typeof raw !== "object" || raw === null) {
    return null;
  }
  const row = raw as Record<string, unknown>;
  const asset_raw = row.asset;
  if (typeof asset_raw !== "string" || asset_raw.trim().length === 0) {
    return null;
  }
  const asset_base = derive_base_asset_from_pair(asset_raw);
  const fused = coerce_finite_number(row.fused_shadow_score);
  if (fused === null) {
    return null;
  }
  const fused_clamped = Math.max(0, Math.min(1, fused));

  const obti_summary =
    typeof row.obti_summary === "string"
      ? row.obti_summary
      : typeof row.obtiSummary === "string"
        ? row.obtiSummary
        : undefined;
  const obti_side =
    typeof row.obti_side === "string"
      ? row.obti_side
      : typeof row.obtiSide === "string"
        ? row.obtiSide
        : undefined;

  const anomaly =
    row.wallet_cluster_anomaly === true ||
    row.walletClusterAnomaly === true ||
    row.cluster_anomaly === true;

  const computed =
    typeof row.computed_at === "string"
      ? row.computed_at
      : typeof row.computedAt === "string"
        ? row.computedAt
        : undefined;

  const stress = row.stress_triggered === true || row.stressTriggered === true;

  return {
    assetBase: asset_base.toUpperCase(),
    fused01: fused_clamped,
    scorePoints: calculate_shadow_gnn_points_from_fused(fused_clamped),
    obtiSummary: obti_summary,
    obtiSide: obti_side,
    walletClusterAnomaly: anomaly,
    computedAtIso: computed,
    stressTriggered: stress,
    raw: row,
  };
}

/**
 * Merge ladder bases (from rotation / fallback 33) with shadow scores.
 * Assets without Redis rows render as skeleton columns (null score).
 */
export function calculate_gnn_shadow_heatmap_columns(
  ladder_pairs: readonly string[],
  shadow_scores: readonly unknown[],
): GnnHeatmapColumn[] {
  const by_base = new Map<string, GnnShadowScoreRowParsed>();
  for (const raw of shadow_scores) {
    const parsed = calculate_parse_shadow_score_row(raw);
    if (parsed !== null) {
      by_base.set(parsed.assetBase, parsed);
    }
  }

  const columns: GnnHeatmapColumn[] = [];
  for (const pair of ladder_pairs) {
    const base = derive_base_asset_from_pair(pair).toUpperCase();
    const detail = by_base.get(base);
    const obti = calculate_gnn_obti_glyph_cell(detail?.obtiSummary, detail?.obtiSide, base);

    const cluster_visual = detail?.walletClusterAnomaly === true ? "●" : "─";
    const cluster_aria =
      detail?.walletClusterAnomaly === true
        ? `Asset ${base}: wallet cluster anomaly detected`
        : undefined;

    if (detail === undefined) {
      columns.push({
        base,
        scorePoints: null,
        scoreIntensity01: 0,
        obtiVisual: obti.visual,
        obtiAria: obti.ariaLabel,
        clusterVisual: cluster_visual,
        clusterAria: cluster_aria,
        detailRow: undefined,
      });
      continue;
    }

    columns.push({
      base,
      scorePoints: detail.scorePoints,
      scoreIntensity01: detail.scorePoints / GNN_SHADOW_POINTS_MAX,
      obtiVisual: obti.visual,
      obtiAria: obti.ariaLabel,
      clusterVisual: cluster_visual,
      clusterAria: cluster_aria,
      detailRow: detail,
    });
  }

  return columns;
}

export function calculate_newest_computed_at_iso(columns: readonly GnnHeatmapColumn[]): string | undefined {
  let best: string | undefined;
  let best_ms = -Infinity;
  for (const col of columns) {
    const iso = col.detailRow?.computedAtIso;
    if (iso === undefined) {
      continue;
    }
    const ms = Date.parse(iso);
    if (!Number.isFinite(ms)) {
      continue;
    }
    if (ms > best_ms) {
      best_ms = ms;
      best = iso;
    }
  }
  return best;
}
