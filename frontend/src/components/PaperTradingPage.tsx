import { useQuery } from "@tanstack/react-query";
import type { ReactElement } from "react";

import { SvgSparkline, type SvgSparklinePoint } from "./charts/SvgSparkline";
import { SignalFeedTable } from "./signals/SignalFeedTable";
import { EmptyState } from "./ui/EmptyState";
import { Skeleton } from "./ui/Skeleton";
import { format_usd_compact_display } from "../lib/format-usd-compact";
import { mapPaperParallelValidationPayload } from "../lib/paper-parallel-mapper";
import { coerce_signal_feed_rows, map_signal_feed_payload } from "../lib/signal-feed-mapper";
import { apiUrl } from "../lib/url";

async function fetch_signal_feed_json(): Promise<unknown> {
  const response = await fetch(apiUrl("/api/signals/feed"), {
    headers: { Accept: "application/json" },
  });
  if (!response.ok) {
    throw new Error(`signal_feed_failed_${response.status}`);
  }
  return response.json() as Promise<unknown>;
}

async function fetch_parallel_validation_json(): Promise<unknown> {
  const response = await fetch(apiUrl("/api/paper-trade/parallel-validation"), {
    headers: { Accept: "application/json" },
  });
  if (!response.ok) {
    throw new Error(`paper_parallel_failed_${response.status}`);
  }
  return response.json() as Promise<unknown>;
}

const TIER_ORDER = ["180_plus", "150_179", "under_150"] as const;

const TIER_LABEL: Record<(typeof TIER_ORDER)[number], string> = {
  "180_plus": "180+",
  "150_179": "150–179",
  under_150: "<150",
};

function equity_to_spark(curve: { equityUsd: string }[]): SvgSparklinePoint[] {
  return curve
    .map((row, idx) => {
      const numeric = Number(row.equityUsd.replace(/,/g, ""));
      return { x: idx, y: numeric };
    })
    .filter((p) => Number.isFinite(p.y));
}

function drawdown_to_spark(curve: { drawdownPct: number }[]): SvgSparklinePoint[] {
  return curve.map((row, idx) => ({ x: idx, y: row.drawdownPct })).filter((p) => Number.isFinite(p.y));
}

/**
 * Paper ledger running adjacent to Redis signal snapshots — heuristic USD replay tiered by ladder score.
 */
