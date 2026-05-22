/** Visual bands for operator self-regulation (cost + minute throttle). */

export type OmniBoxBudgetMeterTone = "green" | "amber" | "red";

export function calculate_omnibox_cost_cap_percent_used(
  estimatedCostUsd: string,
  dailyCostCapUsd: string,
): number {
  const estimated = Number.parseFloat(estimatedCostUsd);
  const cap = Number.parseFloat(dailyCostCapUsd);
  if (!Number.isFinite(estimated) || !Number.isFinite(cap) || cap <= 0) {
    return 0;
  }
  return Math.min(100, (estimated / cap) * 100);
}

export function calculate_omnibox_budget_meter_tone(
  percentUsed: number,
): OmniBoxBudgetMeterTone {
  if (percentUsed > 80) {
    return "red";
  }
  if (percentUsed >= 50) {
    return "amber";
  }
  return "green";
}

export function calculate_omnibox_minute_limit_hit(
  queriesThisMinute: number,
  queriesPerMinuteLimit: number,
): boolean {
  if (queriesPerMinuteLimit <= 0) {
    return false;
  }
  return queriesThisMinute >= queriesPerMinuteLimit;
}
