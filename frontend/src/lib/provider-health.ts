import type { ProviderHealth } from "../store/index";

/** Milliseconds until an OPEN breaker transitions to HALF_OPEN (backend contract). */
export const BREAKER_OPEN_TO_HALF_MS = 30_000;

/** Milliseconds for HALF_OPEN probe window before closing (best-effort UI). */
export const BREAKER_HALF_TO_CLOSED_MS = 30_000;

export interface TierHealthCounts {
  tier1Closed: number;
  tier1Total: number;
  tier2Closed: number;
  tier2Total: number;
}

export function calculate_tier_health_counts(providers: ProviderHealth[]): TierHealthCounts {
  let tier1Closed = 0;
  let tier1Total = 0;
  let tier2Closed = 0;
  let tier2Total = 0;

  for (const p of providers) {
    if (p.tier === 1) {
      tier1Total += 1;
      if (p.state === "CLOSED") {
        tier1Closed += 1;
      }
    } else {
      tier2Total += 1;
      if (p.state === "CLOSED") {
        tier2Closed += 1;
      }
    }
  }

  return { tier1Closed, tier1Total, tier2Closed, tier2Total };
}

export function calculate_total_requests_per_min(providers: ProviderHealth[]): number {
  let sum = 0;
  for (const p of providers) {
    if (Number.isFinite(p.requestsPerMin)) {
      sum += p.requestsPerMin;
    }
  }
  return sum;
}

export function list_tier1_not_closed(providers: ProviderHealth[]): string[] {
  const names: string[] = [];
  for (const p of providers) {
    if (p.tier === 1 && p.state !== "CLOSED") {
      names.push(p.name);
    }
  }
  return names.sort((a, b) => a.localeCompare(b));
}

/** Maps 0–1 health to bar fill ratio (same anchors as dashboard ScoreBar). */
export function calculate_health_bar_ratio(score: number): number {
  if (!Number.isFinite(score)) {
    return 0;
  }
  return Math.min(1, Math.max(0, score));
}

export interface BreakerTimelineModel {
  show: boolean;
  phase: "open_to_half" | "half_to_closed";
  openedAgoSec: number;
  remainingSec: number;
  progress01: number;
}

export function calculate_breaker_timeline(
  state: ProviderHealth["state"],
  lastFailureTs: string | null,
  nowMs: number,
): BreakerTimelineModel | null {
  if (state !== "OPEN" && state !== "HALF_OPEN") {
    return null;
  }

  const openedMs = lastFailureTs !== null ? Date.parse(lastFailureTs) : Number.NaN;
  const start = Number.isFinite(openedMs) ? openedMs : nowMs;
  const windowMs = state === "OPEN" ? BREAKER_OPEN_TO_HALF_MS : BREAKER_HALF_TO_CLOSED_MS;
  const end = start + windowMs;
  const elapsed = Math.max(0, nowMs - start);
  const remainingMs = Math.max(0, end - nowMs);
  const progress01 = windowMs <= 0 ? 1 : Math.min(1, elapsed / windowMs);

  return {
    show: true,
    phase: state === "OPEN" ? "open_to_half" : "half_to_closed",
    openedAgoSec: elapsed / 1000,
    remainingSec: remainingMs / 1000,
    progress01,
  };
}

/** Tailwind classes for aggregated latency (p50) using design tokens. */
export function latency_color_class(ms: number | undefined): string {
  if (ms === undefined || !Number.isFinite(ms)) {
    return "text-[var(--text-tertiary)]";
  }
  if (ms < 200) {
    return "text-[var(--success)]";
  }
  if (ms < 800) {
    return "text-[var(--warning)]";
  }
  return "text-[var(--danger)]";
}

export function format_breaker_state_label(state: ProviderHealth["state"]): string {
  switch (state) {
    case "CLOSED":
      return "● CLOSED";
    case "DEGRADED":
      return "◐ DEGRADED";
    case "OPEN":
      return "○ OPEN";
    case "HALF_OPEN":
      return "◑ HALF-OPEN";
    default:
      return "● CLOSED";
  }
}
