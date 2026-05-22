import type { ReactElement } from "react";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { cn } from "../../lib/cn";
import { Badge } from "../ui/Badge";
import { Card } from "../ui/Card";
import { EmptyState } from "../ui/EmptyState";
import { Spinner } from "../ui/Spinner";
import { StatusDot } from "../ui/StatusDot";

interface RiskGovernorLimits {
  totalExposureMaxPct: number;
  dailyDrawdownLimitPct: number;
  weeklyDrawdownLimitPct: number;
  intradayPnl1hVetoPct: number;
  intradayPnl4hVetoPct: number;
  intradayPnl24hShutdownPct: number;
}

interface TierExposureRow {
  tierId: string;
  label: string;
  exposurePct: number;
}

interface PipelineCircuit {
  stage: string;
  state: string;
}

interface VetoHistoryDay {
  dayIso: string;
  vetoCount: number;
}

interface VetoReasonBucket {
  reason: string;
  count: number;
}

interface RecentVeto {
  tsIso: string;
  asset: string;
  cycleId: string;
  reasons: string[];
}

interface RiskGovernorSnapshot {
  asOfIso: string;
  totalPortfolioExposurePct: number;
  dailyDrawdownPct: number;
  weeklyDrawdownPct: number;
  trailingPnl1hPct: number;
  trailingPnl4hPct: number;
  trailingPnl24hPct: number;
  maxSinglePositionAllowedPct: number;
  portfolioEquityUsd: string;
  tierExposure: TierExposureRow[];
  pipelineCircuits: PipelineCircuit[];
  tradingHalted: boolean;
  haltReason: string | null;
  haltTriggeredBy: string | null;
  haltTimestampIso: string | null;
  policyLimits: RiskGovernorLimits;
  vetoEvents30dTotal: number;
  vetoHistoryByDay: VetoHistoryDay[];
  vetoReasonBuckets: VetoReasonBucket[];
  recentVetoes: RecentVeto[];
}

function finiteNumber(value: unknown): number {
  if (typeof value === "number" && Number.isFinite(value)) {
    return value;
  }
  if (typeof value === "string") {
    const t = value.trim();
    if (t === "") {
      return 0;
    }
    const n = Number(t);
    return Number.isFinite(n) ? n : 0;
  }
  return 0;
}

function pickTrailingPnl(raw: Record<string, unknown>, camel: string, snake: string): number {
  for (const key of [camel, snake]) {
    if (!(key in raw)) {
      continue;
    }
    const coerced = finiteNumber(raw[key]);
    if (Number.isFinite(coerced)) {
      return coerced;
    }
  }
  return 0;
}

function formatFixed(value: unknown, fractionDigits: number): string {
  return finiteNumber(value).toFixed(fractionDigits);
}

function firstPresentNumber(raw: Record<string, unknown>, keys: readonly string[]): number {
  for (const key of keys) {
    if (!(key in raw)) {
      continue;
    }
    const coerced = finiteNumber(raw[key]);
    if (Number.isFinite(coerced)) {
      return coerced;
    }
  }
  return 0;
}

function normalizeTierExposure(rows: unknown): TierExposureRow[] {
  if (!Array.isArray(rows)) {
    return [];
  }
  return rows.map((row) => {
    const o = row as Record<string, unknown>;
    return {
      tierId: String(o.tierId ?? o.tier_id ?? ""),
      label: String(o.label ?? ""),
      exposurePct: finiteNumber(o.exposurePct ?? o.exposure_pct),
    };
  });
}

function normalizePolicyLimits(raw: Record<string, unknown>, limits: RiskGovernorLimits | undefined): RiskGovernorLimits {
  const l = limits as unknown as Record<string, unknown> | undefined;
  const src = l && typeof l === "object" ? l : {};
  return {
    totalExposureMaxPct: firstPresentNumber(src, ["totalExposureMaxPct", "total_exposure_max_pct"]),
    dailyDrawdownLimitPct: firstPresentNumber(src, ["dailyDrawdownLimitPct", "daily_drawdown_limit_pct"]),
    weeklyDrawdownLimitPct: firstPresentNumber(src, ["weeklyDrawdownLimitPct", "weekly_drawdown_limit_pct"]),
    intradayPnl1hVetoPct: firstPresentNumber(src, ["intradayPnl1hVetoPct", "intraday_pnl_1h_veto_pct"]),
    intradayPnl4hVetoPct: firstPresentNumber(src, ["intradayPnl4hVetoPct", "intraday_pnl_4h_veto_pct"]),
    intradayPnl24hShutdownPct: firstPresentNumber(src, ["intradayPnl24hShutdownPct", "intraday_pnl_24h_shutdown_pct"]),
  };
}

