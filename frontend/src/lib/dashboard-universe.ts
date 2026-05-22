import { derive_pair_from_rotation_entry } from "./dashboard-symbol";

/**
 * Canonical 33-asset PROMETHEUS ladder bases when Redis ``polaris:rotation:active_33`` is cold.
 * Mirrors ``backend/config/asset_universe.py`` POLARIS_DASHBOARD_FALLBACK_BASES_ORDER byte-for-byte.
 */
export const FALLBACK_ACTIVE_33_BASES_ORDER: readonly string[] = [
  "BTC",
  "ETH",
  "BNB",
  "XRP",
  "SOL",
  "TRX",
  "DOGE",
  "HYPE",
  "ADA",
  "ZEC",
  "BCH",
  "LINK",
  "XMR",
  "CC",
  "TON",
  "XLM",
  "LTC",
  "SUI",
  "AVAX",
  "HBAR",
  "TAO",
  "XAUT",
  "UNI",
  "DOT",
  "PAXG",
  "WLFI",
  "NEAR",
  "ONDO",
  "ASTER",
  "SKY",
  "ICP",
  "ETC",
  "AAVE",
];

/** Mirrors ATLAS ALWAYS_ON perpetual list (tier-one filter). */
export const TIER_ONE_BASE_ASSETS: ReadonlySet<string> = new Set([
  "BTC",
  "ETH",
  "BNB",
  "XRP",
  "SOL",
  "TRX",
  "DOGE",
  "HYPE",
  "ADA",
  "ZEC",
  "BCH",
  "LINK",
  "XMR",
  "CC",
  "TON",
  "XLM",
  "LTC",
  "SUI",
]);

const TARGET_CARD_COUNT = 33;

/** Maximum unique pairs rendered on the dashboard and mirrored to ``POST /api/dashboard/pins``. */
export const MAX_DASHBOARD_MONITORED_ASSETS = 8;

/**
 * Merge rotation ladder pairs with operator pins, dedupe, and cap at ``MAX_DASHBOARD_MONITORED_ASSETS``.
 * Pins are honoured first; remaining slots fill from ``base_pairs`` in order.
 */
export function merge_dashboard_monitored_pairs(
  base_pairs: readonly string[],
  pinned_pairs: readonly string[],
): string[] {
  const seen_pairs = new Set<string>();
  const merged_pairs: string[] = [];

  const append_unique = (raw_pair: string): void => {
    if (merged_pairs.length >= MAX_DASHBOARD_MONITORED_ASSETS) {
      return;
    }
    const canonical_pair = derive_pair_from_rotation_entry(raw_pair);
    const dedupe_key = canonical_pair.toUpperCase();
    if (seen_pairs.has(dedupe_key)) {
      return;
    }
    seen_pairs.add(dedupe_key);
    merged_pairs.push(canonical_pair);
  };

  for (const raw_pin of pinned_pairs) {
    append_unique(raw_pin);
  }
  for (const raw_base of base_pairs) {
    if (merged_pairs.length >= MAX_DASHBOARD_MONITORED_ASSETS) {
      break;
    }
    append_unique(raw_base);
  }

  return merged_pairs;
}

/** Build exactly 33 `BASE/USDT` rows for the dashboard grid. */
export function calculate_dashboard_pairs(active_33_from_api: readonly string[] | undefined | null): string[] {
  const out: string[] = [];
  const seen = new Set<string>();

  for (const entry of active_33_from_api ?? []) {
    const normalized = derive_pair_from_rotation_entry(entry);
    const key = normalized.toUpperCase();
    if (!seen.has(key)) {
      seen.add(key);
      out.push(normalized);
    }
    if (out.length >= TARGET_CARD_COUNT) {
      return out.slice(0, TARGET_CARD_COUNT);
    }
  }

  for (const base of FALLBACK_ACTIVE_33_BASES_ORDER) {
    const pair = `${base}/USDT`;
    const key = pair.toUpperCase();
    if (!seen.has(key)) {
      seen.add(key);
      out.push(pair);
    }
    if (out.length >= TARGET_CARD_COUNT) {
      break;
    }
  }

  return out.slice(0, TARGET_CARD_COUNT);
}

export function calculate_is_tier_one_pair(pair: string): boolean {
  const base = pair.trim().split("/")[0]?.toUpperCase() ?? "";
  return TIER_ONE_BASE_ASSETS.has(base);
}
