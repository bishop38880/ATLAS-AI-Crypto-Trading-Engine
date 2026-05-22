import { apiUrl } from "./url";

export interface ConfluenceScoreHistoryPoint {
  timestamp: string;
  rawScore: number;
  normalizedScore: number;
  decision: string;
  derivatives: number | null;
  onchain: number | null;
  technical: number | null;
  sentiment: number | null;
  marketContext: number | null;
}

export interface DerivativesFundingPoint {
  fundingTimeMs: number;
  fundingRate: number;
}

export interface DerivativesOiPoint {
  tsMs: number;
  oiUsd: number;
}

export interface RecentDecisionOutcome {
  signalId: string;
  timestamp: string;
  decision: string;
  rawScore: number;
  normalizedScore: number;
  outcomeLabel: string | null;
  pnlPct: number | null;
}

export interface AssetInspectorPayload {
  asset: string;
  scoreHistory: ConfluenceScoreHistoryPoint[];
  fundingHistory: DerivativesFundingPoint[];
  oiHistory: DerivativesOiPoint[];
  derivativesSource: "okx_mcp_cache" | "none";
  recentDecisions: RecentDecisionOutcome[];
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

function read_optional_number(value: unknown): number | null {
  if (value === null || value === undefined) {
    return null;
  }
  const n = read_number(value, NaN);
  return Number.isFinite(n) ? n : null;
}

function is_record(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function map_asset_inspector_payload(raw: unknown): AssetInspectorPayload | null {
  if (!is_record(raw)) {
    return null;
  }
  const asset = typeof raw.asset === "string" ? raw.asset : "";
  if (asset.length === 0) {
    return null;
  }

  const shRaw = raw.scoreHistory ?? raw.score_history;
  const scoreHistory: ConfluenceScoreHistoryPoint[] = [];
  if (Array.isArray(shRaw)) {
    for (const row of shRaw) {
      if (!is_record(row)) {
        continue;
      }
      scoreHistory.push({
        timestamp: typeof row.timestamp === "string" ? row.timestamp : "",
        rawScore: read_number(row.rawScore ?? row.raw_score),
        normalizedScore: read_number(row.normalizedScore ?? row.normalized_score),
        decision: typeof row.decision === "string" ? row.decision : "",
        derivatives: read_optional_number(row.derivatives),
        onchain: read_optional_number(row.onchain),
        technical: read_optional_number(row.technical),
        sentiment: read_optional_number(row.sentiment),
        marketContext: read_optional_number(row.marketContext ?? row.market_context),
      });
    }
  }

  const fhRaw = raw.fundingHistory ?? raw.funding_history;
  const fundingHistory: DerivativesFundingPoint[] = [];
  if (Array.isArray(fhRaw)) {
    for (const row of fhRaw) {
      if (!is_record(row)) {
        continue;
      }
      fundingHistory.push({
        fundingTimeMs: Math.round(read_number(row.fundingTimeMs ?? row.funding_time_ms)),
        fundingRate: read_number(row.fundingRate ?? row.funding_rate),
      });
    }
  }

  const oiRaw = raw.oiHistory ?? raw.oi_history;
  const oiHistory: DerivativesOiPoint[] = [];
  if (Array.isArray(oiRaw)) {
    for (const row of oiRaw) {
      if (!is_record(row)) {
        continue;
      }
      oiHistory.push({
        tsMs: Math.round(read_number(row.tsMs ?? row.ts_ms)),
        oiUsd: read_number(row.oiUsd ?? row.oi_usd),
      });
    }
  }

  const srcRaw = raw.derivativesSource ?? raw.derivatives_source;
  const derivativesSource = srcRaw === "okx_mcp_cache" ? "okx_mcp_cache" : "none";

  const rdRaw = raw.recentDecisions ?? raw.recent_decisions;
  const recentDecisions: RecentDecisionOutcome[] = [];
  if (Array.isArray(rdRaw)) {
    for (const row of rdRaw) {
      if (!is_record(row)) {
        continue;
      }
      const ol = row.outcomeLabel ?? row.outcome_label;
      const pnl = row.pnlPct ?? row.pnl_pct;
      recentDecisions.push({
        signalId: String(row.signalId ?? row.signal_id ?? ""),
        timestamp: typeof row.timestamp === "string" ? row.timestamp : "",
        decision: typeof row.decision === "string" ? row.decision : "",
        rawScore: read_number(row.rawScore ?? row.raw_score),
        normalizedScore: read_number(row.normalizedScore ?? row.normalized_score),
        outcomeLabel: ol === null || ol === undefined ? null : String(ol),
        pnlPct: pnl === null || pnl === undefined ? null : read_number(pnl),
      });
    }
  }

  return {
    asset,
    scoreHistory,
    fundingHistory,
    oiHistory,
    derivativesSource,
    recentDecisions,
  };
}

export async function fetch_asset_inspector_json(
  asset: string,
  days = 30,
): Promise<unknown> {
  const slug = encodeURIComponent(asset);
  const response = await fetch(
    apiUrl(`/api/signals/${slug}/inspector?days=${encodeURIComponent(String(days))}`),
    { headers: { Accept: "application/json" } },
  );
  if (!response.ok) {
    throw new Error(`asset_inspector_failed_${response.status}`);
  }
  return response.json() as Promise<unknown>;
}
