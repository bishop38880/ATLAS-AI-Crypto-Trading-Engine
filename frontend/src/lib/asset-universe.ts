import { derive_base_asset_from_pair, derive_pair_from_rotation_entry } from "./dashboard-symbol";
import { calculate_dashboard_pairs, calculate_is_tier_one_pair } from "./dashboard-universe";

export type AssetUniverseCoverage = "daily_8" | "active_33" | "universe" | "fallback";

export interface AssetUniverseRotationPayload {
  full_universe?: unknown;
  active_33?: unknown;
  daily_8?: unknown;
  fetched_at?: unknown;
  is_stale?: unknown;
}

export interface AssetUniverseRow {
  base: string;
  pair: string;
  symbol: string;
  coverage: AssetUniverseCoverage;
  is_tier_one: boolean;
}

export interface AssetUniverseViewModel {
  rows: AssetUniverseRow[];
  active_count: number;
  daily_count: number;
  fetched_at: string | null;
  is_stale: boolean;
  used_fallback: boolean;
}

function calculate_unique_pairs(raw_entries: unknown): string[] {
  if (!Array.isArray(raw_entries)) {
    return [];
  }

  const seen_pairs = new Set<string>();
  const pairs: string[] = [];

  for (const raw_entry of raw_entries) {
    if (typeof raw_entry !== "string") {
      continue;
    }

    const trimmed_entry = raw_entry.trim();
    if (trimmed_entry.length === 0) {
      continue;
    }

    const pair = derive_pair_from_rotation_entry(trimmed_entry);
    const pair_key = pair.toUpperCase();
    if (seen_pairs.has(pair_key)) {
      continue;
    }

    seen_pairs.add(pair_key);
    pairs.push(pair);
  }

  return pairs;
}

function calculate_pair_lookup(pairs: readonly string[]): ReadonlySet<string> {
  return new Set(pairs.map((pair) => pair.toUpperCase()));
}

function calculate_coverage(
  pair: string,
  daily_pairs: ReadonlySet<string>,
  active_pairs: ReadonlySet<string>,
  used_fallback: boolean,
): AssetUniverseCoverage {
  if (daily_pairs.has(pair.toUpperCase())) {
    return "daily_8";
  }

  if (active_pairs.has(pair.toUpperCase())) {
    return "active_33";
  }

  return used_fallback ? "fallback" : "universe";
}

export function calculate_asset_universe_view_model(
  payload: AssetUniverseRotationPayload | null | undefined,
): AssetUniverseViewModel {
  const full_pairs = calculate_unique_pairs(payload?.full_universe);
  const active_pairs = calculate_unique_pairs(payload?.active_33);
  const daily_pairs = calculate_unique_pairs(payload?.daily_8);

  const used_fallback = full_pairs.length === 0 && active_pairs.length === 0;
  const source_pairs =
    full_pairs.length > 0
      ? full_pairs
      : active_pairs.length > 0
        ? active_pairs
        : calculate_dashboard_pairs(null);

  const active_pair_lookup = calculate_pair_lookup(active_pairs);
  const daily_pair_lookup = calculate_pair_lookup(daily_pairs);

  const rows = source_pairs.map((pair) => {
    const canonical_pair = derive_pair_from_rotation_entry(pair);
    const base = derive_base_asset_from_pair(canonical_pair);
    return {
      base,
      pair: canonical_pair,
      symbol: canonical_pair.replace("/", ""),
      coverage: calculate_coverage(canonical_pair, daily_pair_lookup, active_pair_lookup, used_fallback),
      is_tier_one: calculate_is_tier_one_pair(canonical_pair),
    };
  });

  return {
    rows,
    active_count: active_pairs.length,
    daily_count: daily_pairs.length,
    fetched_at: typeof payload?.fetched_at === "string" ? payload.fetched_at : null,
    is_stale: payload?.is_stale === true || used_fallback,
    used_fallback,
  };
}
