import type { SystemHealth } from "../store/index";

export type HmmRegimeNative = NonNullable<SystemHealth["hmmRegimeNative"]>;

export function normalize_hmm_regime_token(raw: string): HmmRegimeNative | null {
  const token = raw.trim().toLowerCase();
  if (token === "bull" || token === "bear" || token === "volatile") {
    return token;
  }
  return null;
}

/** True when HMM reads volatile but the dashboard bucket shows RANGING. */
export function detects_dashboard_regime_mask(health: SystemHealth | null): boolean {
  if (health === null) {
    return false;
  }
  return health.hmmRegimeNative === "volatile" && health.currentRegime === "RANGING";
}

export function format_regime_badge_label(health: SystemHealth | null): string {
  const regime_confidence_display = Math.round(health?.regimeConfidence ?? 0);
  const base_regime_label =
    health?.currentRegime === "BULL"
      ? `BULL ▲ ${regime_confidence_display}%`
      : health?.currentRegime === "BEAR"
        ? `BEAR ▼ ${regime_confidence_display}%`
        : `RANGING → ${regime_confidence_display}%`;

  const asset = health?.regimeContextAsset?.trim();
  const tf = health?.regimeContextTimeframe?.trim();
  if (asset && tf) {
    return `${asset}: ${base_regime_label} (${tf})`;
  }
  if (asset) {
    return `${asset}: ${base_regime_label}`;
  }
  return base_regime_label;
}

export function build_regime_badge_tooltip(health: SystemHealth | null): string | undefined {
  if (health === null) {
    return undefined;
  }
  const lines: string[] = [];
  if (health.regimeContextAsOf) {
    const parsed = new Date(health.regimeContextAsOf);
    const readable = Number.isNaN(parsed.getTime())
      ? health.regimeContextAsOf
      : parsed.toLocaleString();
    lines.push(`As of: ${readable}`);
  }
  if (typeof health.regimeContextDurationBars === "number") {
    const tf = health.regimeContextTimeframe ?? "this timeframe";
    lines.push(`In current state: ${health.regimeContextDurationBars} bars at ${tf}`);
  }
  if (health.regimeContextRunnerUp) {
    lines.push(health.regimeContextRunnerUp);
  }
  if (health.regimeContextTransitionHint) {
    lines.push(health.regimeContextTransitionHint);
  }
  if (health.regimeContextExplanation) {
    lines.push(health.regimeContextExplanation);
  }
  if (detects_dashboard_regime_mask(health)) {
    lines.push("HMM reads VOLATILE while dashboard bucket shows RANGING — open Regime Center for the native label.");
  }
  return lines.length > 0 ? lines.join("\n") : undefined;
}

export function summarize_regime_badge_variant(regime: string): "bull" | "bear" | "ranging" {
  switch (regime) {
    case "BULL":
      return "bull";
    case "BEAR":
      return "bear";
    default:
      return "ranging";
  }
}
