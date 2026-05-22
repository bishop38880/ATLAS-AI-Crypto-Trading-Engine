import { memo, type ReactElement } from "react";

import {
  calculate_tier_health_counts,
  calculate_total_requests_per_min,
} from "../../lib/provider-health";
import type { ProviderHealth } from "../../store/index";

export interface ProviderHealthSummaryProps {
  providers: ProviderHealth[];
}

function list_degraded_names(providers: ProviderHealth[]): string[] {
  const names: string[] = [];
  for (const p of providers) {
    if (p.state !== "CLOSED") {
      names.push(p.name);
    }
  }
  return names.sort((a, b) => a.localeCompare(b));
}

const pillClass =
  "inline-flex items-center gap-1.5 rounded-full border border-[var(--border)] bg-[var(--bg-elevated)] px-3 py-1 font-mono text-[11px] text-[var(--text-primary)]";

export const ProviderHealthSummary = memo(function ProviderHealthSummary({
  providers,
}: ProviderHealthSummaryProps): ReactElement {
  const counts = calculate_tier_health_counts(providers);
  const degraded = list_degraded_names(providers);
  const totalRpm = calculate_total_requests_per_min(providers);

  return (
    <div
      aria-live="polite"
      aria-atomic="true"
      className="mb-4 flex flex-wrap items-center gap-2"
    >
      <span className={pillClass}>
        <span className="text-[var(--text-secondary)]">Tier 1</span>
        <span className="tabular-nums text-[var(--accent-teal)]">
          {counts.tier1Closed}/{counts.tier1Total}
        </span>
        <span className="text-[var(--text-tertiary)]">Healthy ▲</span>
      </span>
      <span className={pillClass}>
        <span className="text-[var(--text-secondary)]">Tier 2</span>
        <span className="tabular-nums text-[var(--accent-teal)]">
          {counts.tier2Closed}/{counts.tier2Total}
        </span>
        <span className="text-[var(--text-tertiary)]">Healthy ▲</span>
      </span>
      <span className={pillClass}>
        <span className="text-[var(--text-secondary)]">Degraded</span>
        <span className="tabular-nums text-[var(--warning)]">{degraded.length}</span>
        {degraded.length > 0 && (
          <span className="max-w-[min(56vw,22rem)] truncate text-[var(--text-secondary)]">
            : {degraded.join(", ")}
          </span>
        )}
      </span>
      <span className={pillClass}>
        <span className="text-[var(--text-secondary)]">Total req/min</span>
        <span className="tabular-nums">{Math.round(totalRpm)}</span>
      </span>
    </div>
  );
});
