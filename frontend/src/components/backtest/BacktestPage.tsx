import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState, type ReactElement } from "react";

import { SvgSparkline, type SvgSparklinePoint } from "../charts/SvgSparkline";
import { EmptyState } from "../ui/EmptyState";
import { Skeleton } from "../ui/Skeleton";
import { format_usd_compact_display } from "../../lib/format-usd-compact";
import {
  mapBacktestInventoryPayload,
  mapBacktestRunCreatedPayload,
  mapBacktestRunDetailPayload,
  mapBacktestRunListPayload,
  mapBacktestSeedPayload,
  type BacktestRunCreatedMapped,
  type BacktestRunDetailMapped,
  type BacktestRunSummaryMapped,
} from "../../lib/backtest-mapper";
import { apiUrl } from "../../lib/url";

const DEFAULT_ASSET = "BTCUSDT";
const DEFAULT_TIMEFRAME = "1h";
const DEFAULT_START = "2024-01-01";
const DEFAULT_END = "2024-01-15";

async function fetch_inventory(): Promise<unknown> {
  const response = await fetch(apiUrl("/api/backtest/inventory"), {
    headers: { Accept: "application/json" },
  });
  if (!response.ok) {
    throw new Error(`backtest_inventory_failed_${response.status}`);
  }
  return response.json() as Promise<unknown>;
}

async function fetch_runs(): Promise<unknown> {
  const response = await fetch(apiUrl("/api/backtest/runs?limit=30"), {
    headers: { Accept: "application/json" },
  });
  if (!response.ok) {
    throw new Error(`backtest_runs_failed_${response.status}`);
  }
  return response.json() as Promise<unknown>;
}

async function fetch_run_detail(run_id: string): Promise<unknown> {
  const response = await fetch(apiUrl(`/api/backtest/runs/${encodeURIComponent(run_id)}`), {
    headers: { Accept: "application/json" },
  });
  if (!response.ok) {
    throw new Error(`backtest_detail_failed_${response.status}`);
  }
  return response.json() as Promise<unknown>;
}

function equity_to_spark(curve: { equityUsd: string }[]): SvgSparklinePoint[] {
  return curve
    .map((row, idx) => {
      const numeric = Number(row.equityUsd.replace(/,/g, ""));
      return { x: idx, y: numeric };
    })
    .filter((p) => Number.isFinite(p.y));
}

function format_pct(value: number): string {
  return `${(value * 100).toFixed(1)}%`;
}

/**
 * DuckDB offline replay — candles + recorded signals through the PROMETHEUS execution simulator.
 */
