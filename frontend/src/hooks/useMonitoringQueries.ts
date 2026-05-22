import { useQuery } from "@tanstack/react-query";

import { apiUrl } from "../lib/url";
import type {
  AgentZeroScheduleData,
  AlertEntry,
  KeyMetricsData,
  PipelineLatencyData,
  StartupSequenceData,
  TestFloorData,
} from "../types/monitoring";

async function fetch_monitoring_json<T>(path: string): Promise<T> {
  const response = await fetch(apiUrl(path), {
    headers: { Accept: "application/json" },
  });
  if (!response.ok) {
    throw new Error(`monitoring_http_error:${path}:${response.status}`);
  }
  return (await response.json()) as T;
}

export function useMonitoringQueries() {
  const metrics = useQuery({
    queryKey: ["monitoring", "metrics"],
    queryFn: () => fetch_monitoring_json<KeyMetricsData>("/api/monitoring/metrics"),
    refetchInterval: 10_000,
    staleTime: 5_000,
  });

  const startup = useQuery({
    queryKey: ["monitoring", "startup"],
    queryFn: () => fetch_monitoring_json<StartupSequenceData>("/api/monitoring/startup"),
    refetchInterval: 30_000,
    staleTime: 15_000,
  });

  const latency = useQuery({
    queryKey: ["monitoring", "latency"],
    queryFn: () => fetch_monitoring_json<PipelineLatencyData>("/api/monitoring/latency"),
    refetchInterval: 5_000,
    staleTime: 3_000,
  });

  const agentZero = useQuery({
    queryKey: ["monitoring", "agent-zero"],
    queryFn: () => fetch_monitoring_json<AgentZeroScheduleData>("/api/monitoring/agent-zero"),
    refetchInterval: 30_000,
    staleTime: 15_000,
  });

  const alerts = useQuery({
    queryKey: ["monitoring", "alerts"],
    queryFn: () => fetch_monitoring_json<AlertEntry[]>("/api/monitoring/alerts"),
    refetchInterval: 10_000,
    staleTime: 5_000,
  });

  const testFloor = useQuery({
    queryKey: ["monitoring", "test-floor"],
    queryFn: () => fetch_monitoring_json<TestFloorData>("/api/test-floor"),
    refetchInterval: 30_000,
    staleTime: 15_000,
  });

  const allQueries = [metrics, startup, latency, agentZero, alerts, testFloor];
  const isLoading = allQueries.some((query) => query.isPending);
  const isError = allQueries.some((query) => query.isError);
  const isRefreshing = allQueries.some((query) => query.isFetching && !query.isPending);

  return {
    metrics,
    startup,
    latency,
    agentZero,
    alerts,
    testFloor,
    isLoading,
    isError,
    isRefreshing,
  };
}
