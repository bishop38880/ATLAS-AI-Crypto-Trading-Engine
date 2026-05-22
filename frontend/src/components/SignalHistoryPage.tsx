import { useQuery } from "@tanstack/react-query";
import type { ReactElement } from "react";

import { coerce_signal_feed_rows, map_signal_feed_payload } from "../lib/signal-feed-mapper";
import { map_signal_history_payload } from "../lib/signal-history-mapper";
import { calculate_signal_timeline_ticks } from "../lib/signal-timeline";
import { apiUrl } from "../lib/url";
import { SignalFeedTable } from "./signals/SignalFeedTable";
import { SignalTimeline } from "./signals/SignalTimeline";
import { EmptyState } from "./ui/EmptyState";
import { Skeleton } from "./ui/Skeleton";

async function fetch_signal_feed_json(): Promise<unknown> {
  const response = await fetch(apiUrl("/api/signals/feed"), {
    headers: { Accept: "application/json" },
  });
  if (!response.ok) {
    throw new Error(`signal_feed_failed_${response.status}`);
  }
  return response.json() as Promise<unknown>;
}

async function fetch_signal_history_timeline_json(): Promise<unknown> {
  const response = await fetch(apiUrl("/api/signals/history?limit=500"), {
    headers: { Accept: "application/json" },
  });
  if (!response.ok) {
    throw new Error(`signal_history_failed_${response.status}`);
  }
  return response.json() as Promise<unknown>;
}

/**
 * Signal deep-dive landing page: Redis-fed `/feed` table plus PostgreSQL timeline ribbon.
 */
export function SignalHistoryPage(): ReactElement {
  const feed_query = useQuery({
    queryKey: ["signals", "feed"],
    queryFn: fetch_signal_feed_json,
    staleTime: 5_000,
    refetchInterval: 5_000,
    select: map_signal_feed_payload,
  });

  const history_query = useQuery({
    queryKey: ["signals", "history", "timeline"],
    queryFn: fetch_signal_history_timeline_json,
    staleTime: 15_000,
    refetchInterval: 15_000,
    select: (raw: unknown) => calculate_signal_timeline_ticks(map_signal_history_payload(raw)),
  });

  const loading = feed_query.isLoading || history_query.isLoading;
  const fetching = feed_query.isFetching || history_query.isFetching;

  if (loading) {
    return (
      <section className="space-y-4" aria-busy="true" aria-label="Signals loading">
        <Skeleton className="block h-10 w-64" height={40} />
        <Skeleton className="block h-48 w-full" height={192} />
      </section>
    );
  }

  if (feed_query.error !== null) {
    return (
      <EmptyState
        title="Could not load signal feed"
        description="The `/api/signals/feed` endpoint is unreachable. Confirm POLARIS API availability."
      />
    );
  }

  const rows = coerce_signal_feed_rows(feed_query.data);

  return (
    <div className="space-y-8" aria-live="polite">
      <header className="flex flex-wrap items-baseline justify-between gap-2">
        <div>
          <h1 className="text-lg font-black text-[var(--text-primary)]">Signals</h1>
          <p className="mt-1 max-w-3xl text-xs text-[var(--text-secondary)]">
            Live Redis snapshots across the rotation universe with PostgreSQL audit trail below. OBTI summaries arrive flattened from PROMETHEUS — never recomputed in-browser.
          </p>
        </div>
        {fetching ? <span className="text-xs text-[var(--text-tertiary)]">Refreshing…</span> : null}
      </header>

      {rows.length === 0 ? (
        <EmptyState
          title="No polaris snapshots yet"
          description="Once Redis publishes `polaris:signals:{asset}` payloads the grid fills automatically."
        />
      ) : (
        <SignalFeedTable rows={rows} />
      )}

      <SignalTimeline ticks={history_query.data ?? []} />
      {history_query.error !== null ? (
        <p className="text-xs text-[var(--warning)]">Timeline history unavailable — feed remains usable.</p>
      ) : null}
    </div>
  );
}
