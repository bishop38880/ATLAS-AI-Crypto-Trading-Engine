import type { ReactElement, ReactNode } from "react";

import { calculate_service_health_level } from "../../lib/calculate-memory-health-level";
import { format_memory_integer, format_memory_percent } from "../../lib/format-memory-dashboard";
import { format_last_compact_from_iso } from "../../lib/format-relative-age";
import { cn } from "../../lib/cn";
import type { RAGStatsWire } from "../../types/memory-api";
import { Card } from "../ui/Card";
import { Skeleton } from "../ui/Skeleton";
import { StatusDot } from "../ui/StatusDot";

function format_sync_glyph(sync_state: string): { glyph: string; tone: "ok" | "warn" | "bad" } {
  const u = sync_state.trim().toUpperCase();
  if (u === "IN_SYNC" || u === "SYNCED" || u === "READY") {
    return { glyph: "●", tone: "ok" };
  }
  if (u.includes("LAG") || u.includes("SYNCING")) {
    return { glyph: "◐", tone: "warn" };
  }
  return { glyph: "○", tone: "bad" };
}

function tone_class(tone: "ok" | "warn" | "bad"): string {
  if (tone === "ok") {
    return "text-[var(--success)]";
  }
  if (tone === "warn") {
    return "text-[var(--hold)]";
  }
  return "text-[var(--text-tertiary)]";
}

export interface RAGMemoryStatsProps {
  data: RAGStatsWire | undefined;
  is_loading: boolean;
  is_error: boolean;
}

export function RAGMemoryStats(props: RAGMemoryStatsProps): ReactElement {
  const { data, is_loading, is_error } = props;
  const last_label = format_last_compact_from_iso(data?.lastUpdatedIso);

  if (is_loading && data === undefined) {
    return (
      <Card title="RAG Memory Layer" accent="cyan">
        <div className="space-y-3" aria-busy="true">
          <Skeleton height={14} className="max-w-md" />
          <Skeleton height={180} rounded={false} className="rounded-[var(--radius-sm)]" />
        </div>
      </Card>
    );
  }

  if (is_error && data === undefined) {
    return (
      <Card title="RAG Memory Layer" accent="cyan">
        <p className="text-xs text-[var(--sell)]" role="status">
          Could not load RAG statistics from the API.
        </p>
      </Card>
    );
  }

  if (data === undefined) {
    return (
      <Card title="RAG Memory Layer" accent="cyan">
        <p className="text-xs text-[var(--text-secondary)]">No statistics available.</p>
      </Card>
    );
  }

  const sync = format_sync_glyph(data.lancedb.syncState);
  const q_health = calculate_service_health_level(data.qdrantHealth);
  const l_health = calculate_service_health_level(data.lancedbHealth);

  return (
    <Card title="RAG Memory Layer" accent="cyan" subtitle={`Last updated: ${last_label}`}    >
      <p className="sr-only" aria-live="polite">
        RAG statistics last updated {last_label}
      </p>
      <div className="space-y-4 font-data text-xs leading-relaxed">
        <div className="grid gap-1 border-b border-[var(--border)] pb-3">
          <StatRow
            label="Postgres scope"
            value={<span className="text-[var(--text-primary)]">{data.collectionName}</span>}
          />
          <StatRow label="Signal rows" value={format_memory_integer(data.totalDocuments)} />
          <StatRow
            label="Archived (soft)"
            value={`${format_memory_integer(data.archivedCount)} (${format_memory_percent(data.archivedPercent, 1)})`}
          />
          <StatRow label="Active documents" value={format_memory_integer(data.activeDocuments)} />
          {data.qdrantCollectionName !== undefined && data.qdrantCollectionName !== "" ? (
            <StatRow
              label="Qdrant collection"
              value={<span className="font-mono text-[var(--text-primary)]">{data.qdrantCollectionName}</span>}
            />
          ) : null}
          {data.qdrantPointsCount != null ? (
            <StatRow
              label="Qdrant points (vectors)"
              value={format_memory_integer(data.qdrantPointsCount)}
            />
          ) : (
            <StatRow
              label="Qdrant points"
              value={<span className="text-[var(--text-tertiary)]">Unavailable (degraded or not connected)</span>}
            />
          )}
          {data.patternMemoryRows != null ? (
            <StatRow label="Pattern memory rows" value={format_memory_integer(data.patternMemoryRows)} />
          ) : null}
        </div>

        <div className="grid gap-1 border-b border-[var(--border)] pb-3">
          <p className="font-body text-[11px] font-semibold uppercase tracking-wide text-[var(--text-secondary)]">
            LanceDB hot standby
          </p>
          <StatRow
            label="Sync state"
            value={
              <span className="inline-flex items-center gap-2 tabular-nums">
                <span className={cn("font-semibold", tone_class(sync.tone))} aria-hidden>
                  {sync.glyph}
                </span>
                <span>{data.lancedb.syncState}</span>
                <span className="text-[var(--text-tertiary)]">
                  (lag: {format_memory_integer(data.lancedb.lagSeconds)}s)
                </span>
              </span>
            }
          />
          <StatRow label="Vectors mirrored" value={format_memory_integer(data.lancedb.vectorCount)} />
          <StatRow
            label="Last sync"
            value={format_last_compact_from_iso(data.lancedb.lastSyncIso)}
          />
        </div>

        <div className="grid gap-1 border-b border-[var(--border)] pb-3">
          <StatRow
            label="Embedding model"
            value={
              <span>
                {data.embeddingModel}{" "}
                <span className="text-[var(--text-tertiary)]">(interim; local-model migration planned)</span>
              </span>
            }
          />
          <StatRow label="Embedding dims" value={format_memory_integer(data.embeddingDims)} />
        </div>

        <div className="flex flex-wrap items-center gap-x-6 gap-y-2">
          <span className="inline-flex items-center gap-2">
            <span className="text-[var(--text-secondary)]">Qdrant health</span>
            <StatusDot level={q_health} label={data.qdrantHealth} />
          </span>
          <span className="inline-flex items-center gap-2">
            <span className="text-[var(--text-secondary)]">LanceDB health</span>
            <StatusDot level={l_health} label={data.lancedbHealth} />
          </span>
        </div>
      </div>
    </Card>
  );
}

function StatRow(props: { label: string; value: ReactNode }): ReactElement {
  return (
    <div className="grid grid-cols-[minmax(0,160px)_1fr] gap-3 sm:grid-cols-[minmax(0,200px)_1fr]">
      <span className="text-[var(--text-secondary)]">{props.label}</span>
      <span className="min-w-0 tabular-nums text-[var(--text-primary)]">{props.value}</span>
    </div>
  );
}
