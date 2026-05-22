import { useEffect, useState, type ReactElement } from "react";

import {
  calculate_breaker_timeline,
  BREAKER_HALF_TO_CLOSED_MS,
  BREAKER_OPEN_TO_HALF_MS,
} from "../../lib/provider-health";
import type { ProviderHealth } from "../../store/index";

export interface CircuitBreakerTimelineProps {
  state: ProviderHealth["state"];
  lastFailureTs: string | null;
}

export function CircuitBreakerTimeline({
  state,
  lastFailureTs,
}: CircuitBreakerTimelineProps): ReactElement | null {
  const [nowMs, setNowMs] = useState(() => Date.now());

  useEffect(() => {
    const id = window.setInterval(() => {
      setNowMs(Date.now());
    }, 1000);
    return () => window.clearInterval(id);
  }, []);

  const model = calculate_breaker_timeline(state, lastFailureTs, nowMs);
  if (model === null || !model.show) {
    return null;
  }

  const leftLabel =
    model.phase === "open_to_half" ? "○ OPEN" : "◑ HALF-OPEN";
  const rightLabel =
    model.phase === "open_to_half" ? "◑ HALF-OPEN" : "● CLOSED";
  const windowMs =
    model.phase === "open_to_half" ? BREAKER_OPEN_TO_HALF_MS : BREAKER_HALF_TO_CLOSED_MS;

  return (
    <div className="mt-3 rounded-[var(--radius-sm)] border border-[var(--border)] bg-[var(--bg-elevated)]/80 px-3 py-2">
      <div className="mb-1.5 flex items-center justify-between text-[10px] font-mono uppercase tracking-wide text-[var(--text-tertiary)]">
        <span>{leftLabel}</span>
        <span>{rightLabel}</span>
      </div>
      <div
        className="h-1.5 w-full overflow-hidden rounded-full bg-[var(--bg-base)]"
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={windowMs}
        aria-valuenow={Math.round(model.progress01 * windowMs)}
        aria-label="Circuit breaker recovery progress"
      >
        <div
          className="h-full rounded-full bg-[var(--warning)] transition-[width] duration-700 ease-linear"
          style={{ width: `${Math.min(100, model.progress01 * 100)}%` }}
        />
      </div>
      <div className="mt-1.5 flex flex-wrap justify-between gap-x-3 gap-y-0.5 font-mono text-[10px] text-[var(--text-secondary)]">
        <span>
          Opened {Math.max(1, Math.round(model.openedAgoSec))}s ago <span aria-hidden="true">→</span>
        </span>
        <span className="tabular-nums">{Math.max(0, Math.ceil(model.remainingSec))}s remaining</span>
      </div>
    </div>
  );
}
