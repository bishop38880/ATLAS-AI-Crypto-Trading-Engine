export interface BacktestInventoryMapped {
  dbPath: string;
  candleCount: number;
  signalCount: number;
  runCount: number;
  assets: string[];
}

export interface BacktestMetricsMapped {
  runId: string;
  totalTrades: number;
  winningTrades: number;
  losingTrades: number;
  winRate: number;
  grossPnlUsd: string;
  totalFeesUsd: string;
  netPnlUsd: string;
  maxDrawdownUsd: string;
  maxDrawdownPct: number;
  sharpeRatio: number | null;
  sortinoRatio: number | null;
  profitFactor: number | null;
  avgHoldSeconds: number | null;
  vetoCount: number;
}

export interface BacktestEquityPointMapped {
  tsIso: string;
  equityUsd: string;
}

export interface BacktestRunSummaryMapped {
  runId: string;
  createdAtIso: string;
  asset: string;
  timeframe: string;
  startTsIso: string;
  endTsIso: string;
  initialCapitalUsd: string;
  netPnlUsd: string | null;
  winRate: number | null;
  totalTrades: number | null;
  vetoCount: number | null;
}

export interface BacktestRunCreatedMapped {
  runId: string;
  metrics: BacktestMetricsMapped;
  equityCurve: BacktestEquityPointMapped[];
  disclaimer: string;
}

export interface BacktestTradeRowMapped {
  tradeId: string;
  signalId: string;
  asset: string;
  direction: string;
  entryTsIso: string;
  exitTsIso: string | null;
  entryPrice: string;
  exitPrice: string | null;
  netPnlUsd: string | null;
  exitReason: string | null;
  riskVeto: boolean;
}

export interface BacktestRunDetailMapped {
  run: BacktestRunSummaryMapped;
  metrics: BacktestMetricsMapped;
  equityCurve: BacktestEquityPointMapped[];
  trades: BacktestTradeRowMapped[];
  disclaimer: string;
}

export interface BacktestSeedMapped {
  candlesImported: number;
  signalsImported: number;
  asset: string;
  timeframe: string;
  message: string;
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

function read_optional_number(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) {
    return value;
  }
  return null;
}

function map_metrics(raw: Record<string, unknown>): BacktestMetricsMapped | null {
  const runId = read_string(raw.runId ?? raw.run_id);
  if (!runId) {
    return null;
  }
  return {
    runId,
    totalTrades: read_number(raw.totalTrades ?? raw.total_trades),
    winningTrades: read_number(raw.winningTrades ?? raw.winning_trades),
    losingTrades: read_number(raw.losingTrades ?? raw.losing_trades),
    winRate: read_number(raw.winRate ?? raw.win_rate),
    grossPnlUsd: read_string(raw.grossPnlUsd ?? raw.gross_pnl_usd),
    totalFeesUsd: read_string(raw.totalFeesUsd ?? raw.total_fees_usd),
    netPnlUsd: read_string(raw.netPnlUsd ?? raw.net_pnl_usd),
    maxDrawdownUsd: read_string(raw.maxDrawdownUsd ?? raw.max_drawdown_usd),
    maxDrawdownPct: read_number(raw.maxDrawdownPct ?? raw.max_drawdown_pct),
    sharpeRatio: read_optional_number(raw.sharpeRatio ?? raw.sharpe_ratio),
    sortinoRatio: read_optional_number(raw.sortinoRatio ?? raw.sortino_ratio),
    profitFactor: read_optional_number(raw.profitFactor ?? raw.profit_factor),
    avgHoldSeconds: read_optional_number(raw.avgHoldSeconds ?? raw.avg_hold_seconds),
    vetoCount: read_number(raw.vetoCount ?? raw.veto_count),
  };
}

function map_equity_point(raw: unknown): BacktestEquityPointMapped | null {
  if (!is_record(raw)) {
    return null;
  }
  const tsIso = read_string(raw.tsIso ?? raw.ts_iso);
  const equityUsd = read_string(raw.equityUsd ?? raw.equity_usd);
  if (!tsIso || !equityUsd) {
    return null;
  }
  return { tsIso, equityUsd };
}

function map_run_summary(raw: Record<string, unknown>): BacktestRunSummaryMapped | null {
  const runId = read_string(raw.runId ?? raw.run_id);
  if (!runId) {
    return null;
  }
  return {
    runId,
    createdAtIso: read_string(raw.createdAtIso ?? raw.created_at_iso),
    asset: read_string(raw.asset),
    timeframe: read_string(raw.timeframe),
    startTsIso: read_string(raw.startTsIso ?? raw.start_ts_iso),
    endTsIso: read_string(raw.endTsIso ?? raw.end_ts_iso),
    initialCapitalUsd: read_string(raw.initialCapitalUsd ?? raw.initial_capital_usd),
    netPnlUsd:
      typeof (raw.netPnlUsd ?? raw.net_pnl_usd) === "string"
        ? read_string(raw.netPnlUsd ?? raw.net_pnl_usd)
        : null,
    winRate: read_optional_number(raw.winRate ?? raw.win_rate),
    totalTrades: read_optional_number(raw.totalTrades ?? raw.total_trades),
    vetoCount: read_optional_number(raw.vetoCount ?? raw.veto_count),
  };
}

