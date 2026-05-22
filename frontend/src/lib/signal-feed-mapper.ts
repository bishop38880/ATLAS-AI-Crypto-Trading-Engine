export interface SignalFeedRow {
  asset: string;
  timestamp: string;
  decision: string;
  totalScore: number;
  normalizedScore: number;
  confidence: number;
  passesGate: boolean;
  gateThreshold: number;
  obtiSummary: string | null;
  obtiSide: string | null;
  llmTierLabel: string | null;
  cycleNumber: number | null;
  price: string;
  change24h: string;
}

function is_record(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function read_number(value: unknown, fallback = 0): number {
  if (typeof value === "number" && Number.isFinite(value)) {
    return value;
  }
  if (typeof value === "string" && value.trim().length > 0) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : fallback;
  }
  return fallback;
}

function read_string(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

function read_optional_string(value: unknown): string | null {
  if (value === null || value === undefined) {
    return null;
  }
  const s = typeof value === "string" ? value.trim() : String(value).trim();
  return s.length > 0 ? s : null;
}

/**
 * Normalises cached/query `data` into a concrete array for table renderers.
 * Guards against unexpected envelopes or corrupted query cache (must never throw on `.length`).
 */
export function coerce_signal_feed_rows(value: unknown): SignalFeedRow[] {
  return Array.isArray(value) ? (value as SignalFeedRow[]) : [];
}

/**
 * Maps `/api/signals/feed` JSON (camelCase) into normalised feed rows.
 */
export function map_signal_feed_payload(raw: unknown): SignalFeedRow[] {
  if (!Array.isArray(raw)) {
    return [];
  }

  const rows: SignalFeedRow[] = [];

  for (const item of raw) {
    if (!is_record(item)) {
      continue;
    }

    const asset = read_string(item.asset ?? item.symbol);
    if (asset.length === 0) {
      continue;
    }

    const gateRaw = read_number(item.gateThreshold ?? item.gate_threshold, Number.NaN);
    const gateThreshold = Number.isFinite(gateRaw) ? Math.round(gateRaw) : 0;
    const cycleRaw = item.cycleNumber ?? item.cycle_number;
    let cycleNumber: number | null = null;
    if (cycleRaw !== null && cycleRaw !== undefined) {
      cycleNumber = Math.round(read_number(cycleRaw, Number.NaN));
      if (!Number.isFinite(cycleNumber)) {
        cycleNumber = null;
      }
    }

    rows.push({
      asset,
      timestamp: read_string(item.timestamp ?? item.cycleTs ?? item.cycle_ts),
      decision: read_string(item.decision, "Hold"),
      totalScore: Math.round(read_number(item.totalScore ?? item.total_score)),
      normalizedScore: Math.round(read_number(item.normalizedScore ?? item.normalized_score)),
      confidence: read_number(item.confidence ?? item.signalConfidence ?? item.signal_confidence),
      passesGate:
        typeof item.passesGate === "boolean"
          ? item.passesGate
          : typeof item.passes_gate === "boolean"
            ? item.passes_gate
            : false,
      gateThreshold,
      obtiSummary: read_optional_string(item.obtiSummary ?? item.obti_summary),
      obtiSide: read_optional_string(item.obtiSide ?? item.obti_side),
      llmTierLabel: read_optional_string(item.llmTierLabel ?? item.llm_tier_label),
      cycleNumber,
      price: read_string(item.price, "0"),
      change24h: read_string(item.change24h ?? item.change_24h, "0"),
    });
  }

  return rows;
}
