import { useState, type ReactElement } from "react";

import type { SourceCitationPayload } from "../../types/omnibox-api";

export interface SourceCitationCardProps {
  citation: SourceCitationPayload;
}

export function SourceCitationCard(props: SourceCitationCardProps): ReactElement {
  const { citation } = props;
  const [open, set_open] = useState(false);
  const anchor_id = `omnibox-source-${citation.id}`;

  const meta_parts: string[] = [];
  if (typeof citation.score === "number" && typeof citation.scoreMax === "number") {
    meta_parts.push(`Score: ${citation.score}/${citation.scoreMax}`);
  }
  if (citation.decision) {
    meta_parts.push(`Decision: ${citation.decision}`);
  }
  if (citation.outcomePct && citation.outcomeLabel) {
    meta_parts.push(`Outcome: ${citation.outcomePct} (${citation.outcomeLabel})`);
  }

  return (
    <section
      id={anchor_id}
      className="scroll-mt-24 rounded-md border border-[var(--border)] bg-[var(--bg-elevated)]"
    >
      <button
        type="button"
        aria-expanded={open}
        aria-label={`${open ? "Collapse" : "Expand"} source ${citation.id}`}
        onClick={() => set_open((previous) => !previous)}
        className="flex w-full items-start justify-between gap-2 px-3 py-2 text-left text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent-cyan)]"
      >
        <span>
          <span className="font-data text-[var(--accent-cyan)]">[{citation.id}]</span>{" "}
          <span className="font-semibold text-[var(--text-primary)]">
            {citation.title}
          </span>
        </span>
        <span className="text-[var(--text-tertiary)]" aria-hidden>
          {open ? "▼" : "▶"}
        </span>
      </button>
      {open ? (
        <div className="border-t border-[var(--border)] px-3 py-2 text-xs text-[var(--text-secondary)]">
          {meta_parts.length > 0 ? (
            <p className="font-data text-[var(--text-primary)]">{meta_parts.join(" · ")}</p>
          ) : null}
          {citation.snippet ? (
            <blockquote className="mt-2 border-l-2 border-[var(--border-accent)] pl-2 italic">
              &ldquo;{citation.snippet}&rdquo;
            </blockquote>
          ) : null}
          {citation.url ? (
            <a
              href={citation.url}
              className="mt-2 inline-block text-[var(--accent-cyan)] underline-offset-2 hover:underline focus-visible:rounded-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent-cyan)]"
              target="_blank"
              rel="noreferrer noopener"
            >
              Open link
            </a>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}
