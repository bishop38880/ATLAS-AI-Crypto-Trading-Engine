import type { ReactElement } from "react";

const SUGGESTED_QUERIES: readonly string[] = [
  'What was the last time BTC had this funding rate Z-score?',
  "Compare current SOL setup to historical bull runs",
  "What does extreme order-book toxicity (OBTI) mean for SOL right now?",
  "Explain how confluence scoring works",
  "Why is INJ being held back from a position?",
];

export interface SuggestedQueriesProps {
  on_select: (text: string) => void;
}

export function SuggestedQueries(props: SuggestedQueriesProps): ReactElement {
  return (
    <div className="rounded-lg border border-dashed border-[var(--border)] bg-[var(--bg-surface)] px-4 py-3">
      <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-[var(--text-tertiary)]">
        Try asking
      </h3>
      <ul className="space-y-2 text-sm text-[var(--text-secondary)]">
        {SUGGESTED_QUERIES.map((query) => (
          <li key={query}>
            <button
              type="button"
              onClick={() => props.on_select(query)}
              className="w-full rounded-md px-2 py-1 text-left hover:bg-[var(--bg-overlay)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent-cyan)]"
            >
              <span className="text-[var(--accent-teal)]">●</span>{" "}
              <span className="text-[var(--text-primary)]">&ldquo;{query}&rdquo;</span>
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
