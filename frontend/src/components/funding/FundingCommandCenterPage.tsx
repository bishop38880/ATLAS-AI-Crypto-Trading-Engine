import type { ReactElement } from "react";
import { useMemo, useState } from "react";

import { useDashboardPositionSlotsQuery } from "../../hooks/useDashboardQueries";
import { useFundingCommandCenterQuery } from "../../hooks/useFundingCommandCenterQuery";
import { derive_pair_from_rotation_entry } from "../../lib/dashboard-symbol";
import { cn } from "../../lib/cn";
import { Badge } from "../ui/Badge";
import { EmptyState } from "../ui/EmptyState";
import { GlassPanel } from "../ui/GlassPanel";
import { Skeleton } from "../ui/Skeleton";
import { Spinner } from "../ui/Spinner";
import { FundingHistoryChart, type FundingChartWindowDays } from "./FundingHistoryChart";

import type { FundingCommandRow } from "../../types/funding-command-center";

function format_micro_rate(rate: string): string {
  const n = Number(rate);
  if (!Number.isFinite(n)) {
    return "—";
  }

  const bps = n * 10_000;
  return `${bps >= 0 ? "+" : ""}${bps.toFixed(2)} bps`;
}

function format_pct_headline(decimal_pct: string): string {
  const n = Number(decimal_pct);
  if (!Number.isFinite(n)) {
    return "—";
  }

  return `${n >= 0 ? "+" : ""}${n.toFixed(2)}%/yr`;
}

function flip_signal_badge(signal: FundingCommandRow["flipSignal"]) {
  if (signal === "COMPRESS_FROM_POSITIVE") {
    return (
      <Badge variant="bear" className="max-w-[10rem] whitespace-normal leading-tight">
        Flip ↓
      </Badge>
    );
  }

  if (signal === "COMPRESS_FROM_NEGATIVE") {
    return (
      <Badge variant="bull" className="max-w-[10rem] whitespace-normal leading-tight">
        Flip ↑
      </Badge>
    );
  }

  return (
    <Badge variant="hold" className="max-w-[10rem] whitespace-normal leading-tight">
      Neutral
    </Badge>
  );
}

function pair_key_normalize(raw: string | null): string {
  if (raw === null) {
    return "";
  }

  const trimmed = raw.trim();
  if (!trimmed) {
    return "";
  }

  return derive_pair_from_rotation_entry(trimmed).toUpperCase();
}

function estimate_annual_carry_usd(carry_decimal_pct_str: string, notional_usd: number): string {
  const pct = Number(carry_decimal_pct_str);

  if (!Number.isFinite(pct) || !Number.isFinite(notional_usd) || notional_usd <= 0) {
    return "—";
  }

  const dollars = (notional_usd * pct) / 100;
  return dollars.toLocaleString(undefined, {
    maximumFractionDigits: 2,
    minimumFractionDigits: 2,
  });
}

interface PositionCarryProjectionProps {
  readonly rows_by_pair: Map<string, FundingCommandRow>;
  readonly notional_usd: number;
}

function PositionCarryProjection(props: PositionCarryProjectionProps): ReactElement {
  const slots_query = useDashboardPositionSlotsQuery();
  const slots = slots_query.data ?? [];

  return (
    <div className="space-y-3">
      <p className="text-xs leading-relaxed text-[var(--text-secondary)]">
        Ladder slots from PROMETHEUS use simple annualisation (three 8-hour prints per day, not compounded).
        USD estimate multiplies signed carry APY by the notional you enter below.
      </p>
      {!slots_query.isLoading ? null : <Skeleton className="h-20 w-full" />}
      {!slots_query.isLoading && slots.every((slot) => slot.asset === null) ? (
        <EmptyState
          title="No ladder exposures"
          description="Pin a hypothetical notional to approximate annual funding drag once slots populate."
        />
      ) : null}
      {!slots_query.isLoading ? (
        <ul className="space-y-2">
          {slots.map((slot) => {
            if (slot.asset === null || slot.direction === null) {
              return null;
            }

            const row = props.rows_by_pair.get(pair_key_normalize(slot.asset));
            const raw_carry =
              slot.direction === "LONG"
                ? row?.effectiveLongCarryPct
                : row?.effectiveShortCarryPct;

            const carry_label =
              typeof raw_carry === "string"
                ? format_pct_headline(raw_carry)
                : "— USD carry unknown";

            const usd_projection =
              typeof raw_carry === "string"
                ? estimate_annual_carry_usd(raw_carry, props.notional_usd)
                : "—";

            return (
              <li key={slot.slot_index}>
                <div className="flex flex-wrap items-baseline gap-2 rounded-lg border border-[var(--border)] bg-[rgba(15,23,42,0.35)] px-3 py-2">
                  <span className="text-[11px] font-semibold uppercase tracking-wide text-[var(--text-secondary)]">
                    Slot {slot.slot_index}
                  </span>
                  <span className="font-mono text-sm text-[var(--text-primary)]">{slot.asset}</span>
                  <Badge variant={slot.direction === "LONG" ? "bull" : "bear"}>{slot.direction}</Badge>
                  <span className="ml-auto tabular-nums text-sm text-[var(--text-secondary)]">{carry_label}</span>
                  <span className="w-full pt-1 text-[11px] tabular-nums text-[var(--accent-cyan)] sm:w-auto">
                    Est. yearly cash ≈{" "}
                    <span className="text-[var(--text-primary)]">
                      {usd_projection === "—" ? usd_projection : `$${usd_projection}`}
                    </span>
                  </span>
                  {row?.cacheHit === false ? (
                    <span className="w-full text-[10px] text-[var(--degraded)]">
                      Missing OKX cache — stale card.
                    </span>
                  ) : null}
                </div>
              </li>
            );
          })}
        </ul>
      ) : null}
    </div>
  );
}

