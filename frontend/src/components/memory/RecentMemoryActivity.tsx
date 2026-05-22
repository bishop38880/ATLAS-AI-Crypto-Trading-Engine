import type { ReactElement } from "react";
import { memo } from "react";

import {
  format_memory_decimal,
  format_memory_integer,
  format_memory_result_aria,
} from "../../lib/format-memory-dashboard";
import { format_last_compact_from_iso } from "../../lib/format-relative-age";
import type { MemoryActivityEntryWire } from "../../types/memory-api";
import { Card } from "../ui/Card";
import { EmptyState } from "../ui/EmptyState";
import { Skeleton } from "../ui/Skeleton";

export interface RecentMemoryActivityProps {
  entries: MemoryActivityEntryWire[] | undefined;
  is_loading: boolean;
  is_error: boolean;
}

function format_latency(latency_ms: number): string {
  if (latency_ms >= 1000) {
    return `${format_memory_decimal(latency_ms / 1000, 1)}s`;
  }
  return `${latency_ms}ms`;
}

const ActivityRow = memo(function ActivityRow(props: { row: MemoryActivityEntryWire }): ReactElement {
  const { row } = props;
  const when = format_last_compact_from_iso(row.timestamp);
  const aria = format_memory_result_aria(row.result);

  return (
    <tr className="border-b border-[var(--border)] last:border-b-0">
      <td className="py-1.5 pr-2 tabular-nums text-[var(--text-secondary)]">{when}</td>
      <td className="py-1.5 pr-2 font-medium text-[var(--text-primary)]">{row.operation.toUpperCase()}</td>
      <td className="py-1.5 pr-2 text-[var(--accent-cyan)]">{row.asset}</td>
      <td className="py-1.5 pr-2 text-right tabular-nums">{format_memory_integer(row.documentCount)}</td>
      <td className="py-1.5 pr-2 text-right tabular-nums text-[var(--text-secondary)]">
        {format_latency(row.latencyMs)}
      </td>
      <td className="py-1.5 text-right text-[var(--success)]" title={aria}>
        <span aria-label={aria}>{row.result}</span>
      </td>
    </tr>
  );
});

export function RecentMemoryActivity(props: RecentMemoryActivityProps): ReactElement {
  const { entries, is_loading, is_error } = props;
  const rows = entries ?? [];

  if (is_loading && entries === undefined) {
    return (
      <Card title="Recent memory activity" subtitle="Last 20 RAG read/write events" accent="teal">
        <div className="space-y-2" aria-busy="true">
          <Skeleton height={140} rounded={false} className="rounded-[var(--radius-sm)]" />
        </div>
      </Card>
    );
  }

  if (is_error && entries === undefined) {
    return (
      <Card title="Recent memory activity" subtitle="Last 20 RAG read/write events" accent="teal">
        <p className="text-xs text-[var(--sell)]" role="status">
          Could not load activity feed.
        </p>
      </Card>
    );
  }

  return (
    <Card title="Recent memory activity" subtitle="Last 20 RAG read/write events" accent="teal">
      {rows.length === 0 ? (
        <EmptyState
          title="No recent events"
          description="The API returned an empty activity list — connect POLARIS or wait for RAG traffic."
          className="py-8"
        />
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[520px] border-collapse text-left font-data text-xs">
            <thead>
              <tr className="border-b border-[var(--border)] text-[var(--text-tertiary)]">
                <th scope="col" className="pb-2 pr-2 font-medium">
                  Time
                </th>
                <th scope="col" className="pb-2 pr-2 font-medium">
                  Operation
                </th>
                <th scope="col" className="pb-2 pr-2 font-medium">
                  Asset
                </th>
                <th scope="col" className="pb-2 pr-2 text-right font-medium">
                  Docs
                </th>
                <th scope="col" className="pb-2 pr-2 text-right font-medium">
                  Latency
                </th>
                <th scope="col" className="pb-2 text-right font-medium">
                  Result
                </th>
              </tr>
            </thead>
            <tbody
              aria-live="polite"
              aria-atomic="false"
              aria-relevant="additions"
            >
              {rows.map((row) => (
                <ActivityRow key={row.id} row={row} />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}
