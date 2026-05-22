import { memo, type ReactElement } from "react";

import { list_tier1_not_closed } from "../../lib/provider-health";
import type { ProviderHealth } from "../../store/index";

export interface Tier1DegradedBannerProps {
  providers: ProviderHealth[];
}

export const Tier1DegradedBanner = memo(function Tier1DegradedBanner({
  providers,
}: Tier1DegradedBannerProps): ReactElement | null {
  const bad = list_tier1_not_closed(providers);
  if (bad.length === 0) {
    return null;
  }

  const label = bad.join(", ");
  const verb = bad.length === 1 ? "is" : "are";

  return (
    <div
      role="alert"
      aria-live="assertive"
      className="mb-4 rounded-[var(--radius-md)] border border-[var(--sell)]/50 bg-[rgba(239,83,80,0.1)] px-4 py-3 text-sm text-[var(--text-primary)]"
    >
      <p className="font-mono font-semibold text-[var(--sell)]">
        ⚠ TIER 1 PROVIDER DEGRADED: {label} {verb} not CLOSED.
      </p>
      <p className="mt-1 text-xs text-[var(--text-secondary)]">
        Live intelligence may be stale. Paper-trade entry suppression follows backend policy until Tier 1 stabilizes.
      </p>
    </div>
  );
});