interface FundingSortMode {
  id: "magnitude" | "zscore";
  label: string;
}

const SORT_MATRIX: FundingSortMode[] = [
  { id: "magnitude", label: "|Rate|" },
  { id: "zscore", label: "|Z-score|" },
];

export function FundingCommandCenterPage(): ReactElement {
  const query = useFundingCommandCenterQuery();
  const bundle = query.data;

  const [selected_pair, set_selected_pair] = useState<string>(() => "");

  const [chart_window_days, set_chart_window_days] = useState<FundingChartWindowDays>(30);

  const [sort_mode, set_sort_mode] = useState<FundingSortMode["id"]>("magnitude");

  const [hypothetical_notional_usd, set_hypothetical_notional_usd] = useState<string>("25000");

  const notional_numeric = Number(hypothetical_notional_usd.replaceAll(",", ""));
  const notional_live = Number.isFinite(notional_numeric) ? Math.max(0, notional_numeric) : 0;

  const rows_by_pair = useMemo(() => {
    const result = new Map<string, FundingCommandRow>();
    for (const row of bundle?.rows ?? []) {
      result.set(row.asset.toUpperCase(), row);
    }
    return result;
  }, [bundle?.rows]);

  const magnitude_sorted = useMemo(() => [...(bundle?.rows ?? [])], [bundle?.rows]);

  const ranked_rows = useMemo(() => {
    const cloned = [...(bundle?.rows ?? [])];

    if (sort_mode === "zscore") {
      cloned.sort(
        (left, right) =>
          Math.abs(right.zscore) - Math.abs(left.zscore) ||
          Math.abs(Number(right.fundingRate8h)) - Math.abs(Number(left.fundingRate8h)),
      );

      return cloned;
    }

    return magnitude_sorted;
  }, [bundle?.rows, magnitude_sorted, sort_mode]);

  const selected_snapshot = useMemo(() => {
    if (!bundle?.rows.length) {
      return null;
    }

    const normalized = selected_pair.trim().toUpperCase();
    if (normalized.length > 0) {
      const direct = ranked_rows.find((row) => row.asset.toUpperCase() === normalized);
      if (direct) {
        return direct;
      }
    }

    return ranked_rows[0] ?? null;
  }, [bundle?.rows.length, ranked_rows, selected_pair]);

  return (
    <div className="space-y-5">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <p className="text-[11px] font-semibold uppercase tracking-[0.2em] text-[var(--accent-cyan)]">
            Derivatives telemetry
          </p>
          <h1 className="mt-1 text-2xl font-semibold text-[var(--text-primary)]">
            Funding rate command center
          </h1>
          <p className="mt-2 max-w-3xl text-sm leading-relaxed text-[var(--text-secondary)]">
            OKX MCP Redis caches power the ladder: extremes feed the nonlinear funding gate, and sustained crowding tends to unwind.
            Use the ladder to prioritise convexity hunts, while the projections translate annualised APR into hypothetical dollar bleed.
          </p>
          {bundle?.generatedAt ? (
            <p className="mt-2 text-[11px] tabular-nums text-[var(--text-secondary)]">
              Snapshot UTC {bundle.generatedAt}
            </p>
          ) : null}
        </div>
        {query.isFetching ? (
          <span className="inline-flex items-center gap-2 rounded-md border border-[var(--border)] bg-[var(--bg-elevated)] px-3 py-1.5 text-[11px] text-[var(--text-secondary)]">
            <Spinner className="h-3 w-3" />
            Refreshing
          </span>
        ) : null}
      </header>

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1.05fr)_minmax(320px,0.95fr)]">
        <GlassPanel glow className="p-5">
          <div className="flex flex-wrap items-center justify-between gap-3 pb-4">
            <div>
              <h2 className="text-lg font-semibold text-[var(--text-primary)]">33-card ladder · sorted</h2>
              <p className="mt-1 text-xs text-[var(--text-secondary)]">
                Highest absolute per-interval prints surface first unless you pivot to squeeze candidates by |Z|.
              </p>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              {SORT_MATRIX.map((mode) => (
                <button
                  key={mode.id}
                  type="button"
                  onClick={() => set_sort_mode(mode.id)}
                  className={cn(
                    "rounded-md border px-3 py-1 text-[11px] font-semibold uppercase tracking-wide",
                    sort_mode === mode.id
                      ? "border-[var(--accent-cyan)] text-[var(--accent-cyan)]"
                      : "border-[var(--border)] text-[var(--text-secondary)]",
                  )}
                >
                  {mode.label}
                </button>
              ))}
            </div>
          </div>

          {query.isError ? (
            <p className="text-sm text-[var(--sell)]" role="status">
              Could not hydrate funding aggregates — confirm ATLAS exposes `/api/funding/command-center`.
            </p>
          ) : null}

          {query.isLoading ? <FundingGridSkeleton /> : null}

          {!query.isLoading && bundle?.rows?.length === 0 ? (
            <EmptyState
              title="Funding ladder empty"
              description="Redis rotation or OKX MCP caches returned nothing — verify polaris:rotation:active_33 and provider:okx_mcp:* keys."
            />
          ) : null}

          {!query.isLoading && ranked_rows.length > 0 ? (
            <div className="max-h-[min(70vh,720px)] overflow-auto rounded-xl border border-[var(--border)]">
              <table className="w-full min-w-[960px] text-left text-sm">
                <thead className="sticky top-0 z-10 bg-[rgba(2,8,21,0.92)] backdrop-blur">
                  <tr className="text-[11px] uppercase tracking-wide text-[var(--text-secondary)]">
                    <th className="px-4 py-2">Asset</th>
                    <th className="px-4 py-2">8h print</th>
                    <th className="px-4 py-2 text-right">Simple APR</th>
                    <th className="px-4 py-2 text-right">Z-score</th>
                    <th className="px-4 py-2">Flip cue</th>
                    <th className="px-4 py-2 text-right">Strength</th>
                    <th className="px-4 py-2 text-center">Cache</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-white/5 text-[var(--text-primary)]">
                  {ranked_rows.map((row) => (
                    <tr
                      key={row.asset}
                      className={cn(
                        "cursor-pointer transition-colors hover:bg-white/5",
                        selected_snapshot?.asset === row.asset && "bg-cyan-500/5",
                      )}
                      onClick={() => set_selected_pair(row.asset)}
                    >
                      <td className="px-4 py-2 font-semibold">{row.asset}</td>
                      <td className="px-4 py-2 font-mono text-xs tabular-nums text-[var(--text-secondary)]">
                        {format_micro_rate(row.fundingRate8h)}
                      </td>
                      <td className="px-4 py-2 text-right tabular-nums text-[var(--accent-cyan)]">
                        {format_pct_headline(row.annualizedSimplePct)}
                      </td>
                      <td className="px-4 py-2 text-right tabular-nums">
                        {row.zscore !== row.zscore ? "—" : row.zscore.toFixed(2)}
                      </td>
                      <td className="px-4 py-2">{flip_signal_badge(row.flipSignal)}</td>
                      <td className="px-4 py-2 text-right">{row.flipStrength}</td>
                      <td className="px-4 py-2 text-center">
                        {row.cacheHit ? <Badge variant="healthy">Warm</Badge> : <Badge variant="degraded">Cold</Badge>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : null}
        </GlassPanel>

        <GlassPanel glow className="space-y-4 p-5">
          <section>
            <h2 className="text-lg font-semibold text-[var(--text-primary)]">Historical path</h2>
            <p className="mt-1 text-xs text-[var(--text-secondary)]">
              OKX MCP stores ~ thirty days of prints; narrower windows sharpen squeeze timing.
            </p>
          </section>

          {selected_snapshot ? (
            <div className="space-y-3">
              <div className="flex flex-wrap items-center gap-3">
                <span className="text-sm font-semibold text-[var(--text-primary)]">
                  Selected {selected_snapshot.asset}
                </span>
                <Badge variant={selected_snapshot.flipSignal === "NEUTRAL" ? "shadow" : "live"}>
                  {selected_snapshot.flipSignal}
                </Badge>
              </div>
              <div className="flex flex-wrap gap-2">
                <button
                  type="button"
                  onClick={() => set_chart_window_days(7)}
                  className={cn(
                    "rounded-md px-3 py-1 text-xs font-semibold",
                    chart_window_days === 7
                      ? "bg-[rgba(56,232,236,0.15)] text-[var(--accent-cyan)]"
                      : "border border-[var(--border)] text-[var(--text-secondary)]",
                  )}
                >
                  Last 7d
                </button>
                <button
                  type="button"
                  onClick={() => set_chart_window_days(30)}
                  className={cn(
                    "rounded-md px-3 py-1 text-xs font-semibold",
                    chart_window_days === 30
                      ? "bg-[rgba(56,232,236,0.15)] text-[var(--accent-cyan)]"
                      : "border border-[var(--border)] text-[var(--text-secondary)]",
                  )}
                >
                  Last 30d
                </button>
              </div>
              <FundingHistoryChart
                points_all={selected_snapshot.history}
                window_days={chart_window_days}
              />
              <div className="rounded-lg border border-[var(--border)] bg-[rgba(15,23,42,0.45)] p-4 text-xs leading-snug text-[var(--text-secondary)]">
                {selected_snapshot.flipExplanation}
              </div>
              <dl className="grid gap-y-3 text-xs text-[var(--text-secondary)] sm:grid-cols-2">
                <div>
                  <dt className="text-[10px] uppercase tracking-wide">Long carry</dt>
                  <dd className="font-mono text-sm text-[var(--text-primary)]">
                    {format_pct_headline(selected_snapshot.effectiveLongCarryPct)}
                  </dd>
                </div>
                <div>
                  <dt className="text-[10px] uppercase tracking-wide">Short carry</dt>
                  <dd className="font-mono text-sm text-[var(--text-primary)]">
                    {format_pct_headline(selected_snapshot.effectiveShortCarryPct)}
                  </dd>
                </div>
              </dl>
            </div>
          ) : query.isLoading ? (
            <Skeleton className="h-52 w-full" />
          ) : (
            <EmptyState
              title="Pick an asset"
              description="Click a row in the ladder to load the 7d/30d path and flip narrative."
            />
          )}

          <section className="border-t border-white/5 pt-4">
            <div className="flex flex-wrap items-end justify-between gap-3 pb-4">
              <div>
                <h2 className="text-lg font-semibold text-[var(--text-primary)]">Position bleed model</h2>
                <p className="mt-1 text-xs text-[var(--text-secondary)]">
                  Map ladder slots plus a custom notional to estimate annual USD funding drag.
                </p>
              </div>
              <label className="flex flex-col text-[11px] font-semibold uppercase tracking-wide text-[var(--text-secondary)]">
                Notional USD
                <input
                  value={hypothetical_notional_usd}
                  onChange={(event) => set_hypothetical_notional_usd(event.target.value)}
                  className="mt-1 rounded-md border border-[var(--border)] bg-transparent px-2 py-1 text-[13px] text-[var(--text-primary)]"
                  inputMode="decimal"
                  type="number"
                  min={0}
                />
              </label>
            </div>

            <PositionCarryProjection rows_by_pair={rows_by_pair} notional_usd={notional_live} />
          </section>
        </GlassPanel>
      </div>
    </div>
  );
}

function FundingGridSkeleton(): ReactElement {
  const strip_ids = [
    "alpha",
    "bravo",
    "charlie",
    "delta",
    "echo",
    "foxtrot",
    "golf",
    "hotel",
    "india",
    "juliett",
    "kilo",
  ] as const;

  return (
    <div className="space-y-2">
      {strip_ids.map((strip_id) => (
        <Skeleton key={strip_id} className="h-11 w-full" />
      ))}
    </div>
  );
}