function normalizeSnapshot(raw: Record<string, unknown>): RiskGovernorSnapshot {
  const base = raw as unknown as RiskGovernorSnapshot;
  const limits = base.policyLimits;
  return {
    ...base,
    asOfIso: String(base.asOfIso ?? raw.as_of_iso ?? ""),
    totalPortfolioExposurePct: firstPresentNumber(raw, ["totalPortfolioExposurePct", "total_portfolio_exposure_pct"]),
    dailyDrawdownPct: firstPresentNumber(raw, ["dailyDrawdownPct", "daily_drawdown_pct"]),
    weeklyDrawdownPct: firstPresentNumber(raw, ["weeklyDrawdownPct", "weekly_drawdown_pct"]),
    trailingPnl1hPct: pickTrailingPnl(raw, "trailingPnl1hPct", "trailing_pnl_1h_pct"),
    trailingPnl4hPct: pickTrailingPnl(raw, "trailingPnl4hPct", "trailing_pnl_4h_pct"),
    trailingPnl24hPct: pickTrailingPnl(raw, "trailingPnl24hPct", "trailing_pnl_24h_pct"),
    maxSinglePositionAllowedPct: firstPresentNumber(raw, [
      "maxSinglePositionAllowedPct",
      "max_single_position_allowed_pct",
    ]),
    portfolioEquityUsd: String(base.portfolioEquityUsd ?? raw.portfolio_equity_usd ?? "—"),
    tierExposure: normalizeTierExposure(raw.tierExposure ?? raw.tier_exposure),
    pipelineCircuits: Array.isArray(base.pipelineCircuits)
      ? base.pipelineCircuits
      : Array.isArray(raw.pipeline_circuits)
        ? (raw.pipeline_circuits as PipelineCircuit[])
        : [],
    policyLimits: normalizePolicyLimits(raw, limits),
    vetoEvents30dTotal: finiteNumber(raw.vetoEvents30dTotal ?? raw.veto_events_30d_total),
    vetoHistoryByDay: Array.isArray(base.vetoHistoryByDay)
      ? base.vetoHistoryByDay
      : Array.isArray(raw.veto_history_by_day)
        ? (raw.veto_history_by_day as VetoHistoryDay[])
        : [],
    vetoReasonBuckets: Array.isArray(base.vetoReasonBuckets)
      ? base.vetoReasonBuckets
      : Array.isArray(raw.veto_reason_buckets)
        ? (raw.veto_reason_buckets as VetoReasonBucket[])
        : [],
    recentVetoes: Array.isArray(base.recentVetoes)
      ? base.recentVetoes
      : Array.isArray(raw.recent_vetoes)
        ? (raw.recent_vetoes as RecentVeto[])
        : [],
    tradingHalted: Boolean(base.tradingHalted ?? raw.trading_halted),
    haltReason: (base.haltReason ?? raw.halt_reason ?? null) as string | null,
    haltTriggeredBy: (base.haltTriggeredBy ?? raw.halt_triggered_by ?? null) as string | null,
    haltTimestampIso: (base.haltTimestampIso ?? raw.halt_timestamp_iso ?? null) as string | null,
  };
}

async function fetchSnapshot(): Promise<RiskGovernorSnapshot> {
  const res = await fetch("/api/risk-governor/snapshot");
  if (!res.ok) {
    throw new Error(`snapshot_failed_${res.status}`);
  }
  const raw = (await res.json()) as Record<string, unknown>;
  return normalizeSnapshot(raw);
}

function exposureBarClass(pct: number, limitPct: number): string {
  const over = pct >= limitPct;
  return over ? "bg-[var(--sell)]/90" : "bg-[var(--accent-cyan)]/80";
}