export function mapBacktestInventoryPayload(raw: unknown): BacktestInventoryMapped | null {
  if (!is_record(raw)) {
    return null;
  }
  const assetsRaw = raw.assets;
  const assets = Array.isArray(assetsRaw)
    ? assetsRaw.filter((item): item is string => typeof item === "string")
    : [];
  return {
    dbPath: read_string(raw.dbPath ?? raw.db_path),
    candleCount: read_number(raw.candleCount ?? raw.candle_count),
    signalCount: read_number(raw.signalCount ?? raw.signal_count),
    runCount: read_number(raw.runCount ?? raw.run_count),
    assets,
  };
}

export function mapBacktestRunListPayload(raw: unknown): BacktestRunSummaryMapped[] {
  if (!is_record(raw)) {
    return [];
  }
  const runsRaw = raw.runs;
  if (!Array.isArray(runsRaw)) {
    return [];
  }
  const out: BacktestRunSummaryMapped[] = [];
  for (const item of runsRaw) {
    if (!is_record(item)) {
      continue;
    }
    const mapped = map_run_summary(item);
    if (mapped) {
      out.push(mapped);
    }
  }
  return out;
}

export function mapBacktestRunCreatedPayload(raw: unknown): BacktestRunCreatedMapped | null {
  if (!is_record(raw)) {
    return null;
  }
  const runId = read_string(raw.runId ?? raw.run_id);
  const metricsRaw = raw.metrics;
  if (!runId || !is_record(metricsRaw)) {
    return null;
  }
  const metrics = map_metrics(metricsRaw);
  if (!metrics) {
    return null;
  }
  const curveRaw = raw.equityCurve ?? raw.equity_curve;
  const equityCurve: BacktestEquityPointMapped[] = [];
  if (Array.isArray(curveRaw)) {
    for (const point of curveRaw) {
      const mapped = map_equity_point(point);
      if (mapped) {
        equityCurve.push(mapped);
      }
    }
  }
  return {
    runId,
    metrics,
    equityCurve,
    disclaimer: read_string(raw.disclaimer),
  };
}

export function mapBacktestRunDetailPayload(raw: unknown): BacktestRunDetailMapped | null {
  if (!is_record(raw)) {
    return null;
  }
  const runRaw = raw.run;
  const metricsRaw = raw.metrics;
  if (!is_record(runRaw) || !is_record(metricsRaw)) {
    return null;
  }
  const run = map_run_summary(runRaw);
  const metrics = map_metrics(metricsRaw);
  if (!run || !metrics) {
    return null;
  }
  const curveRaw = raw.equityCurve ?? raw.equity_curve;
  const equityCurve: BacktestEquityPointMapped[] = [];
  if (Array.isArray(curveRaw)) {
    for (const point of curveRaw) {
      const mapped = map_equity_point(point);
      if (mapped) {
        equityCurve.push(mapped);
      }
    }
  }
  const tradesRaw = raw.trades;
  const trades: BacktestTradeRowMapped[] = [];
  if (Array.isArray(tradesRaw)) {
    for (const row of tradesRaw) {
      if (!is_record(row)) {
        continue;
      }
      trades.push({
        tradeId: read_string(row.tradeId ?? row.trade_id),
        signalId: read_string(row.signalId ?? row.signal_id),
        asset: read_string(row.asset),
        direction: read_string(row.direction),
        entryTsIso: read_string(row.entryTsIso ?? row.entry_ts_iso),
        exitTsIso:
          typeof (row.exitTsIso ?? row.exit_ts_iso) === "string"
            ? read_string(row.exitTsIso ?? row.exit_ts_iso)
            : null,
        entryPrice: read_string(row.entryPrice ?? row.entry_price),
        exitPrice:
          typeof (row.exitPrice ?? row.exit_price) === "string"
            ? read_string(row.exitPrice ?? row.exit_price)
            : null,
        netPnlUsd:
          typeof (row.netPnlUsd ?? row.net_pnl_usd) === "string"
            ? read_string(row.netPnlUsd ?? row.net_pnl_usd)
            : null,
        exitReason:
          typeof (row.exitReason ?? row.exit_reason) === "string"
            ? read_string(row.exitReason ?? row.exit_reason)
            : null,
        riskVeto: Boolean(row.riskVeto ?? row.risk_veto),
      });
    }
  }
  return {
    run,
    metrics,
    equityCurve,
    trades,
    disclaimer: read_string(raw.disclaimer),
  };
}

export function mapBacktestSeedPayload(raw: unknown): BacktestSeedMapped | null {
  if (!is_record(raw)) {
    return null;
  }
  return {
    candlesImported: read_number(raw.candlesImported ?? raw.candles_imported),
    signalsImported: read_number(raw.signalsImported ?? raw.signals_imported),
    asset: read_string(raw.asset),
    timeframe: read_string(raw.timeframe),
    message: read_string(raw.message),
  };
}
