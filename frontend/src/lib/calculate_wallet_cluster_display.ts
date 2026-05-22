export interface WalletClusterSubcluster {
  label: string;
  wallets: number;
  behavior: string;
}

export interface WalletClusterPanelViewModel {
  clustersDetected: number | null;
  largestClusterWallets: number | null;
  largestBehavior: string | null;
  largestGlyph: string;
  anomalyWallets: number | null;
  smartMoneyScore: number | null;
  smartMoneyLabel: string | null;
  subclusters: WalletClusterSubcluster[];
  stressLine: string;
}

function coerce_nonneg_int(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value) && value >= 0) {
    return Math.round(value);
  }
  return null;
}

function coerce_optional_number(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) {
    return value;
  }
  return null;
}

function behaviour_glyph(behavior: string): string {
  const upper = behavior.trim().toUpperCase();
  if (upper.includes("DISTRIBUT")) {
    return "▼";
  }
  if (upper.includes("ACCUMUL")) {
    return "▲";
  }
  if (upper.includes("HOLD")) {
    return "→";
  }
  if (upper.includes("TRAD")) {
    return "◆";
  }
  return "→";
}

function smart_money_label(score: number): string {
  if (score >= 0.65) {
    return "high accumulation";
  }
  if (score >= 0.4) {
    return "neutral positioning";
  }
  return "distribution tilt";
}

/**
 * Maps backend walletAnalysis entry for one ladder base into panel copy.
 */
export function calculate_wallet_cluster_panel(
  base: string,
  wallet_payload: unknown,
  stress_triggered_from_shadow: boolean | undefined,
): WalletClusterPanelViewModel {
  const sym = base.trim().toUpperCase();

  if (typeof wallet_payload !== "object" || wallet_payload === null) {
    const stress_normal = stress_triggered_from_shadow !== true;
    return {
      clustersDetected: null,
      largestClusterWallets: null,
      largestBehavior: null,
      largestGlyph: stress_normal ? "→" : "▼",
      anomalyWallets: null,
      smartMoneyScore: null,
      smartMoneyLabel: null,
      subclusters: [],
      stressLine: stress_normal
        ? "Stress trigger: ● NORMAL (no cross-chain contagion)"
        : `Stress trigger: ◐ ELEVATED (${sym} shadow stress flag)`,
    };
  }

  const row = wallet_payload as Record<string, unknown>;

  const clusters_detected =
    coerce_nonneg_int(row.clustersDetected) ??
    coerce_nonneg_int(row.clusters_detected) ??
    coerce_nonneg_int(row.clusterCount);

  const largest = row.largestCluster ?? row.largest_cluster;
  let largest_wallets: number | null = null;
  let largest_behavior: string | null = null;
  if (typeof largest === "object" && largest !== null) {
    const lob = largest as Record<string, unknown>;
    largest_wallets =
      coerce_nonneg_int(lob.wallets) ?? coerce_nonneg_int(lob.walletCount) ?? coerce_nonneg_int(lob.size);
    const beh =
      typeof lob.behavior === "string"
        ? lob.behavior
        : typeof lob.regime === "string"
          ? lob.regime
          : null;
    largest_behavior = beh;
  }

  const anomaly_wallets =
    coerce_nonneg_int(row.anomalyWallets) ??
    coerce_nonneg_int(row.anomaly_wallets) ??
    coerce_nonneg_int(row.anomalies);

  const smart_raw =
    coerce_optional_number(row.smartMoneyScore) ??
    coerce_optional_number(row.smart_money_score) ??
    coerce_optional_number(row.accumulationScore);

  const smart_label = smart_raw !== null ? smart_money_label(smart_raw) : null;

  const raw_clusters = row.clusters ?? row.subclusters;
  const subclusters: WalletClusterSubcluster[] = [];
  if (Array.isArray(raw_clusters)) {
    let idx = 0;
    for (const c of raw_clusters) {
      idx += 1;
      if (typeof c !== "object" || c === null) {
        continue;
      }
      const cr = c as Record<string, unknown>;
      const wallets =
        coerce_nonneg_int(cr.wallets) ??
        coerce_nonneg_int(cr.walletCount) ??
        coerce_nonneg_int(cr.size) ??
        0;
      const behavior =
        typeof cr.behavior === "string"
          ? cr.behavior
          : typeof cr.regime === "string"
            ? cr.regime
            : "UNKNOWN";
      const label =
        typeof cr.label === "string"
          ? cr.label
          : typeof cr.name === "string"
            ? cr.name
            : `Cluster ${idx}`;
      subclusters.push({ label, wallets, behavior });
    }
  }

  const stress_normal_payload =
    row.stressTriggerNormal === true ||
    row.stress_trigger_normal === true ||
    row.stressNormal === true;

  const stress_normal = stress_triggered_from_shadow !== true && stress_normal_payload !== false;

  const largest_glyph =
    largest_behavior !== null && largest_behavior.length > 0
      ? behaviour_glyph(largest_behavior)
      : "→";

  return {
    clustersDetected: clusters_detected,
    largestClusterWallets: largest_wallets,
    largestBehavior: largest_behavior,
    largestGlyph: largest_glyph,
    anomalyWallets: anomaly_wallets,
    smartMoneyScore: smart_raw,
    smartMoneyLabel: smart_label,
    subclusters,
    stressLine: stress_normal
      ? "Stress trigger: ● NORMAL (no cross-chain contagion)"
      : `Stress trigger: ◐ ELEVATED (${sym} shadow stress flag)`,
  };
}