export function PaperTradingPage(): ReactElement {
  const feed_query = useQuery({
    queryKey: ["signals", "feed"],
    queryFn: fetch_signal_feed_json,
    staleTime: 5_000,
    refetchInterval: 5_000,
    select: map_signal_feed_payload,
  });

  const sim_query = useQuery({
    queryKey: ["paper-trade", "parallel-validation"],
    queryFn: fetch_parallel_validation_json,
    staleTime: 15_000,
    refetchInterval: 15_000,
    select: mapPaperParallelValidationPayload,
  });

  const loading = feed_query.isLoading || sim_query.isLoading;
  const fetching = feed_query.isFetching || sim_query.isFetching;

  if (loading) {
    return (
      <section className="space-y-4" aria-busy="true" aria-label="Paper trading loading">
        <Skeleton className="block h-10 w-72" height={40} />
        <div className="grid gap-4 lg:grid-cols-2">
          <Skeleton className="block h-96 w-full" height={384} />
          <Skeleton className="block h-96 w-full" height={384} />
        </div>
      </section>
    );
  }

  if (feed_query.error !== null) {
    return (
      <EmptyState
        title="Live signal lane unavailable"
        description="`/api/signals/feed` did not resolve — simulated portfolio waits for the Redis rotation payload."
      />
    );
  }

  const rows = coerce_signal_feed_rows(feed_query.data);
  const ledger = sim_query.data ?? null;

  const equity_pts = ledger ? equity_to_spark(ledger.equityCurve) : [];
  const draw_pts = ledger ? drawdown_to_spark(ledger.drawdownCurve) : [];

  return (
    <div className="space-y-6" aria-live="polite">
      <header className="flex flex-wrap items-baseline justify-between gap-3">
        <div>
          <h1 className="text-lg font-black text-[var(--text-primary)]">Paper Trading</h1>
          <p className="mt-1 max-w-3xl text-xs text-[var(--text-secondary)]">
            LEFT: Redis signal mirrors the live scorer. RIGHT: rolling synthetic executions from recent Postgres audits show
            whether high-confluence sleeves actually outperform — before capital hits Bitget wiring. For DuckDB candle +
            signal replay, use the Backtest page.
          </p>
        </div>
        {fetching ? <span className="text-xs text-[var(--text-tertiary)]">Refreshing lanes…</span> : null}
      </header>

      <div className="grid min-h-[28rem] gap-4 xl:grid-cols-2">
        <section className="flex min-h-[22rem] flex-col overflow-hidden rounded-[var(--radius-md)] border border-white/10 bg-[rgba(8,13,21,0.55)] shadow-[var(--glass-shadow-soft)] backdrop-blur-sm">
          <div className="border-b border-white/10 px-4 py-3">
            <h2 className="text-xs font-bold uppercase tracking-wide text-[var(--accent-cyan)]">Live signal mirror</h2>
            <p className="mt-0.5 text-[11px] text-[var(--text-tertiary)]">Feeds `/api/signals/feed` identical to Signals page.</p>
          </div>
          <div className="min-h-0 flex-1 overflow-auto p-3">
            {rows.length === 0 ? (
              <EmptyState
                title="Awaiting rotation snapshots"
                description="Populate Redis `polaris:signals:{asset}` to populate the authoritative scoring grid."
              />
            ) : (
              <SignalFeedTable rows={rows} />
            )}
          </div>
        </section>

        <section className="flex min-h-[22rem] flex-col gap-4 overflow-hidden rounded-[var(--radius-md)] border border-white/10 bg-[rgba(8,13,21,0.55)] px-4 py-3 shadow-[var(--glass-shadow-soft)] backdrop-blur-sm">
          <div className="border-b border-white/10 pb-3">
            <h2 className="text-xs font-bold uppercase tracking-wide text-[var(--accent-cyan)]">
              Synthetic execution ledger
            </h2>
            <p className="mt-0.5 text-[11px] text-[var(--text-tertiary)]">
              Model {ledger?.pricingModel ?? "—"} · Rows ingested {ledger?.rowCountUsed ?? "—"}
            </p>
          </div>

          {sim_query.error !== null || ledger === null ? (
            <EmptyState
              title="Historical store offline"
              description="Parallel validation reads the PostgreSQL signals warehouse — ensure the archival pool is attached to ATLAS."
            />
          ) : (
            <>
              <div className="grid gap-3 sm:grid-cols-3">
                <div className="rounded-[var(--radius-sm)] border border-white/5 bg-black/35 p-3">
                  <div className="text-[10px] font-semibold uppercase tracking-wide text-[var(--text-tertiary)]">Cash start</div>
                  <div className="font-data mt-1 text-sm text-[var(--text-primary)]">
                    {format_usd_compact_display(ledger.initialUsd, "—")}
                  </div>
                </div>
                <div className="rounded-[var(--radius-sm)] border border-white/5 bg-black/35 p-3">
                  <div className="text-[10px] font-semibold uppercase tracking-wide text-[var(--text-tertiary)]">
                    Synthetic equity NOW
                  </div>
                  <div className="font-data mt-1 text-sm text-[var(--text-primary)]">
                    {format_usd_compact_display(ledger.endingUsd, "—")}
                  </div>
                </div>
                <div className="rounded-[var(--radius-sm)] border border-white/5 bg-black/35 p-3">
                  <div className="text-[10px] font-semibold uppercase tracking-wide text-[var(--text-tertiary)]">
                    Weekly Sharpe (annualized)
                  </div>
                  <div className="font-data mt-1 text-sm text-[var(--text-primary)]">
                    {(ledger.weeklySharpeAnnualized ?? 0).toFixed(2)}
                  </div>
                </div>
              </div>

              <div className="grid gap-4 md:grid-cols-2">
                <div className="rounded-[var(--radius-sm)] border border-white/5 bg-black/25 p-2">
                  <div className="px-2 text-[10px] font-semibold uppercase text-[var(--text-tertiary)]">
                    Synthetic equity rail
                  </div>
                  {equity_pts.length > 2 ? (
                    <SvgSparkline title="Synthetic equity USD" points={equity_pts} />
                  ) : (
                    <p className="px-2 py-4 text-[11px] text-[var(--text-tertiary)]">
                      Waiting for deeper signals history before plotting the rail.
                    </p>
                  )}
                </div>
                <div className="rounded-[var(--radius-sm)] border border-white/5 bg-black/25 p-2">
                  <div className="px-2 text-[10px] font-semibold uppercase text-[var(--text-tertiary)]">
                    Drawdown vs ledger peak (%)
                  </div>
                  {draw_pts.length > 2 ? (
                    <SvgSparkline title="Portfolio drawdown" points={draw_pts} accentClassName="stroke-[var(--warning)]" />
                  ) : (
                    <p className="px-2 py-4 text-[11px] text-[var(--text-tertiary)]">
                      Need additional weekly buckets to articulate drawdown.
                    </p>
                  )}
                </div>
              </div>

              <div>
                <h3 className="text-[11px] font-semibold uppercase tracking-wide text-[var(--text-secondary)]">
                  Win-rate by score sleeve
                </h3>
                <div className="mt-2 grid gap-2 sm:grid-cols-3">
                  {TIER_ORDER.map((tierKey) => {
                    const block = ledger.tierWinRates[tierKey];
                    if (!block || block.trades === 0) {
                      return (
                        <div
                          key={tierKey}
                          className="rounded-[var(--radius-sm)] border border-dashed border-white/10 px-3 py-2 text-[11px] text-[var(--text-tertiary)]"
                        >
                          {TIER_LABEL[tierKey]} · no gated rolls yet.
                        </div>
                      );
                    }
                    const width = `${Math.round(block.winRate * 100)}%`;
                    return (
                      <div
                        key={tierKey}
                        className="rounded-[var(--radius-sm)] border border-white/10 bg-black/40 px-3 py-2 text-[var(--text-primary)]"
                      >
                        <div className="text-[11px] font-semibold">{TIER_LABEL[tierKey]}</div>
                        <div className="font-data mt-1 text-xl font-semibold">{`${(block.winRate * 100).toFixed(1)}%`}</div>
                        <div className="mt-1 h-2 w-full rounded-full bg-white/10">
                          <div className="h-2 rounded-full bg-[var(--accent-cyan)]" style={{ width }} />
                        </div>
                        <div className="mt-2 text-[10px] text-[var(--text-tertiary)]">
                          {`${block.wins} / ${block.trades}`} gated rolls realised
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>

              {ledger.disclaimer.length > 0 ? (
                <p className="text-[11px] leading-snug text-[var(--text-tertiary)]">{ledger.disclaimer}</p>
              ) : null}
            </>
          )}
        </section>
      </div>
    </div>
  );
}
