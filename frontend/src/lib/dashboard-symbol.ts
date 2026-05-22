/** Normalisation helpers for rotation entries and websocket symbol keys (BASE/USDT-centric). */

const USDT_PAIR_SUFFIX = "/USDT";

export function derive_base_asset_from_pair(pair_or_symbol: string): string {
  const trimmed = pair_or_symbol.trim().toUpperCase();
  if (trimmed.includes("/")) {
    return trimmed.slice(0, trimmed.indexOf("/"));
  }
  if (trimmed.endsWith("USDT")) {
    return trimmed.slice(0, -"USDT".length);
  }
  if (trimmed.endsWith("-PERP")) {
    return trimmed.slice(0, -"-PERP".length);
  }
  return trimmed;
}

export function derive_pair_from_rotation_entry(raw: string): string {
  const trimmed = raw.trim().toUpperCase();
  if (trimmed.includes("/")) {
    const [base, quote] = trimmed.split("/", 2);
    return `${base}/${quote}`;
  }
  if (trimmed.endsWith("-PERP")) {
    const base = trimmed.slice(0, -"-PERP".length);
    return `${base}${USDT_PAIR_SUFFIX}`;
  }
  if (trimmed.endsWith("USDT")) {
    const base = trimmed.slice(0, -"USDT".length);
    return `${base}${USDT_PAIR_SUFFIX}`;
  }
  return `${trimmed}${USDT_PAIR_SUFFIX}`;
}

export function derive_ws_lookup_symbol_keys(pair: string): string[] {
  const base = derive_base_asset_from_pair(pair);
  const normalized_pair = derive_pair_from_rotation_entry(pair);
  return [`${base}/USDT`, `${base}USDT`, `${base}`, normalized_pair, pair.trim()];
}

export function calculate_symbol_keys_deduped(pair: string): string[] {
  const seen = new Set<string>();
  const keys: string[] = [];
  for (const candidate of derive_ws_lookup_symbol_keys(pair)) {
    const token = candidate.toUpperCase();
    if (!seen.has(token)) {
      seen.add(token);
      keys.push(candidate);
    }
  }
  return keys;
}