export function RiskGovernorPage(): ReactElement {
  const queryClient = useQueryClient();
  const snapQ = useQuery({
    queryKey: ["risk-governor-snapshot"],
    queryFn: fetchSnapshot,
    refetchInterval: 10_000,
    select: (row) => normalizeSnapshot(row as unknown as Record<string, unknown>),
  });

  const haltMut = useMutation({
    mutationFn: async (panicKey: string) => {
      const res = await fetch("/api/risk-governor/kill-switch/halt", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-Panic-Key": panicKey,
        },
        body: JSON.stringify({ panicKey }),
      });
      if (!res.ok) {
        const detail = await res.text();
        throw new Error(detail || `halt_${res.status}`);
      }
      return res.json() as Promise<{ status: string }>;
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["risk-governor-snapshot"] });
    },
  });

  const data = snapQ.data;
  const maxVetoDay = Math.max(1, ...(data?.vetoHistoryByDay.map((d) => d.vetoCount) ?? [1]));

  return (
    <div className="flex min-h-0 flex-col space-y-4">
      <header className="shrink-0">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0 flex-1">
            <h1 className="font-mono text-lg font-semibold tracking-tight text-[var(--text-primary)]">
              Risk Governor
            </h1>
            <p className="mt-1 max-w-3xl text-xs leading-relaxed text-[var(--text-secondary)]">
              Safety-layer telemetry: portfolio exposure, drawdown lines, pipeline circuit states, and Risk
              Manager veto history for threshold calibration. When live, this is your first screen each
              session.
            </p>
          </div>
          {snapQ.isFetching ? (
            <span className="inline-flex items-center gap-2 rounded-md border border-[var(--border)] bg-[var(--bg-elevated)] px-3 py-1.5 text-[11px] text-[var(--text-secondary)]">
              <Spinner className="h-3 w-3" />
              Refreshing
            </span>
          ) : null}
        </div>
      </header>

      <KillSwitchBar
        tradingHalted={data?.tradingHalted}
        haltReason={data?.haltReason}
        haltTriggeredBy={data?.haltTriggeredBy}
        haltTimestampIso={data?.haltTimestampIso}
        onHalt={(key) => haltMut.mutate(key)}
        haltError={haltMut.error instanceof Error ? haltMut.error.message : null}
        isHalting={haltMut.isPending}
      />

      {snapQ.isError ? (
        <p className="text-xs text-[var(--sell)]" role="status">
          Could not load Risk Governor snapshot. Ensure the API is reachable.
        </p>
      ) : null}

      {snapQ.isLoading ? (
        <div className="flex min-h-[30vh] items-center justify-center text-sm text-[var(--text-secondary)]">
          <Spinner className="mr-2 h-4 w-4" />
          Loading safety telemetry…
        </div>
      ) : null}

      {!snapQ.isLoading && data ? (
        <>
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
            <Card className="border-[var(--border)] bg-[var(--bg-elevated)]/60 p-4">
              <div className="text-[11px] font-semibold uppercase tracking-wide text-[var(--text-secondary)]">
                Total exposure
              </div>
              <div className="mt-2 font-data text-2xl tabular-nums text-[var(--text-primary)]">
                {formatFixed(data.totalPortfolioExposurePct, 1)}%
              </div>
              <div className="mt-2 h-1.5 w-full overflow-hidden rounded-full bg-black/40">
                <div
                  className={cn(
                    "h-full rounded-full transition-all",
                    exposureBarClass(data.totalPortfolioExposurePct, data.policyLimits.totalExposureMaxPct),
                  )}
                  style={{
                    width: `${Math.min(100, (data.totalPortfolioExposurePct / Math.max(data.policyLimits.totalExposureMaxPct, 0.1)) * 100)}%`,
                  }}
                />
              </div>
              <p className="mt-1 text-[10px] text-[var(--text-secondary)]">
                Limit {data.policyLimits.totalExposureMaxPct.toFixed(0)}% — veto-class exposure halving above
                policy bands (see Risk agent).
              </p>
            </Card>

            <Card className="border-[var(--border)] bg-[var(--bg-elevated)]/60 p-4">
              <div className="text-[11px] font-semibold uppercase tracking-wide text-[var(--text-secondary)]">
                Daily drawdown (rolling)
              </div>
              <div className="mt-2 font-data text-2xl tabular-nums text-[var(--text-primary)]">
                {data.dailyDrawdownPct.toFixed(2)}%
              </div>
              <p className="mt-1 text-[10px] text-[var(--text-secondary)]">
                vs. limit {data.policyLimits.dailyDrawdownLimitPct.toFixed(1)}% (Prometheus{" "}
                <code className="font-mono text-[var(--accent-cyan)]">portfolio:daily_drawdown_pct</code>)
              </p>
            </Card>

            <Card className="border-[var(--border)] bg-[var(--bg-elevated)]/60 p-4">
              <div className="text-[11px] font-semibold uppercase tracking-wide text-[var(--text-secondary)]">
                Weekly drawdown (rolling)
              </div>
              <div className="mt-2 font-data text-2xl tabular-nums text-[var(--text-primary)]">
                {data.weeklyDrawdownPct.toFixed(2)}%
              </div>
              <p className="mt-1 text-[10px] text-[var(--text-secondary)]">
                vs. limit {data.policyLimits.weeklyDrawdownLimitPct.toFixed(1)}% (
                <code className="font-mono text-[var(--accent-cyan)]">portfolio:weekly_drawdown_pct</code>)
              </p>
            </Card>

            <Card className="border-[var(--border)] bg-[var(--bg-elevated)]/60 p-4">
              <div className="text-[11px] font-semibold uppercase tracking-wide text-[var(--text-secondary)]">
                Max single position
              </div>
              <div className="mt-2 font-data text-2xl tabular-nums text-[var(--text-primary)]">
                {data.maxSinglePositionAllowedPct.toFixed(2)}%
              </div>
              <p className="mt-1 text-[10px] text-[var(--text-secondary)]">
                of equity (default cap 10% —{" "}
                <code className="font-mono text-[var(--accent-cyan)]">portfolio:max_single_position_pct</code>
                )
              </p>
              <p className="mt-2 text-[11px] text-[var(--text-secondary)]">
                Equity USD{" "}
                <span className="font-mono text-[var(--text-primary)]">{data.portfolioEquityUsd}</span>
              </p>
            </Card>
          </div>

          <div className="grid gap-4 xl:grid-cols-2">
            <Card className="border-[var(--border)] bg-[var(--bg-elevated)]/60 p-4">
              <h2 className="text-sm font-semibold text-[var(--text-primary)]">Trailing PnL vs. veto rails</h2>
              <p className="mt-1 text-[11px] leading-relaxed text-[var(--text-secondary)]">
                Mirrors the Risk agent fast-path: 1h / 4h / 24h portfolio PnL gates (
                <code className="font-mono">portfolio:pnl_*</code>).
              </p>
              <dl className="mt-3 space-y-2 text-xs">
                <div className="flex justify-between gap-3 border-b border-[var(--border)]/60 pb-2 font-mono tabular-nums">
                  <dt>1h PnL</dt>
                  <dd
                    className={cn(
                      finiteNumber(data.trailingPnl1hPct) <= finiteNumber(data.policyLimits?.intradayPnl1hVetoPct)
                        ? "text-[var(--sell)]"
                        : "",
                    )}
                  >
                    {finiteNumber(data.trailingPnl1hPct).toFixed(2)}% / veto at{" "}
                    {finiteNumber(data.policyLimits?.intradayPnl1hVetoPct).toFixed(0)}%
                  </dd>
                </div>
                <div className="flex justify-between gap-3 border-b border-[var(--border)]/60 pb-2 font-mono tabular-nums">
                  <dt>4h PnL</dt>
                  <dd
                    className={cn(
                      finiteNumber(data.trailingPnl4hPct) <= finiteNumber(data.policyLimits?.intradayPnl4hVetoPct)
                        ? "text-[var(--sell)]"
                        : "",
                    )}
                  >
                    {finiteNumber(data.trailingPnl4hPct).toFixed(2)}% / veto at{" "}
                    {finiteNumber(data.policyLimits?.intradayPnl4hVetoPct).toFixed(0)}%
                  </dd>
                </div>
                <div className="flex justify-between gap-3 font-mono tabular-nums">
                  <dt>24h PnL</dt>
                  <dd
                    className={cn(
                      finiteNumber(data.trailingPnl24hPct) <=
                      finiteNumber(data.policyLimits?.intradayPnl24hShutdownPct)
                        ? "text-[var(--sell)]"
                        : "",
                    )}
                  >
                    {finiteNumber(data.trailingPnl24hPct).toFixed(2)}% / shutdown broadcast at{" "}
                    {finiteNumber(data.policyLimits?.intradayPnl24hShutdownPct).toFixed(0)}%
                  </dd>
                </div>
              </dl>
            </Card>

            <Card className="border-[var(--border)] bg-[var(--bg-elevated)]/60 p-4">
              <h2 className="text-sm font-semibold text-[var(--text-primary)]">Pipeline circuit breakers</h2>
              <p className="mt-1 text-[11px] text-[var(--text-secondary)]">
                Latency-SLA stages (Redis <code className="font-mono">cb:&#123;stage&#125;:state</code>).{" "}
                <span className="text-[var(--text-secondary)]">open</span> = tripped / blocking non-critical work;{" "}
                <span className="text-[var(--text-secondary)]">degraded</span> = throttled.
              </p>
              <ul className="mt-3 space-y-2">
                {data.pipelineCircuits.map((c) => (
                  <li
                    key={c.stage}
                    className="flex items-center justify-between gap-3 rounded-md border border-[var(--border)]/50 bg-black/20 px-3 py-2 text-xs"
                  >
                    <span className="font-mono text-[var(--text-secondary)]">{c.stage}</span>
                    <Badge
                      variant={
                        c.state === "closed"
                          ? "bull"
                          : c.state === "degraded"
                            ? "degraded"
                            : c.state === "open"
                              ? "bear"
                              : "no-position"
                      }
                    >
                      {c.state === "closed" ? "armed (closed)" : c.state}
                    </Badge>
                  </li>
                ))}
              </ul>
            </Card>
          </div>

          <Card className="border-[var(--border)] bg-[var(--bg-elevated)]/60 p-4">
            <h2 className="text-sm font-semibold text-[var(--text-primary)]">Exposure by tier</h2>
            <p className="mt-1 text-[11px] text-[var(--text-secondary)]">
              Bucket notionals from <code className="font-mono">portfolio:exposure_by_tier</code> JSON (core,
              majors, l1_l2, defi, rotation). Populate from PROMETHEUS position tagging.
            </p>
            <div className="mt-4 grid gap-2 sm:grid-cols-5">
              {data.tierExposure.map((t) => (
                <div
                  key={t.tierId}
                  className="rounded-md border border-[var(--border)]/60 bg-black/25 px-3 py-2 text-center"
                >
                  <div className="text-[10px] font-semibold uppercase tracking-wide text-[var(--text-secondary)]">
                    {t.label}
                  </div>
                  <div className="mt-1 font-data text-lg tabular-nums text-[var(--text-primary)]">
                    {t.exposurePct.toFixed(1)}%
                  </div>
                </div>
              ))}
            </div>
          </Card>

          <div className="grid gap-4 xl:grid-cols-[1.1fr_0.9fr]">
            <Card className="border-[var(--border)] bg-[var(--bg-elevated)]/60 p-4">
              <div className="flex flex-wrap items-end justify-between gap-2">
                <div>
                  <h2 className="text-sm font-semibold text-[var(--text-primary)]">
                    Risk Manager vetoes — 30 days
                  </h2>
                  <p className="mt-1 text-[11px] text-[var(--text-secondary)]">
                    {data.vetoEvents30dTotal} vetoes in window — use reason mix to judge if gates are too tight
                    or loose.
                  </p>
                </div>
                <span className="font-mono text-[10px] text-[var(--text-secondary)]">
                  as of {data.asOfIso}
                </span>
              </div>
              <VetoSparkline days={data.vetoHistoryByDay} maxCount={maxVetoDay} />
            </Card>

            <Card className="border-[var(--border)] bg-[var(--bg-elevated)]/60 p-4">
              <h2 className="text-sm font-semibold text-[var(--text-primary)]">Top veto reasons</h2>
              {data.vetoReasonBuckets.length === 0 ? (
                <EmptyState
                  title="No vetoes logged yet"
                  description="Vetoes append when the Risk agent fast-path fires."
                />
              ) : (
                <ul className="mt-3 max-h-64 space-y-2 overflow-auto text-xs">
                  {data.vetoReasonBuckets.map((b) => (
                    <li
                      key={b.reason}
                      className="flex items-start justify-between gap-2 rounded-md border border-[var(--border)]/40 bg-black/20 px-2 py-1.5"
                    >
                      <span className="text-[var(--text-secondary)]">{b.reason}</span>
                      <span className="shrink-0 font-mono tabular-nums text-[var(--text-primary)]">{b.count}</span>
                    </li>
                  ))}
                </ul>
              )}
            </Card>
          </div>

          <Card className="border-[var(--border)] bg-[var(--bg-elevated)]/60 p-4">
            <h2 className="text-sm font-semibold text-[var(--text-primary)]">Recent Risk vetoes</h2>
            <div className="mt-3 overflow-auto">
              <table className="min-w-full border-collapse text-left text-[11px]">
                <thead>
                  <tr className="border-b border-[var(--border)] text-[var(--text-secondary)]">
                    <th className="py-2 pr-4 font-medium">Time (UTC)</th>
                    <th className="py-2 pr-4 font-medium">Asset</th>
                    <th className="py-2 pr-4 font-medium">Cycle</th>
                    <th className="py-2 font-medium">Reasons</th>
                  </tr>
                </thead>
                <tbody>
                  {data.recentVetoes.length === 0 ? (
                    <tr>
                      <td colSpan={4} className="py-6 text-center text-[var(--text-secondary)]">
                        No vetoes recorded.
                      </td>
                    </tr>
                  ) : (
                    data.recentVetoes.map((r) => (
                      <tr key={`${r.tsIso}-${r.asset}-${r.cycleId}`} className="border-b border-[var(--border)]/40">
                        <td className="py-2 pr-4 font-mono text-[var(--text-primary)]">{r.tsIso}</td>
                        <td className="py-2 pr-4 font-semibold text-[var(--accent-cyan)]">{r.asset}</td>
                        <td className="py-2 pr-4 font-mono text-[var(--text-secondary)]">{r.cycleId}</td>
                        <td className="py-2 text-[var(--text-secondary)]">{r.reasons.join(" · ")}</td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>
          </Card>
        </>
      ) : null}
    </div>
  );
}

function VetoSparkline(props: { days: VetoHistoryDay[]; maxCount: number }): ReactElement {
  const { days, maxCount } = props;
  if (days.length === 0) {
    return <p className="mt-4 text-xs text-[var(--text-secondary)]">No history.</p>;
  }
  const w = 320;
  const h = 80;
  const pad = 4;
  const pts = days.map((d, i) => {
    const x = pad + (i * (w - pad * 2)) / Math.max(1, days.length - 1);
    const y = h - pad - (d.vetoCount / maxCount) * (h - pad * 2);
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  });
  return (
    <svg
      viewBox={`0 0 ${w} ${h}`}
      className="mt-4 w-full max-w-[420px] text-[var(--accent-cyan)]"
      role="img"
      aria-label="Veto count per day last 30 days"
    >
      <title>Veto counts last 30 days</title>
      <polyline
        fill="none"
        stroke="currentColor"
        strokeWidth="1.5"
        points={pts.join(" ")}
      />
    </svg>
  );
}

interface KillSwitchBarProps {
  tradingHalted: boolean | undefined;
  haltReason: string | null | undefined;
  haltTriggeredBy: string | null | undefined;
  haltTimestampIso: string | null | undefined;
  onHalt: (panicKey: string) => void;
  haltError: string | null;
  isHalting: boolean;
}

function KillSwitchBar(props: KillSwitchBarProps): ReactElement {
  const {
    tradingHalted,
    haltReason,
    haltTriggeredBy,
    haltTimestampIso,
    onHalt,
    haltError,
    isHalting,
  } = props;
  const [opened, setOpened] = useState(false);
  const [panicKey, setPanicKey] = useState("");
  // Dev-server only: never embed POLARIS_PANIC_KEY in production bundles.
  const envKey =
    import.meta.env.DEV &&
    typeof import.meta.env.VITE_POLARIS_PANIC_KEY === "string"
      ? import.meta.env.VITE_POLARIS_PANIC_KEY
      : "";

  return (
    <Card className="border border-[var(--sell)]/40 bg-[rgba(127,29,29,0.15)] p-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex min-w-0 items-center gap-3">
          <StatusDot level={tradingHalted ? "error" : "healthy"} />
          <div>
            <div className="text-sm font-semibold text-[var(--text-primary)]">Kill switch</div>
            <p className="text-[11px] text-[var(--text-secondary)]">
              {tradingHalted
                ? `TRADING HALTED${haltReason ? ` — ${haltReason}` : ""}${haltTriggeredBy ? ` (by ${haltTriggeredBy})` : ""}${haltTimestampIso ? ` @ ${haltTimestampIso}` : ""}`
                : "PROMETHEUS execution plane idle — halt propagates via Redis + audit trail."}
            </p>
          </div>
        </div>
        <button
          type="button"
          className={cn(
            "rounded-md border px-4 py-2 text-xs font-semibold uppercase tracking-wide",
            "border-[var(--sell)] bg-[var(--sell)]/20 text-[var(--sell)] hover:bg-[var(--sell)]/30",
          )}
          onClick={() => {
            setOpened(true);
            if (envKey) {
              setPanicKey(envKey);
            }
          }}
        >
          Activate kill switch
        </button>
      </div>
      {opened ? (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4"
          role="dialog"
          aria-modal="true"
          aria-labelledby="kill-switch-title"
        >
          <Card className="w-full max-w-md border-[var(--border)] bg-[var(--bg-elevated)] p-6 shadow-xl">
            <h3 id="kill-switch-title" className="text-base font-semibold text-[var(--text-primary)]">
              Confirm emergency halt
            </h3>
            <p className="mt-2 text-xs leading-relaxed text-[var(--text-secondary)]">
              Halts PROMETHEUS trading via the canonical kill switch (Redis{" "}
              <code className="font-mono text-[var(--accent-cyan)]">prometheus:trading_halted</code>
              ). Requires the operator panic key — same as{" "}
              <code className="font-mono">POLARIS_PANIC_KEY</code> on the API host.
            </p>
            <label className="mt-4 block text-[11px] font-medium text-[var(--text-secondary)]" htmlFor="panic-key">
              Panic key
            </label>
            <input
              id="panic-key"
              type="password"
              autoComplete="off"
              className="mt-1 w-full rounded-md border border-[var(--border)] bg-black/30 px-3 py-2 font-mono text-sm text-[var(--text-primary)] outline-none focus:border-[var(--accent-cyan)]"
              value={panicKey}
              onChange={(e) => setPanicKey(e.target.value)}
              placeholder={envKey ? "(pre-filled from VITE_POLARIS_PANIC_KEY)" : ""}
            />
            {haltError ? (
              <p className="mt-2 text-xs text-[var(--sell)]" role="status">
                {haltError}
              </p>
            ) : null}
            <div className="mt-6 flex flex-wrap justify-end gap-2">
              <button
                type="button"
                className="rounded-md border border-[var(--border)] px-4 py-2 text-xs text-[var(--text-secondary)] hover:bg-white/5"
                onClick={() => {
                  setOpened(false);
                  setPanicKey("");
                }}
              >
                Cancel
              </button>
              <button
                type="button"
                disabled={isHalting || panicKey.trim().length === 0}
                className="rounded-md border border-[var(--sell)] bg-[var(--sell)]/25 px-4 py-2 text-xs font-semibold uppercase tracking-wide text-[var(--sell)] disabled:opacity-40"
                onClick={() => onHalt(panicKey.trim() || envKey)}
              >
                {isHalting ? "Halting…" : "Halt trading"}
              </button>
            </div>
          </Card>
        </div>
      ) : null}
    </Card>
  );
}
