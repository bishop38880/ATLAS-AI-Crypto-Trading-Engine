import type { ReactElement } from "react";

import {
  calculate_escore_band_track_class,
  calculate_escore_bar_fill_percent,
} from "../../lib/calculate-memory-escore-band";
import {
  format_memory_decimal,
  format_memory_integer,
  format_memory_percent,
} from "../../lib/format-memory-dashboard";
import type { AgentZeroLifecycleWire } from "../../types/memory-api";
import { Card } from "../ui/Card";
import { Skeleton } from "../ui/Skeleton";

export interface AgentZeroPanelProps {
  data: AgentZeroLifecycleWire | undefined;
  is_loading: boolean;
  is_error: boolean;
}

export function AgentZeroPanel(props: AgentZeroPanelProps): ReactElement {
  const { data, is_loading, is_error } = props;

  if (is_loading && data === undefined) {
    return (
      <Card title="Agent Zero — Memory Lifecycle Manager" accent="violet">
        <div className="space-y-3" aria-busy="true">
          <Skeleton height={120} rounded={false} className="rounded-[var(--radius-sm)]" />
          <Skeleton height={160} rounded={false} className="rounded-[var(--radius-sm)]" />
        </div>
      </Card>
    );
  }

  if (is_error && data === undefined) {
    return (
      <Card title="Agent Zero — Memory Lifecycle Manager" accent="violet">
        <p className="text-xs text-[var(--sell)]" role="status">
          Could not load Agent Zero lifecycle data.
        </p>
      </Card>
    );
  }

  if (data === undefined) {
    return (
      <Card title="Agent Zero — Memory Lifecycle Manager" accent="violet">
        <p className="text-xs text-[var(--text-secondary)]">No lifecycle data.</p>
      </Card>
    );
  }

  const results = data.lastRunResults;
  const scored =
    results?.documentsScored !== undefined ? format_memory_integer(results.documentsScored) : "—";
  const archived =
    results?.archived !== undefined ? format_memory_integer(results.archived) : "—";
  const retained =
    results?.retained !== undefined ? format_memory_integer(results.retained) : "—";
  const errors = results?.errors !== undefined ? format_memory_integer(results.errors) : "—";

  let archived_pct = "—";
  if (
    results?.documentsScored !== undefined &&
    results.documentsScored > 0 &&
    results?.archived !== undefined
  ) {
    archived_pct = format_memory_percent((results.archived / results.documentsScored) * 100, 1);
  }

  let retained_pct = "—";
  if (
    results?.documentsScored !== undefined &&
    results.documentsScored > 0 &&
    results?.retained !== undefined
  ) {
    retained_pct = format_memory_percent((results.retained / results.documentsScored) * 100, 1);
  }

  const max_band_count =
    data.escoreDistribution.length > 0
      ? Math.max(...data.escoreDistribution.map((b) => b.count))
      : 0;

  const health_upper = data.collectionHealthLabel.trim().toUpperCase();
  const health_tone =
    health_upper.includes("HEALTHY") || health_upper.includes("STRONG")
      ? "text-[var(--success)]"
      : health_upper.includes("AWAIT") || health_upper.includes("DATA")
        ? "text-[var(--hold)]"
        : "text-[var(--text-primary)]";

  return (
    <Card title="Agent Zero — Memory Lifecycle Manager" accent="violet">
      <div className="space-y-4 font-data text-xs">
        <div className="grid gap-1 border-b border-[var(--border)] pb-3 sm:grid-cols-2">
          <div>
            <span className="text-[var(--text-secondary)]">Last nightly run</span>
            <div className="mt-0.5 text-[var(--text-primary)]">{data.lastRunRelative}</div>
          </div>
          <div>
            <span className="text-[var(--text-secondary)]">Next scheduled</span>
            <div className="mt-0.5 text-[var(--text-primary)]">{data.nextRunRelative}</div>
          </div>
        </div>

        <div>
          <p className="mb-2 font-body text-[11px] font-semibold uppercase tracking-wide text-[var(--text-secondary)]">
            Last run results
          </p>
          <ul className="grid gap-1">
            <li className="flex justify-between gap-3">
              <span className="text-[var(--text-secondary)]">Documents scored</span>
              <span className="tabular-nums text-[var(--text-primary)]">{scored}</span>
            </li>
            <li className="flex justify-between gap-3">
              <span className="text-[var(--text-secondary)]">Archived (&lt; {format_memory_decimal(data.threshold, 2)})</span>
              <span className="tabular-nums">
                {archived}
                {archived_pct !== "—" ? ` (${archived_pct})` : ""}
              </span>
            </li>
            <li className="flex justify-between gap-3">
              <span className="text-[var(--text-secondary)]">Retained (≥ {format_memory_decimal(data.threshold, 2)})</span>
              <span className="tabular-nums">
                {retained}
                {retained_pct !== "—" ? ` (${retained_pct})` : ""}
              </span>
            </li>
            <li className="flex justify-between gap-3">
              <span className="text-[var(--text-secondary)]">Errors</span>
              <span className="tabular-nums text-[var(--sell)]">{errors}</span>
            </li>
          </ul>
        </div>

        <div>
          <p className="mb-2 font-body text-[11px] font-semibold uppercase tracking-wide text-[var(--text-secondary)]">
            Escore distribution
          </p>
          {data.escoreDistribution.length === 0 ? (
            <p className="rounded-[var(--radius-sm)] border border-dashed border-[var(--border)] px-3 py-6 text-center text-[var(--text-tertiary)]">
              No distribution yet — waiting for nightly scoring runs.
            </p>
          ) : (
            <ul className="space-y-1.5">
              {data.escoreDistribution.map((band) => {
                const fill = calculate_escore_bar_fill_percent(band.count, max_band_count);
                const track_class = calculate_escore_band_track_class(band.status);
                return (
                  <li
                    key={band.label}
                    className="grid grid-cols-[minmax(0,88px)_1fr_auto_auto] items-center gap-2 sm:grid-cols-[minmax(0,120px)_1fr_auto_auto]"
                  >
                    <span className="truncate text-[var(--text-tertiary)]">{band.label}</span>
                    <div
                      className="h-2 min-w-0 rounded-sm bg-[var(--bg-elevated)]"
                      title={`${band.status}: ${format_memory_integer(band.count)} docs`}
                    >
                      <div
                        className={`h-2 rounded-sm ${track_class}`}
                        style={{ width: `${fill}%` }}
                      />
                    </div>
                    <span className="w-14 text-right tabular-nums text-[var(--text-primary)] sm:w-16">
                      {format_memory_integer(band.count)}
                    </span>
                    <span className="w-16 text-right tabular-nums text-[var(--text-tertiary)] sm:w-20">
                      {format_memory_percent(band.percent, 1)}
                    </span>
                  </li>
                );
              })}
            </ul>
          )}
        </div>

        <div className="flex flex-wrap items-baseline gap-x-6 gap-y-1 border-t border-[var(--border)] pt-3">
          <span>
            <span className="text-[var(--text-secondary)]">Avg Escore </span>
            <span className="tabular-nums text-[var(--text-primary)]">
              {format_memory_decimal(data.avgEscore, 2)}
            </span>
          </span>
          <span className={`inline-flex items-center gap-1 font-body ${health_tone}`}>
            <span aria-hidden>■</span>
            <span>{data.collectionHealthLabel}</span>
          </span>
          <span>
            <span className="text-[var(--text-secondary)]">Threshold </span>
            <span className="tabular-nums">{format_memory_decimal(data.threshold, 2)}</span>
          </span>
        </div>
      </div>
    </Card>
  );
}
