import type { ReactElement } from "react";
import { memo } from "react";

import { formatDecisionGlyph } from "../../lib/format-decision-glyph";
import type { SignalTimelineTick } from "../../lib/signal-timeline";
import { cn } from "../../lib/cn";

export interface SignalTimelineProps {
  ticks: readonly SignalTimelineTick[];
}

function lane_color(decision: string): string {
  if (decision.includes("Strong Buy")) {
    return "bg-[var(--strong-buy)]";
  }
  if (decision === "Buy") {
    return "bg-[var(--buy)]";
  }
  if (decision.includes("Strong Sell")) {
    return "bg-[var(--strong-sell)]";
  }
  if (decision === "Sell") {
    return "bg-[var(--sell)]";
  }
  if (decision === "No Position") {
    return "bg-[var(--no-position)]";
  }
  return "bg-[var(--hold)]";
}

function SignalTimelineInner({ ticks }: SignalTimelineProps): ReactElement {
  if (ticks.length === 0) {
    return (
      <section aria-live="polite" className="rounded-[var(--radius-md)] border border-[var(--border)] p-4">
        <p className="text-xs text-[var(--text-tertiary)]">
          No persisted cycles in the last 24 hours — timeline fills once PostgreSQL history arrives.
        </p>
      </section>
    );
  }

  return (
    <section aria-live="polite" className="space-y-2">
      <header className="flex items-center justify-between gap-2">
        <h2 className="text-[11px] font-black uppercase tracking-wide text-[var(--text-tertiary)]">
          Last 24h timeline
        </h2>
        <span className="font-mono text-[10px] tabular-nums text-[var(--text-tertiary)]">
          {ticks.length} cycles
        </span>
      </header>
      <div
        className="flex max-h-24 flex-wrap items-end gap-px overflow-x-auto rounded-[var(--radius-md)] border border-[var(--border)] bg-[var(--bg-surface)] p-2"
        role="list"
        aria-label="Signal timeline ticks coloured by decision"
      >
        {ticks.map((tick) => {
          const glyph = formatDecisionGlyph(tick.decision);
          const label = `${tick.asset} ${tick.decision} ${tick.totalScore}/220`;
          return (
            <div key={`${tick.id}-${tick.timestampIso}`} className="group relative flex flex-col items-center" role="listitem">
              <div
                className={cn("h-10 w-1 rounded-full opacity-90 transition-opacity group-hover:opacity-100", lane_color(tick.decision))}
                title={label}
                aria-label={label}
              />
              <span className="pointer-events-none absolute bottom-full mb-1 hidden whitespace-nowrap rounded bg-[var(--bg-overlay)] px-2 py-1 font-mono text-[10px] text-[var(--text-primary)] shadow-md group-hover:block">
                <span aria-hidden>{glyph}</span> {tick.asset} · {tick.totalScore}/220
              </span>
            </div>
          );
        })}
      </div>
    </section>
  );
}

export const SignalTimeline = memo(SignalTimelineInner);
