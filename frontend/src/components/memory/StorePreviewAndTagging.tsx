import type { FormEvent, ReactElement } from "react";
import { useState } from "react";

import { useQueryClient } from "@tanstack/react-query";

import { apiUrl } from "../../lib/url";
import { format_last_compact_from_iso } from "../../lib/format-relative-age";
import type { StorePreviewWire } from "../../types/memory-api";
import { Card } from "../ui/Card";
import { Skeleton } from "../ui/Skeleton";

export interface StorePreviewAndTaggingProps {
  preview: StorePreviewWire | undefined;
  is_loading: boolean;
  is_error: boolean;
}

export function StorePreviewAndTagging(props: StorePreviewAndTaggingProps): ReactElement {
  const { preview, is_loading, is_error } = props;
  const qc = useQueryClient();

  const [patternTitle, setPatternTitle] = useState("");
  const [patternBody, setPatternBody] = useState("");
  const [patternCategory, setPatternCategory] = useState("regime_misclassification");
  const [patternTags, setPatternTags] = useState("review");
  const [patternStatus, setPatternStatus] = useState<string | null>(null);

  const [tagSignalId, setTagSignalId] = useState("");
  const [tagList, setTagList] = useState("regime_misclassification, sol_review");
  const [tagNote, setTagNote] = useState("");
  const [tagStatus, setTagStatus] = useState<string | null>(null);

  async function submitPattern(e: FormEvent): Promise<void> {
    e.preventDefault();
    setPatternStatus(null);
    const tags = patternTags
      .split(",")
      .map((t) => t.trim())
      .filter(Boolean);
    const response = await fetch(apiUrl("/api/memory/patterns"), {
      method: "POST",
      headers: { Accept: "application/json", "Content-Type": "application/json" },
      body: JSON.stringify({
        title: patternTitle,
        content: patternBody,
        category: patternCategory,
        tags,
      }),
    });
    if (!response.ok) {
      setPatternStatus(`Failed: ${response.status}`);
      return;
    }
    setPatternStatus("Saved to pattern_memory (embedded).");
    setPatternTitle("");
    setPatternBody("");
    await qc.invalidateQueries({ queryKey: ["memory", "store-preview"] });
  }

  async function submitTags(e: FormEvent): Promise<void> {
    e.preventDefault();
    setTagStatus(null);
    const tags = tagList
      .split(",")
      .map((t) => t.trim())
      .filter(Boolean);
    const response = await fetch(apiUrl("/api/memory/signal-tags"), {
      method: "POST",
      headers: { Accept: "application/json", "Content-Type": "application/json" },
      body: JSON.stringify({
        signalId: tagSignalId,
        tags,
        note: tagNote || null,
      }),
    });
    if (response.status === 404) {
      setTagStatus("Signal id not found in signal_history.");
      return;
    }
    if (!response.ok) {
      setTagStatus(`Failed: ${response.status}`);
      return;
    }
    setTagStatus("Tags merged on signal row.");
    await qc.invalidateQueries({ queryKey: ["memory", "store-preview"] });
  }

  if (is_loading && preview === undefined) {
    return (
      <Card title="Store contents & manual memory" accent="amber">
        <Skeleton height={160} rounded={false} className="rounded-[var(--radius-sm)]" />
      </Card>
    );
  }

  if (is_error && preview === undefined) {
    return (
      <Card title="Store contents & manual memory" accent="amber">
        <p className="text-xs text-[var(--sell)]">Could not load Postgres store preview.</p>
      </Card>
    );
  }

  const data = preview ?? { signals: [], patterns: [] };

  return (
    <Card
      title="Store contents & manual memory"
      accent="amber"
      subtitle="Latest embedded rows in PostgreSQL (signals + curated patterns). Add lessons or tag a signal_id."
    >
      <div className="grid gap-6 lg:grid-cols-2">
        <div>
          <h3 className="mb-2 font-body text-[11px] font-semibold uppercase tracking-wide text-[var(--text-secondary)]">
            Recent signals
          </h3>
          <ul className="max-h-48 space-y-2 overflow-y-auto text-[11px]">
            {data.signals.length === 0 ? (
              <li className="text-[var(--text-tertiary)]">No rows yet.</li>
            ) : (
              data.signals.map((s) => (
                <li key={s.signalId} className="rounded-sm border border-[var(--border)] px-2 py-1.5">
                  <div className="flex flex-wrap justify-between gap-1">
                    <span className="font-mono text-[var(--text-primary)]">{s.signalId.slice(0, 10)}…</span>
                    <span className="text-[var(--text-tertiary)]">{format_last_compact_from_iso(s.createdAtIso)}</span>
                  </div>
                  <p className="text-[var(--text-secondary)]">
                    {s.asset} · {s.decision} · {s.score}
                  </p>
                  <p className="line-clamp-2 text-[var(--text-tertiary)]">{s.reasoningPreview}</p>
                  {s.userTags.length > 0 ? (
                    <p className="mt-0.5 text-[10px] text-[var(--hold)]">{s.userTags.join(", ")}</p>
                  ) : null}
                </li>
              ))
            )}
          </ul>
          <h3 className="mb-2 mt-4 font-body text-[11px] font-semibold uppercase tracking-wide text-[var(--text-secondary)]">
            Recent patterns
          </h3>
          <ul className="max-h-40 space-y-2 overflow-y-auto text-[11px]">
            {data.patterns.length === 0 ? (
              <li className="text-[var(--text-tertiary)]">No patterns yet.</li>
            ) : (
              data.patterns.map((p) => (
                <li key={p.id} className="rounded-sm border border-[var(--border)] px-2 py-1.5">
                  <p className="font-semibold text-[var(--text-primary)]">{p.title}</p>
                  <p className="text-[var(--text-tertiary)]">
                    {p.category} · {format_last_compact_from_iso(p.createdAtIso)}
                  </p>
                  <p className="line-clamp-2 text-[var(--text-secondary)]">{p.contentPreview}</p>
                </li>
              ))
            )}
          </ul>
        </div>
        <div className="space-y-4">
          <form onSubmit={(e) => void submitPattern(e)} className="space-y-2 rounded-[var(--radius-sm)] border border-[var(--border)] p-3">
            <p className="font-body text-[11px] font-semibold uppercase text-[var(--text-secondary)]">
              New curated pattern
            </p>
            <input
              className="w-full rounded-sm border border-[var(--border)] bg-[var(--surface)] px-2 py-1.5 text-xs text-[var(--text-primary)]"
              placeholder="Title (e.g. SOL regime misclassification)"
              value={patternTitle}
              onChange={(e) => setPatternTitle(e.target.value)}
              required
            />
            <input
              className="w-full rounded-sm border border-[var(--border)] bg-[var(--surface)] px-2 py-1.5 text-xs text-[var(--text-primary)]"
              placeholder="Category"
              value={patternCategory}
              onChange={(e) => setPatternCategory(e.target.value)}
            />
            <input
              className="w-full rounded-sm border border-[var(--border)] bg-[var(--surface)] px-2 py-1.5 text-xs text-[var(--text-primary)]"
              placeholder="Tags, comma-separated"
              value={patternTags}
              onChange={(e) => setPatternTags(e.target.value)}
            />
            <textarea
              className="min-h-[88px] w-full rounded-sm border border-[var(--border)] bg-[var(--surface)] px-2 py-1.5 text-xs text-[var(--text-primary)]"
              placeholder="Full lesson / flag description…"
              value={patternBody}
              onChange={(e) => setPatternBody(e.target.value)}
              required
            />
            <button
              type="submit"
              className="rounded-sm bg-[var(--accent-cyan)] px-3 py-1.5 text-xs font-medium text-black"
            >
              Embed pattern
            </button>
            {patternStatus ? <p className="text-[11px] text-[var(--text-secondary)]">{patternStatus}</p> : null}
          </form>

          <form onSubmit={(e) => void submitTags(e)} className="space-y-2 rounded-[var(--radius-sm)] border border-[var(--border)] p-3">
            <p className="font-body text-[11px] font-semibold uppercase text-[var(--text-secondary)]">
              Tag existing signal
            </p>
            <input
              className="w-full rounded-sm border border-[var(--border)] bg-[var(--surface)] px-2 py-1.5 font-mono text-xs text-[var(--text-primary)]"
              placeholder="signal_id (UUID)"
              value={tagSignalId}
              onChange={(e) => setTagSignalId(e.target.value)}
              required
            />
            <input
              className="w-full rounded-sm border border-[var(--border)] bg-[var(--surface)] px-2 py-1.5 text-xs text-[var(--text-primary)]"
              placeholder="Tags, comma-separated"
              value={tagList}
              onChange={(e) => setTagList(e.target.value)}
            />
            <textarea
              className="min-h-[56px] w-full rounded-sm border border-[var(--border)] bg-[var(--surface)] px-2 py-1.5 text-xs text-[var(--text-primary)]"
              placeholder="Optional note"
              value={tagNote}
              onChange={(e) => setTagNote(e.target.value)}
            />
            <button
              type="submit"
              className="rounded-sm border border-[var(--border)] bg-[var(--surface-elevated)] px-3 py-1.5 text-xs font-medium text-[var(--text-primary)]"
            >
              Merge tags into signal_history.metadata
            </button>
            {tagStatus ? <p className="text-[11px] text-[var(--text-secondary)]">{tagStatus}</p> : null}
          </form>
        </div>
      </div>
    </Card>
  );
}
