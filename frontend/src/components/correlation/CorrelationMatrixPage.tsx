import { useMemo, useState } from "react";
import type { ReactElement } from "react";

import { useCorrelationMatrixQuery } from "../../hooks/useCorrelationMatrixQuery";
import { useDashboardPositionSlotsQuery } from "../../hooks/useDashboardQueries";
import type { PositionCorrelationRiskWire } from "../../lib/correlation-matrix-types";
import { cn } from "../../lib/cn";
import { calculate_dashboard_position_slots_from_payload } from "../../lib/dashboard-positions";
import { derive_base_asset_from_pair } from "../../lib/dashboard-symbol";
import { GlassPanel } from "../ui/GlassPanel";
import {
  calculate_correlation_axis_class,
  calculate_correlation_cell_background,
} from "./calculate_correlation_heatmap_style";

type WindowMode = "14" | "30";

function correlation_pair_key(base_a: string, base_b: string): string {
  const left = base_a.toUpperCase();
  const right = base_b.toUpperCase();
  const [lo, hi] = left < right ? [left, right] : [right, left];
  return `${lo}|${hi}`;
}

function RiskStrip(props: { readonly risk: PositionCorrelationRiskWire; readonly window_label: string }): ReactElement {
  const { risk, window_label } = props;
  const tone =
    risk.level === "critical"
      ? "border-[rgba(248,113,113,0.55)] bg-[rgba(248,113,113,0.1)] text-[var(--danger)]"
      : risk.level === "warn"
        ? "border-[rgba(255,171,64,0.45)] bg-[rgba(255,171,64,0.08)] text-[var(--warning)]"
        : "border-[var(--border)] bg-[var(--bg-elevated)] text-[var(--text-secondary)]";

  return (
    <div className={cn("rounded-[var(--radius-md)] border px-4 py-3 text-xs leading-snug", tone)} role="status">
      <p className="font-[family-name:var(--font-display)] text-[11px] font-bold uppercase tracking-[0.16em]">
        Position overlap · {window_label}
      </p>
      <p className="mt-1.5">{risk.message}</p>
      {risk.monitoredPositions.length > 0 ? (
        <p className="mt-2 font-mono text-[10px] text-[var(--text-tertiary)]">
          Monitored slots: {risk.monitoredPositions.join(", ")}
        </p>
      ) : null}
    </div>
  );
}