export function BacktestPage(): ReactElement {
  const query_client = useQueryClient();

  const [asset, setAsset] = useState(DEFAULT_ASSET);
  const [timeframe, setTimeframe] = useState(DEFAULT_TIMEFRAME);
  const [startDay, setStartDay] = useState(DEFAULT_START);
  const [endDay, setEndDay] = useState(DEFAULT_END);
  const [capital, setCapital] = useState("10000");
  const [scoreThreshold, setScoreThreshold] = useState("65");
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const [lastRun, setLastRun] = useState<BacktestRunCreatedMapped | null>(null);

  const inventory_query = useQuery({
    queryKey: ["backtest", "inventory"],
    queryFn: fetch_inventory,
    staleTime: 10_000,
    select: mapBacktestInventoryPayload,
  });

  const runs_query = useQuery({
    queryKey: ["backtest", "runs"],
    queryFn: fetch_runs,
    staleTime: 5_000,
    select: mapBacktestRunListPayload,
  });

  const detail_query = useQuery({
    queryKey: ["backtest", "run", selectedRunId],
    queryFn: () => fetch_run_detail(selectedRunId ?? ""),
    enabled: selectedRunId !== null && selectedRunId.length > 0,
    staleTime: 30_000,
    select: mapBacktestRunDetailPayload,
  });

  const seed_mutation = useMutation({
    mutationFn: async () => {
      const response = await fetch(apiUrl("/api/backtest/seed-demo"), {
        method: "POST",
        headers: { Accept: "application/json" },
      });
      if (!response.ok) {
        throw new Error(`backtest_seed_failed_${response.status}`);
      }
      return mapBacktestSeedPayload(await response.json());
    },
    onSuccess: () => {
      void query_client.invalidateQueries({ queryKey: ["backtest"] });
    },
  });

  const run_mutation = useMutation({
    mutationFn: async () => {
      const response = await fetch(apiUrl("/api/backtest/run"), {
        method: "POST",
        headers: { Accept: "application/json", "Content-Type": "application/json" },
        body: JSON.stringify({
          asset: asset.trim().toUpperCase(),
          timeframe: timeframe.trim(),
          startDay,
          endDay,
          initialCapitalUsd: capital.trim(),
          scoreThreshold: Number(scoreThreshold),
        }),
      });
      if (!response.ok) {
        throw new Error(`backtest_run_failed_${response.status}`);
      }
      return mapBacktestRunCreatedPayload(await response.json());
    },
    onSuccess: (payload) => {
      if (payload) {
        setLastRun(payload);
        setSelectedRunId(payload.runId);
      }
      void query_client.invalidateQueries({ queryKey: ["backtest"] });
    },
  });

  const active_detail: BacktestRunDetailMapped | null = useMemo(() => {
    if (detail_query.data) {
      return detail_query.data;
    }
    if (lastRun && lastRun.runId === selectedRunId) {
      return {
        run: {
          runId: lastRun.runId,
          createdAtIso: "",
          asset,
          timeframe,
          startTsIso: startDay,
          endTsIso: endDay,
          initialCapitalUsd: capital,
          netPnlUsd: lastRun.metrics.netPnlUsd,
          winRate: lastRun.metrics.winRate,
          totalTrades: lastRun.metrics.totalTrades,
          vetoCount: lastRun.metrics.vetoCount,
        },
        metrics: lastRun.metrics,
        equityCurve: lastRun.equityCurve,
        trades: [],
        disclaimer: lastRun.disclaimer,
      };
    }
    return null;
  }, [detail_query.data, lastRun, selectedRunId, asset, timeframe, startDay, endDay, capital]);

  const equity_pts = active_detail ? equity_to_spark(active_detail.equityCurve) : [];
  const inventory = inventory_query.data;
  const runs: BacktestRunSummaryMapped[] = runs_query.data ?? [];

  if (inventory_query.isLoading) {
    return (
      <section className="space-y-4" aria-busy="true" aria-label="Backtest loading">
        <Skeleton className="block h-10 w-72" height={40} />
        <Skeleton className="block h-48 w-full" height={192} />
      </section>
    );
  }

  if (inventory_query.error !== null) {
    return (
      <EmptyState
        title="Backtest API unavailable"
        description="Ensure ATLAS is running with the /api/backtest routes mounted."
      />
    );
  }

  const warehouse_empty =
    (inventory?.candleCount ?? 0) === 0 || (inventory?.signalCount ?? 0) === 0;

  return (
    <div className="space-y-6" aria-live="polite">
      <header className="flex flex-wrap items-baseline justify-between gap-3">
        <div>
          <h1 className="text-lg font-black text-[var(--text-primary)]">Backtest Suite</h1>
          <p className="mt-1 max-w-3xl text-xs text-[var(--text-secondary)]">
            Offline DuckDB replay of imported OHLCV and recorded POLARIS signals through the PROMETHEUS
            execution simulator (fees, slippage, score gate, risk vetoes). Distinct from Paper Trading’s
            heuristic Postgres score path.
          </p>
        </div>
      </header>

      <section className="grid gap-3 sm:grid-cols-4">
        <div className="rounded-[var(--radius-sm)] border border-white/10 bg-black/35 p-3">
          <div className="text-[10px] font-semibold uppercase text-[var(--text-tertiary)]">Candles</div>
          <div className="font-data mt-1 text-lg text-[var(--text-primary)]">{inventory?.candleCount ?? 0}</div>
        </div>
        <div className="rounded-[var(--radius-sm)] border border-white/10 bg-black/35 p-3">
          <div className="text-[10px] font-semibold uppercase text-[var(--text-tertiary)]">Signals</div>
          <div className="font-data mt-1 text-lg text-[var(--text-primary)]">{inventory?.signalCount ?? 0}</div>
        </div>
        <div className="rounded-[var(--radius-sm)] border border-white/10 bg-black/35 p-3">
          <div className="text-[10px] font-semibold uppercase text-[var(--text-tertiary)]">Runs</div>
          <div className="font-data mt-1 text-lg text-[var(--text-primary)]">{inventory?.runCount ?? 0}</div>
        </div>
        <div className="rounded-[var(--radius-sm)] border border-white/10 bg-black/35 p-3">
          <div className="text-[10px] font-semibold uppercase text-[var(--text-tertiary)]">Assets</div>
          <div className="mt-1 text-xs text-[var(--text-secondary)]">
            {(inventory?.assets ?? []).join(", ") || "—"}
          </div>
        </div>
      </section>

      {warehouse_empty ? (
        <div className="rounded-[var(--radius-md)] border border-dashed border-[var(--accent-cyan)]/40 bg-[rgba(8,13,21,0.45)] px-4 py-4">
          <p className="text-sm text-[var(--text-secondary)]">
            The DuckDB warehouse is empty. Load the bundled BTCUSDT smoke dataset to run your first replay.
          </p>
          <button
            type="button"
            className="mt-3 rounded-[var(--radius-sm)] bg-[var(--accent-cyan)] px-4 py-2 text-xs font-bold text-black hover:opacity-90 disabled:opacity-50"
            disabled={seed_mutation.isPending}
            onClick={() => seed_mutation.mutate()}
          >
            {seed_mutation.isPending ? "Loading demo data…" : "Load demo dataset"}
          </button>
          {seed_mutation.error ? (
            <p className="mt-2 text-xs text-[var(--danger)]">Demo seed failed — check server logs.</p>
          ) : null}
          {seed_mutation.data?.message ? (
            <p className="mt-2 text-xs text-[var(--text-tertiary)]">{seed_mutation.data.message}</p>
          ) : null}
        </div>
      ) : null}

      <div className="grid gap-4 xl:grid-cols-[minmax(0,22rem)_1fr]">
        <section className="space-y-4 rounded-[var(--radius-md)] border border-white/10 bg-[rgba(8,13,21,0.55)] p-4">
          <h2 className="text-xs font-bold uppercase tracking-wide text-[var(--accent-cyan)]">Run replay</h2>
          <form
            className="space-y-3"
            onSubmit={(event) => {
              event.preventDefault();
              run_mutation.mutate();
            }}
          >
            <label className="block text-[11px] text-[var(--text-tertiary)]">
              Asset
              <input
                className="mt-1 w-full rounded border border-white/10 bg-black/40 px-2 py-1.5 text-sm text-[var(--text-primary)]"
                value={asset}
                onChange={(e) => setAsset(e.target.value)}
              />
            </label>
            <label className="block text-[11px] text-[var(--text-tertiary)]">
              Timeframe
              <input
                className="mt-1 w-full rounded border border-white/10 bg-black/40 px-2 py-1.5 text-sm text-[var(--text-primary)]"
                value={timeframe}
                onChange={(e) => setTimeframe(e.target.value)}
              />
            </label>
            <div className="grid grid-cols-2 gap-2">
              <label className="block text-[11px] text-[var(--text-tertiary)]">
                Start (UTC)
                <input
                  type="date"
                  className="mt-1 w-full rounded border border-white/10 bg-black/40 px-2 py-1.5 text-sm text-[var(--text-primary)]"
                  value={startDay}
                  onChange={(e) => setStartDay(e.target.value)}
                />
              </label>
              <label className="block text-[11px] text-[var(--text-tertiary)]">
                End (UTC)
                <input
                  type="date"
                  className="mt-1 w-full rounded border border-white/10 bg-black/40 px-2 py-1.5 text-sm text-[var(--text-primary)]"
                  value={endDay}
                  onChange={(e) => setEndDay(e.target.value)}
                />
              </label>
            </div>
            <label className="block text-[11px] text-[var(--text-tertiary)]">
              Initial capital (USD)
              <input
                className="mt-1 w-full rounded border border-white/10 bg-black/40 px-2 py-1.5 text-sm text-[var(--text-primary)]"
                value={capital}
                onChange={(e) => setCapital(e.target.value)}
              />
            </label>
            <label className="block text-[11px] text-[var(--text-tertiary)]">
              Score threshold
              <input
                className="mt-1 w-full rounded border border-white/10 bg-black/40 px-2 py-1.5 text-sm text-[var(--text-primary)]"
                value={scoreThreshold}
                onChange={(e) => setScoreThreshold(e.target.value)}
              />
            </label>
            <button
              type="submit"
              className="w-full rounded-[var(--radius-sm)] bg-[var(--accent-cyan)] py-2 text-xs font-bold text-black hover:opacity-90 disabled:opacity-50"
              disabled={run_mutation.isPending || warehouse_empty}
            >
              {run_mutation.isPending ? "Running replay…" : "Run backtest"}
            </button>
          </form>

          <div>
            <h3 className="text-[11px] font-semibold uppercase text-[var(--text-secondary)]">Recent runs</h3>
            <ul className="mt-2 max-h-48 space-y-1 overflow-auto text-xs">
              {runs.length === 0 ? (
                <li className="text-[var(--text-tertiary)]">No runs yet.</li>
              ) : (
                runs.map((row) => (
                  <li key={row.runId}>
                    <button
                      type="button"
                      className={`w-full rounded px-2 py-1.5 text-left hover:bg-white/5 ${
                        selectedRunId === row.runId ? "bg-white/10 text-[var(--accent-cyan)]" : "text-[var(--text-primary)]"
                      }`}
                      onClick={() => setSelectedRunId(row.runId)}
                    >
                      <span className="font-data">{row.asset}</span>
                      <span className="text-[var(--text-tertiary)]"> · {row.timeframe}</span>
                      {row.netPnlUsd ? (
                        <span className="ml-1 text-[var(--text-secondary)]">
                          {format_usd_compact_display(row.netPnlUsd, "—")}
                        </span>
                      ) : null}
                    </button>
                  </li>
                ))
              )}
            </ul>
          </div>
        </section>

        <section className="min-h-[24rem] rounded-[var(--radius-md)] border border-white/10 bg-[rgba(8,13,21,0.55)] p-4">
          {!active_detail ? (
            <EmptyState
              title="Select or run a backtest"
              description="Choose a run from the list or submit the replay form to see metrics and equity."
            />
          ) : (
            <div className="space-y-4">
              <div className="grid gap-3 sm:grid-cols-4">
                <div className="rounded border border-white/5 bg-black/35 p-3">
                  <div className="text-[10px] uppercase text-[var(--text-tertiary)]">Net PnL</div>
                  <div className="font-data mt-1 text-sm">
                    {format_usd_compact_display(active_detail.metrics.netPnlUsd, "—")}
                  </div>
                </div>
                <div className="rounded border border-white/5 bg-black/35 p-3">
                  <div className="text-[10px] uppercase text-[var(--text-tertiary)]">Win rate</div>
                  <div className="font-data mt-1 text-sm">{format_pct(active_detail.metrics.winRate)}</div>
                </div>
                <div className="rounded border border-white/5 bg-black/35 p-3">
                  <div className="text-[10px] uppercase text-[var(--text-tertiary)]">Trades</div>
                  <div className="font-data mt-1 text-sm">{active_detail.metrics.totalTrades}</div>
                </div>
                <div className="rounded border border-white/5 bg-black/35 p-3">
                  <div className="text-[10px] uppercase text-[var(--text-tertiary)]">Max DD</div>
                  <div className="font-data mt-1 text-sm">
                    {(active_detail.metrics.maxDrawdownPct * 100).toFixed(2)}%
                  </div>
                </div>
              </div>

              <div className="rounded border border-white/5 bg-black/25 p-2">
                <div className="px-2 text-[10px] font-semibold uppercase text-[var(--text-tertiary)]">
                  Equity curve (USD)
                </div>
                {equity_pts.length > 2 ? (
                  <SvgSparkline title="Backtest equity" points={equity_pts} />
                ) : (
                  <p className="px-2 py-4 text-[11px] text-[var(--text-tertiary)]">Not enough points to plot.</p>
                )}
              </div>

              {active_detail.trades.length > 0 ? (
                <div className="overflow-auto">
                  <table className="w-full text-left text-[11px]">
                    <thead className="text-[var(--text-tertiary)]">
                      <tr>
                        <th className="px-2 py-1">Direction</th>
                        <th className="px-2 py-1">Entry</th>
                        <th className="px-2 py-1">Exit</th>
                        <th className="px-2 py-1">Net PnL</th>
                        <th className="px-2 py-1">Reason</th>
                      </tr>
                    </thead>
                    <tbody>
                      {active_detail.trades.map((trade) => (
                        <tr key={trade.tradeId} className="border-t border-white/5 text-[var(--text-primary)]">
                          <td className="px-2 py-1">{trade.direction}</td>
                          <td className="px-2 py-1 font-data">{trade.entryPrice}</td>
                          <td className="px-2 py-1 font-data">{trade.exitPrice ?? "—"}</td>
                          <td className="px-2 py-1 font-data">
                            {trade.netPnlUsd ? format_usd_compact_display(trade.netPnlUsd, "—") : "—"}
                          </td>
                          <td className="px-2 py-1 text-[var(--text-tertiary)]">{trade.exitReason ?? "—"}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : null}

              {active_detail.disclaimer.length > 0 ? (
                <p className="text-[11px] leading-snug text-[var(--text-tertiary)]">{active_detail.disclaimer}</p>
              ) : null}
            </div>
          )}
        </section>
      </div>
    </div>
  );
}
