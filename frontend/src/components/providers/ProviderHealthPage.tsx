import { useMemo, type ReactElement } from "react";

import { useProvidersHealthPage } from "../../hooks/useProvidersHealthPage";
import { cn } from "../../lib/cn";
import { useProviderStore } from "../../store/index";
import { EmptyState } from "../ui/EmptyState";
import { Skeleton } from "../ui/Skeleton";
import { Spinner } from "../ui/Spinner";
import { ProviderHealthGrid } from "./ProviderHealthGrid";
import { ProviderHealthSummary } from "./ProviderHealthSummary";
import { Tier1DegradedBanner } from "./Tier1DegradedBanner";

export function ProviderHealthPage(): ReactElement {
  const { providers, isLoading, isError, isRefreshing } = useProvidersHealthPage();
  const count = providers.length;

  const isTestingAll = useProviderStore((s) => s.isTestingAll);
  const isTesting = useProviderStore((s) => s.isTesting);
  const testAllProviders = useProviderStore((s) => s.testAllProviders);

  const anyTesting = useMemo(() => {
    return isTestingAll || Object.values(isTesting).some(Boolean);
  }, [isTestingAll, isTesting]);

  return (
    <div className="flex min-h-0 flex-col">
      <header className="mb-2 shrink-0">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0 flex-1">
            <h1 className="font-mono text-lg font-semibold tracking-tight text-[var(--text-primary)]">
              Provider health
            </h1>
          </div>
          <button
            type="button"
            disabled={anyTesting || count === 0}
            aria-busy={isTestingAll ? true : undefined}
            aria-disabled={anyTesting || count === 0 ? true : undefined}
            aria-label={anyTesting ? "Testing all providers…" : "Test all providers"}
            onClick={() => {
              void testAllProviders();
            }}
            className={cn(
              "inline-flex shrink-0 items-center gap-2 rounded-md border border-[var(--border)]",
              "bg-[var(--bg-elevated)] px-3 py-1.5 font-mono text-xs font-medium text-[var(--text-primary)]",
              "hover:bg-[var(--bg-overlay)] disabled:opacity-45",
              "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent-cyan)] focus-visible:ring-offset-2 focus-visible:ring-offset-[var(--bg-base)]",
            )}
          >
            {anyTesting ? <Spinner className="h-3 w-3 shrink-0" /> : null}
            Test All
          </button>
        </div>
        <p className="mt-1 max-w-3xl text-xs text-[var(--text-secondary)]">
          Circuit breakers, latency envelopes, and rate-limit usage for external intelligence feeds. Data
          refreshes on the WebSocket channel (~15s) with REST fallback every 5s when disconnected.
        </p>
      </header>

      {isError && count === 0 && (
        <p className="mb-3 text-xs text-[var(--sell)]" role="status">
          REST bootstrap failed — waiting for WebSocket or retry.
        </p>
      )}

      {isRefreshing && (
        <p className="mb-2 text-[11px] text-[var(--text-tertiary)] tabular-nums" aria-live="polite">
          Refreshing provider snapshot…
        </p>
      )}

      {isLoading && (
        <div className="mb-4 space-y-3" aria-busy="true">
          <Skeleton height={36} className="max-w-3xl" />
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            <Skeleton height={220} rounded={false} className="rounded-[var(--radius-md)]" />
            <Skeleton height={220} rounded={false} className="rounded-[var(--radius-md)]" />
            <Skeleton height={220} rounded={false} className="rounded-[var(--radius-md)]" />
          </div>
        </div>
      )}

      {!isLoading && count === 0 && (
        <EmptyState
          title="No provider snapshots yet"
          description="Connect to POLARIS API or wait for the providers channel to deliver its first envelope."
        />
      )}

      {count > 0 && (
        <>
          <Tier1DegradedBanner providers={providers} />
          <ProviderHealthSummary providers={providers} />
          <ProviderHealthGrid providers={providers} />
        </>
      )}
    </div>
  );
}
