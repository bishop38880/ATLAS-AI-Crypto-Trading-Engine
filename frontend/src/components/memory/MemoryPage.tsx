import type { ReactElement } from "react";
import { useMemo } from "react";

import {
  useAgentZeroQuery,
  useMemoryActivityQuery,
  useRAGStatsQuery,
  useRetrievalsQuery,
  useStorePreviewQuery,
  useVerifierStatsQuery,
} from "../../hooks/useMemoryDashboard";
import { AgentZeroPanel } from "./AgentZeroPanel";
import { MemoryInfluencePanel } from "./MemoryInfluencePanel";
import { RAGMemoryStats } from "./RAGMemoryStats";
import { RecentMemoryActivity } from "./RecentMemoryActivity";
import { StorePreviewAndTagging } from "./StorePreviewAndTagging";
import { VerifierStats } from "./VerifierStats";

export function MemoryPage(): ReactElement {
  const stats_q = useRAGStatsQuery();
  const agent_q = useAgentZeroQuery();
  const activity_q = useMemoryActivityQuery();
  const verifier_q = useVerifierStatsQuery();
  const retrievals_q = useRetrievalsQuery();
  const store_q = useStorePreviewQuery();

  const activity_trimmed = useMemo(() => {
    const list = activity_q.data;
    if (list === undefined) {
      return undefined;
    }
    return list.slice(0, 20);
  }, [activity_q.data]);

  return (
    <div className="flex min-h-0 flex-col gap-6">
      <header className="shrink-0">
        <h1 className="font-mono text-lg font-semibold tracking-tight text-[var(--text-primary)]">Memory / RAG</h1>
        <p className="mt-1 max-w-3xl text-xs text-[var(--text-secondary)]">
          Postgres pgvector rows for signals and patterns, Qdrant collection size for pipeline retrieval, live retrieval
          telemetry (similarity + decayed scores), and tools to embed curated lessons or tag a specific signal_id.
        </p>
      </header>

      <div className="grid gap-6 xl:grid-cols-2">
        <RAGMemoryStats
          data={stats_q.data}
          is_loading={stats_q.isPending}
          is_error={stats_q.isError}
        />
        <MemoryInfluencePanel
          events={retrievals_q.data}
          is_loading={retrievals_q.isPending}
          is_error={retrievals_q.isError}
        />
        <StorePreviewAndTagging
          preview={store_q.data}
          is_loading={store_q.isPending}
          is_error={store_q.isError}
        />
        <AgentZeroPanel
          data={agent_q.data}
          is_loading={agent_q.isPending}
          is_error={agent_q.isError}
        />
        <RecentMemoryActivity
          entries={activity_trimmed}
          is_loading={activity_q.isPending}
          is_error={activity_q.isError}
        />
        <VerifierStats
          data={verifier_q.data}
          is_loading={verifier_q.isPending}
          is_error={verifier_q.isError}
        />
      </div>
    </div>
  );
}
