import type { ReactElement } from "react";

import { format_last_compact_from_iso } from "../../lib/format-relative-age";
import type { RetrievalEventWire } from "../../types/memory-api";
import { Card } from "../ui/Card";
import { Skeleton } from "../ui/Skeleton";

export interface MemoryInfluencePanelProps {
  events: RetrievalEventWire[] | undefined;
  is_loading: boolean;
  is_error: boolean;
}

export function MemoryInfluencePanel(props: MemoryInfluencePanelProps): ReactElement {
  const { events, is_loading, is_error } = props;

  if (is_loading && events === undefined) {
    return (
      <Card title="Retrieval influence" accent="violet" subtitle="Per pipeline cycle">
        <div className="space-y-2" aria-busy="true">
          <Skeleton height={12} className="max-w-xl" />
          <Skeleton height={100} rounded={false} className="rounded-[var(--radius-sm)]" />
        </div>
      </Card>
    );
  }

  if (is_error && events === undefined) {
    return (
      <Card title="Retrieval influence" accent="violet">
        <p className="text-xs text-[var(--sell)]" role="status">
          Could not load retrieval telemetry.
        </p>
      </Card>
    );
  }

  const list = events ?? [];

  return (
    <Card
      title="Retrieval influence"
      accent="violet"
      subtitle="Vectors pulled into the live context pack (similarity + freshness decayed score)"
    >
      {list.length === 0 ? (
        <p className="text-xs text-[var(--text-secondary)]">
          No retrievals recorded yet. Run the autonomous / pipeline cycle with Qdrant available — events appear
          here with similarity and final scores per hit.
        </p>
      ) : (
        <ul className="max-h-[420px] space-y-4 overflow-y-auto pr-1 font-data text-[11px] leading-relaxed">
          {list.map((ev) => (
            <li
              key={ev.id}
              className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[var(--surface-elevated)]/40 p-3"
            >
              <div className="flex flex-wrap items-baseline justify-between gap-2">
                <span className="font-semibold text-[var(--text-primary)]">{ev.asset}</span>
                <span className="text-[var(--text-tertiary)]">{format_last_compact_from_iso(ev.timestamp)}</span>
              </div>
              <p className="mt-1 text-[var(--text-secondary)]">
                <span className="text-[var(--text-tertiary)]">Query ·</span> {ev.queryText}
              </p>
              <p className="mt-0.5 text-[var(--text-tertiary)]">
                Depth: {ev.retrievalDepth} · Hits: {ev.hitCount}
                {ev.error ? (
                  <span className="ml-2 text-[var(--sell)]">Error: {ev.error}</span>
                ) : null}
              </p>
              {ev.hits.length > 0 ? (
                <ul className="mt-2 space-y-2 border-t border-[var(--border)] pt-2">
                  {ev.hits.map((h) => (
                    <li key={`${ev.id}-${h.documentId}`} className="rounded-sm bg-[var(--surface)]/60 px-2 py-1.5">
                      <div className="flex flex-wrap gap-x-3 gap-y-0.5 text-[10px] text-[var(--text-tertiary)]">
                        <span className="text-[var(--text-primary)]">id {h.documentId.slice(0, 8)}…</span>
                        {h.signalDecision ? (
                          <span>
                            decision <span className="text-[var(--text-secondary)]">{h.signalDecision}</span>
                          </span>
                        ) : null}
                        <span>
                          sim <span className="tabular-nums text-[var(--accent-cyan)]">{h.similarityScore.toFixed(4)}</span>
                        </span>
                        <span>
                          final{" "}
                          <span className="tabular-nums text-[var(--accent-cyan)]">{h.finalScore.toFixed(4)}</span>
                        </span>
                      </div>
                      {h.preview ? (
                        <p className="mt-1 text-[11px] text-[var(--text-secondary)]">{h.preview}</p>
                      ) : null}
                      {h.userTags.length > 0 ? (
                        <p className="mt-1 text-[10px] text-[var(--hold)]">Tags: {h.userTags.join(", ")}</p>
                      ) : null}
                    </li>
                  ))}
                </ul>
              ) : null}
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}
