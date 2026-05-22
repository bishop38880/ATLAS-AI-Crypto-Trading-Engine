import { useQuery } from "@tanstack/react-query";

import { calculate_dashboard_pairs } from "../lib/dashboard-universe";
import { calculate_dashboard_position_slots_from_payload } from "../lib/dashboard-positions";
import { apiUrl } from "../lib/url";

import type { DashboardPositionSlot } from "../lib/dashboard-positions";

interface RotationStatePayload {
  active_33?: string[];
}

async function dashboard_rotation_pairs_fetch(): Promise<string[]> {
  try {
    const response = await fetch(apiUrl("/api/rotation/state"));
    if (!response.ok) {
      return calculate_dashboard_pairs(null);
    }
    const parsed = (await response.json().catch(() => ({}))) as RotationStatePayload;
    return calculate_dashboard_pairs(parsed.active_33 ?? []);
  } catch {
    return calculate_dashboard_pairs(null);
  }
}

async function dashboard_positions_slots_fetch(): Promise<DashboardPositionSlot[]> {
  const response = await fetch(apiUrl("/api/positions"));
  if (!response.ok) {
    return calculate_dashboard_position_slots_from_payload([]);
  }
  const body: unknown = await response.json().catch(() => []);
  return calculate_dashboard_position_slots_from_payload(Array.isArray(body) ? body : []);
}

/** Redis-backed ladder (33-card universe) polled every 10s with cached dedupe. */
export function useDashboardAssetPairsQuery() {
  return useQuery({
    queryKey: ["dashboard", "rotation_pairs_v1"],
    queryFn: dashboard_rotation_pairs_fetch,
    staleTime: 10_000,
    refetchInterval: 120_000,
    placeholderData: calculate_dashboard_pairs(null),
  });
}

export function useDashboardPositionSlotsQuery() {
  return useQuery({
    queryKey: ["dashboard", "positions_slots_v1"],
    queryFn: dashboard_positions_slots_fetch,
    staleTime: 10_000,
  });
}
