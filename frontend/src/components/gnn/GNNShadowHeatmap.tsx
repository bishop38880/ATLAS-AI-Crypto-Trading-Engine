import type { ReactElement } from "react";

import {
  calculate_newest_computed_at_iso,
  GNN_SHADOW_POINTS_MAX,
  type GnnHeatmapColumn,
} from "../../lib/calculate_gnn_shadow_grid";
import { format_last_compact_from_iso } from "../../lib/format-relative-age";

export interface GNNShadowHeatmapProps {
  columns: readonly GnnHeatmapColumn[];
  selectedBase: string;
  onSelectAsset: (base: string) => void;
  onOpenDetail: (base: string) => void;
}

export function GNNShadowHeatmap(props: GNNShadowHeatmapProps): ReactElement {
  const { columns, selectedBase, onSelectAsset, onOpenDetail } = props;
  const latest_iso = calculate_newest_computed_at_iso(columns);
  const age_label = format_last_compact_from_iso(latest_iso);

  return (
    <section
      className="rounded-[var(--radius-lg)] border border-[var(--border)] bg-[var(--bg-surface)] p-[var(--space-4)]"
      aria-live="polite"
      aria-label="Shadow GNN scores heatmap"
    >
      <header className="mb-[var(--space-4)] flex flex-wrap items-end justify-between gap-2">
        <h2 className="font-[family-name:var(--font-display)] text-sm font-semibold text-[var(--text-primary)]">
          SHADOW GNN SCORES (0–{GNN_SHADOW_POINTS_MAX} pts)
        </h2>
        <p className="font-data text-xs text-[var(--text-secondary)]">
          Last inference: {age_label}
        </p>
      </header>

      <div className="overflow-x-auto pb-2 [-webkit-overflow-scrolling:touch]">
        <div
          className="grid gap-x-1 gap-y-2 pr-2"
          style={{
            gridTemplateColumns: `96px repeat(${columns.length}, minmax(40px, 1fr))`,
            minWidth: `${120 + columns.length * 44}px`,
          }}
        >
          <div className="font-data text-[10px] uppercase tracking-wide text-[var(--text-tertiary)]" aria-hidden />
          {columns.map((col) => (
            <div
              key={`h-${col.base}`}
              className="text-center font-data text-[10px] font-semibold uppercase text-[var(--text-secondary)]"
            >
              {col.base}
            </div>
          ))}

          <div className="font-data text-xs text-[var(--text-secondary)]">Score</div>
          {columns.map((col) => {
            const intensity = col.scoreIntensity01;
            const bg =
              col.scorePoints === null
                ? "rgba(255,255,255,0.04)"
                : `rgba(0, 230, 118, ${0.08 + intensity * 0.38})`;
            return (
              <button
                key={`s-${col.base}`}
                type="button"
                className={`rounded-[var(--radius-sm)] px-1 py-2 text-center font-data text-xs font-semibold transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent-cyan)] focus-visible:ring-offset-2 focus-visible:ring-offset-[var(--bg-surface)] ${
                  selectedBase === col.base ? "ring-1 ring-[var(--accent-cyan)]" : ""
                }`}
                style={{ backgroundColor: bg }}
                aria-label={
                  col.scorePoints === null
                    ? `Asset ${col.base}: shadow GNN score pending`
                    : `Asset ${col.base}: shadow GNN score ${col.scorePoints} points`
                }
                onClick={() => {
                  onSelectAsset(col.base);
                  onOpenDetail(col.base);
                }}
              >
                {col.scorePoints === null ? (
                  <span className="skeleton-shimmer-bg block min-h-[1rem] rounded-[var(--radius-sm)]" />
                ) : (
                  <span className="text-[var(--text-primary)]">{col.scorePoints}</span>
                )}
              </button>
            );
          })}

          <div className="font-data text-xs text-[var(--text-secondary)]">OBTI</div>
          {columns.map((col) => (
            <button
              key={`o-${col.base}`}
              type="button"
              className="rounded-[var(--radius-sm)] bg-[rgba(255,255,255,0.03)] px-1 py-2 text-center font-data text-sm text-[var(--text-primary)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent-cyan)] focus-visible:ring-offset-2 focus-visible:ring-offset-[var(--bg-surface)]"
              aria-label={col.obtiAria ?? `Asset ${col.base}: OBTI balanced`}
              onClick={() => {
                onSelectAsset(col.base);
                onOpenDetail(col.base);
              }}
            >
              {col.obtiVisual}
            </button>
          ))}

          <div className="font-data text-xs text-[var(--text-secondary)]">Cluster</div>
          {columns.map((col) => (
            <button
              key={`c-${col.base}`}
              type="button"
              className="rounded-[var(--radius-sm)] bg-[rgba(255,255,255,0.03)] px-1 py-2 text-center font-data text-sm text-[var(--text-primary)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent-cyan)] focus-visible:ring-offset-2 focus-visible:ring-offset-[var(--bg-surface)]"
              aria-label={col.clusterAria ?? `Asset ${col.base}: no wallet cluster anomaly`}
              onClick={() => {
                onSelectAsset(col.base);
                onOpenDetail(col.base);
              }}
            >
              {col.clusterVisual}
            </button>
          ))}
        </div>
      </div>
    </section>
  );
}
