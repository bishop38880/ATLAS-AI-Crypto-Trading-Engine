import { useQuery } from "@tanstack/react-query";

import { apiUrl } from "../lib/url";
import type {
  AgentZeroLifecycleWire,
  MemoryActivityEntryWire,
  RAGStatsWire,
  RetrievalEventWire,
  StorePreviewWire,
  VerifierStatsWire,
} from "../types/memory-api";

async function fetch_json_or_throw<T>(path: string): Promise<T> {
  const response = await fetch(apiUrl(path), {
    headers: { Accept: "application/json" },
  });
  if (!response.ok) {
    throw new Error(`memory_http_${response.status}`);
  }
  return (await response.json()) as T;
}

export function useRAGStatsQuery() {
  return useQuery({
    queryKey: ["memory", "stats"],
    queryFn: async (): Promise<RAGStatsWire> => fetch_json_or_throw<RAGStatsWire>("/api/memory/stats"),
  });
}

export function useAgentZeroQuery() {
  return useQuery({
    queryKey: ["memory", "agent-zero"],
    queryFn: async (): Promise<AgentZeroLifecycleWire> =>
      fetch_json_or_throw<AgentZeroLifecycleWire>("/api/memory/agent-zero"),
  });
}

export function useMemoryActivityQuery() {
  return useQuery({
    queryKey: ["memory", "activity"],
    queryFn: async (): Promise<MemoryActivityEntryWire[]> =>
      fetch_json_or_throw<MemoryActivityEntryWire[]>("/api/memory/activity"),
    refetchInterval: 30_000,
  });
}

export function useVerifierStatsQuery() {
  return useQuery({
    queryKey: ["memory", "verifier"],
    queryFn: async (): Promise<VerifierStatsWire> =>
      fetch_json_or_throw<VerifierStatsWire>("/api/memory/verifier"),
  });
}

export function useRetrievalsQuery() {
  return useQuery({
    queryKey: ["memory", "retrievals"],
    queryFn: async (): Promise<RetrievalEventWire[]> =>
      fetch_json_or_throw<RetrievalEventWire[]>("/api/memory/retrievals?limit=30"),
    refetchInterval: 15_000,
  });
}

export function useStorePreviewQuery() {
  return useQuery({
    queryKey: ["memory", "store-preview"],
    queryFn: async (): Promise<StorePreviewWire> =>
      fetch_json_or_throw<StorePreviewWire>("/api/memory/store-preview?limit=12"),
    refetchInterval: 60_000,
  });
}