export function CorrelationMatrixPage(): ReactElement {
  const [window_mode, set_window_mode] = useState<WindowMode>("30");

  const position_query = useDashboardPositionSlotsQuery();
  const slots = position_query.data ?? calculate_dashboard_position_slots_from_payload([]);

  const positions_csv = useMemo(() => {
    const bases = slots
      .map((slot) => (slot.asset !== null ? derive_base_asset_from_pair(slot.asset) : null))
      .filter((base): base is string => base !== null && base.trim().length > 0);
    const unique_sorted = Array.from(new Set(bases.map((b) => b.toUpperCase()))).sort((a, b) =>
      a.localeCompare(b),
    );
    return unique_sorted.join(",");
  }, [slots]);

  const matrix_query = useCorrelationMatrixQuery(positions_csv);
  const payload = matrix_query.data;

  const active_matrix = window_mode === "14" ? payload?.matrix14d : payload?.matrix30d;
  const active_risk = window_mode === "14" ? payload?.positionRisk14d : payload?.positionRisk30d;

  const extreme_keys = useMemo(() => {
    const bucket = new Set<string>();
    for (const pair of payload?.extremePairs30d ?? []) {
      bucket.add(correlation_pair_key(pair.baseA, pair.baseB));
    }
    return bucket;
  }, [payload]);

  const cluster_index_by_base = useMemo(() => {
    const map = new Map<string, number>();
    for (let idx = 0; idx < (payload?.clusters30d.length ?? 0); idx += 1) {
      const cluster = payload?.clusters30d[idx];
      if (cluster === undefined) {
        continue;
      }
      for (const base of cluster.bases) {
        map.set(base.toUpperCase(), idx);
      }
    }
    return map;
  }, [payload]);

  const monitored = useMemo(() => {
    const set = new Set<string>();
    for (const token of active_risk?.monitoredPositions ?? []) {
      set.add(token.toUpperCase());
    }
    return set;
  }, [active_risk]);

  return (
    <div className="mx-auto flex w-full max-w-[1680px] flex-col gap-6">
      <header className="space-y-2">
        <h1 className="text-2xl font-semibold text-[var(--text-primary)]">Correlation Matrix</h1>
        <p className="max-w-3xl text-sm text-[var(--text-secondary)]">
          Rolling Pearson correlations on aligned daily log-returns (Binance USDⓈ-M). Toggle 14-day versus 30-day
          windows — dense red tiles (&gt;0.92) mean your names move together; stacks of correlated majors behave like
          one bet. Clusters and extreme pairs below use the 30-day geometry for stability.
        </p>
      </header>

      <div className="flex flex-wrap items-center gap-3">
        <div className="inline-flex rounded-[var(--radius-sm)] border border-[var(--border)] bg-[var(--bg-elevated)] p-0.5">
          <button
            type="button"
            className={cn(
              "rounded-[var(--radius-sm)] px-3 py-1.5 text-xs font-semibold transition-colors",
              window_mode === "14"
                ? "bg-[var(--accent-cyan)] text-black"
                : "text-[var(--text-secondary)] hover:text-[var(--text-primary)]",
            )}
            onClick={() => set_window_mode("14")}
          >
            14-day ρ
          </button>
          <button
            type="button"
            className={cn(
              "rounded-[var(--radius-sm)] px-3 py-1.5 text-xs font-semibold transition-colors",
              window_mode === "30"
                ? "bg-[var(--accent-cyan)] text-black"
                : "text-[var(--text-secondary)] hover:text-[var(--text-primary)]",
            )}
            onClick={() => set_window_mode("30")}
          >
            30-day ρ
          </button>
        </div>
        {matrix_query.isFetching ? (
          <span className="text-[11px] text-[var(--text-tertiary)]">Refreshing…</span>
        ) : null}
        {payload ? (
          <span className="text-[11px] tabular-nums text-[var(--text-tertiary)]">
            Snapshot {new Date(payload.generatedAt).toLocaleString()} · cache TTL {payload.cacheTtlSeconds}s
          </span>
        ) : null}
      </div>

      {matrix_query.isError ? (
        <GlassPanel className="border border-[var(--danger)] bg-[rgba(248,113,113,0.08)] p-4 text-sm text-[var(--danger)]">
          Unable to load correlation matrix — confirm the API is running and Binance futures data is reachable.
        </GlassPanel>
      ) : null}

      {active_risk ? (
        <RiskStrip risk={active_risk} window_label={window_mode === "14" ? "14-day matrix" : "30-day matrix"} />
      ) : null}

      {payload !== undefined && Object.keys(payload.fetchErrors).length > 0 ? (
        <GlassPanel className="border border-[var(--warning)] bg-[rgba(255,171,64,0.06)] p-4 text-xs text-[var(--warning)]">
          <p className="font-semibold uppercase tracking-wide">Missing histories</p>
          <p className="mt-1 text-[var(--text-secondary)]">
            {Object.entries(payload.fetchErrors)
              .map(([base, reason]) => `${base}: ${reason}`)
              .join(" · ")}
          </p>
        </GlassPanel>
      ) : null}

      <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_280px]">
        <GlassPanel className="overflow-hidden p-3 md:p-4">
          {!payload || !active_matrix ? (
            <p className="text-sm text-[var(--text-tertiary)]">Loading correlation lattice…</p>
          ) : (
            <div className="overflow-auto [-webkit-overflow-scrolling:touch]">
              <table className="w-max min-w-full border-separate border-spacing-px">
                <thead>
                  <tr>
                    <th className="sticky left-0 z-20 bg-[var(--bg-base)] px-1 py-1 text-left text-[10px] font-semibold uppercase tracking-wide text-[var(--text-tertiary)]">
                      Asset
                    </th>
                    {payload.bases.map((col_base) => (
                      <th
                        key={`col-${col_base}`}
                        className={cn(
                          "sticky top-0 z-10 min-w-[34px] bg-[var(--bg-base)] px-0.5 py-1 text-center align-bottom",
                          monitored.has(col_base.toUpperCase())
                            ? "text-[var(--accent-cyan)]"
                            : "text-[var(--text-secondary)]",
                        )}
                      >
                        <span className="inline-block max-w-[52px] rotate-[-58deg] whitespace-nowrap font-mono text-[9px] font-bold uppercase leading-none">
                          {col_base}
                        </span>
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {payload.bases.map((row_base, row_idx) => {
                    const row_upper = row_base.toUpperCase();
                    const cluster_idx = cluster_index_by_base.get(row_upper);
                    const accent_hue = cluster_idx === undefined ? null : (cluster_idx * 53) % 360;
                    return (
                      <tr key={`row-${row_base}`}>
                        <th
                          className={cn(
                            "sticky left-0 z-10 bg-[var(--bg-base)] py-0.5 pr-2 text-right align-middle",
                            calculate_correlation_axis_class(monitored.has(row_upper)),
                          )}
                          style={{
                            borderLeftWidth: accent_hue === null ? 0 : 3,
                            borderLeftStyle: accent_hue === null ? undefined : "solid",
                            borderLeftColor:
                              accent_hue === null ? undefined : `hsl(${accent_hue.toFixed(0)}, 68%, 58%)`,
                          }}
                        >
                          {row_base}
                        </th>
                        {payload.bases.map((col_base, col_idx) => {
                          const col_upper = col_base.toUpperCase();
                          const rho = active_matrix[row_idx]?.[col_idx] ?? null;
                          const is_diagonal = row_idx === col_idx;
                          const key = correlation_pair_key(row_base, col_base);
                          const is_extreme = !is_diagonal && extreme_keys.has(key);
                          const bg = calculate_correlation_cell_background(rho, { is_diagonal, is_extreme });
                          const overlap_ring =
                            monitored.has(row_upper) &&
                            monitored.has(col_upper) &&
                            !is_diagonal &&
                            rho !== null &&
                            rho >= 0.92;
                          const outline =
                            is_extreme && !is_diagonal
                              ? "ring-1 ring-[rgba(248,113,113,0.85)] ring-inset"
                              : overlap_ring
                                ? "ring-1 ring-[var(--accent-cyan)] ring-inset"
                                : "";
                          return (
                            <td
                              key={`cell-${row_base}-${col_base}`}
                              className={cn("h-8 w-9 text-center align-middle", outline)}
                              style={{ backgroundColor: bg }}
                              title={
                                is_diagonal ? "Diagonal — ρ = 1.00" : `ρ ${rho === null ? "n/a" : rho.toFixed(3)}`
                              }
                            >
                              <span className="sr-only">
                                {row_base} versus {col_base}{" "}
                                {rho === null ? "undefined correlation" : rho.toFixed(3)}
                              </span>
                              {!is_diagonal && rho !== null ? (
                                <span className="pointer-events-none font-mono text-[9px] font-semibold text-[var(--text-primary)] drop-shadow-sm">
                                  {rho.toFixed(2)}
                                </span>
                              ) : null}
                            </td>
                          );
                        })}
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </GlassPanel>

        <div className="flex flex-col gap-4">
          <GlassPanel className="space-y-2 p-4 text-xs text-[var(--text-secondary)]">
            <p className="font-[family-name:var(--font-display)] text-[11px] font-bold uppercase tracking-[0.14em] text-[var(--text-primary)]">
              Legend
            </p>
            <ul className="list-disc space-y-1 pl-4 leading-snug">
              <li>Cyan underlines track PROMETHEUS ladder slots with live symbols.</li>
              <li>Orange → red tiles climb toward ρ ≥ 0.85 / 0.92 on the active window.</li>
              <li>Thin red rings mark ρ ≥ 0.92 on the 30-day window (even while viewing 14d).</li>
              <li>Left-axis colour stripe buckets assets into the same 30-day correlation component.</li>
            </ul>
          </GlassPanel>

          <GlassPanel className="space-y-2 p-4">
            <p className="font-[family-name:var(--font-display)] text-[11px] font-bold uppercase tracking-[0.14em] text-[var(--text-primary)]">
              Danger clusters (30d)
            </p>
            {!payload ? (
              <p className="text-xs text-[var(--text-tertiary)]">Waiting for data…</p>
            ) : payload.clusters30d.length === 0 ? (
              <p className="text-xs text-[var(--text-secondary)]">No |ρ| ≥ 0.85 components detected.</p>
            ) : (
              <ul className="space-y-2 text-xs text-[var(--text-secondary)]">
                {payload.clusters30d.map((cluster, idx) => (
                  <li
                    key={`cluster-${idx}-${cluster.bases[0] ?? "x"}`}
                    className="rounded-[var(--radius-sm)] border border-[rgba(248,113,113,0.35)] bg-[rgba(248,113,113,0.06)] px-2 py-2"
                  >
                    <p className="font-semibold text-[var(--danger)]">
                      {cluster.size} names · max |ρ| {cluster.maxAbsCorrelation.toFixed(2)}
                    </p>
                    <p className="mt-1 font-mono text-[10px] text-[var(--text-primary)]">{cluster.bases.join(", ")}</p>
                  </li>
                ))}
              </ul>
            )}
          </GlassPanel>

          <GlassPanel className="space-y-2 p-4">
            <p className="font-[family-name:var(--font-display)] text-[11px] font-bold uppercase tracking-[0.14em] text-[var(--text-primary)]">
              Extreme pairs (ρ ≥ 0.92, 30d)
            </p>
            {!payload ? (
              <p className="text-xs text-[var(--text-tertiary)]">Waiting for data…</p>
            ) : payload.extremePairs30d.length === 0 ? (
              <p className="text-xs text-[var(--text-secondary)]">No ultra-tight pairwise links right now.</p>
            ) : (
              <ul className="max-h-56 space-y-1 overflow-auto text-[11px] font-mono text-[var(--text-secondary)] [scrollbar-width:thin]">
                {payload.extremePairs30d.map((pair) => (
                  <li key={`${pair.baseA}-${pair.baseB}`} className="flex justify-between gap-2">
                    <span>
                      {pair.baseA} ↔ {pair.baseB}
                    </span>
                    <span className="shrink-0 text-[var(--danger)]">{pair.correlation.toFixed(3)}</span>
                  </li>
                ))}
              </ul>
            )}
          </GlassPanel>
        </div>
      </div>
    </div>
  );
}
