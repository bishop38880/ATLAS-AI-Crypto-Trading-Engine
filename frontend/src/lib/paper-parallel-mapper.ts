export interface TierWinBlockMapped {
  wins: number;
  trades: number;
  winRate: number;
}

export interface PaperParallelValidationMapped {
  initialUsd: string;
  endingUsd: string;
  equityCurve: { ts: string; equityUsd: string }[];
  drawdownCurve: { ts: string; drawdownPct: number }[];
  weeklySharpeAnnualized: number;
  tierWinRates: Record<string, TierWinBlockMapped>;
  rowCountUsed: number;
  pricingModel: string;
  disclaimer: string;
}

function is_record(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function read_string(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

function read_number(value: unknown, fallback = 0): number {
  if (typeof value === "number" && Number.isFinite(value)) {
    return value;
  }
  return fallback;
}

function read_tier_block(value: unknown): TierWinBlockMapped | null {
  if (!is_record(value)) {
    return null;
  }
  const wins = read_number(value.wins, Number.NaN);
  const trades = read_number(value.trades, Number.NaN);
  const winRate = read_number(value.winRate ?? value.win_rate, Number.NaN);
  if (!Number.isFinite(wins) || !Number.isFinite(trades) || !Number.isFinite(winRate)) {
    return null;
  }
  return { wins: Math.round(wins), trades: Math.round(trades), winRate };
}

/**
 * Normalises `/api/paper-trade/parallel-validation` camelCase payloads.
 */
export function mapPaperParallelValidationPayload(raw: unknown): PaperParallelValidationMapped | null {
  if (!is_record(raw)) {
    return null;
  }

  const tierRaw = raw.tierWinRates ?? raw.tier_win_rates;
  if (!is_record(tierRaw)) {
    return null;
  }

  const tiers: Record<string, TierWinBlockMapped> = {};
  for (const tierKey of Object.keys(tierRaw)) {
    const parsed = read_tier_block(tierRaw[tierKey]);
    if (parsed === null) {
      continue;
    }
    tiers[tierKey] = parsed;
  }

  const eqRaw = raw.equityCurve ?? raw.equity_curve;
  const ddRaw = raw.drawdownCurve ?? raw.drawdown_curve;
  if (!Array.isArray(eqRaw) || !Array.isArray(ddRaw)) {
    return null;
  }

  const equityCurve = eqRaw
    .map((point) =>
      is_record(point)
        ? { ts: read_string(point.ts), equityUsd: read_string(point.equityUsd ?? point.equity_usd) }
        : null,
    )
    .filter((p): p is { ts: string; equityUsd: string } => p !== null && p.ts.length > 0 && p.equityUsd.length > 0);

  const drawdownCurve = ddRaw
    .map((point) =>
      is_record(point)
        ? { ts: read_string(point.ts), drawdownPct: read_number(point.drawdownPct ?? point.drawdown_pct, 0) }
        : null,
    )
    .filter((p): p is { ts: string; drawdownPct: number } => p !== null && p.ts.length > 0);

  return {
    initialUsd: read_string(raw.initialUsd ?? raw.initial_usd),
    endingUsd: read_string(raw.endingUsd ?? raw.ending_usd),
    equityCurve,
    drawdownCurve,
    weeklySharpeAnnualized: read_number(raw.weeklySharpeAnnualized ?? raw.weekly_sharpe_annualized, 0),
    tierWinRates: tiers,
    rowCountUsed: Math.round(read_number(raw.rowCountUsed ?? raw.row_count_used, 0)),
    pricingModel: read_string(raw.pricingModel ?? raw.pricing_model),
    disclaimer: read_string(raw.disclaimer),
  };
}
