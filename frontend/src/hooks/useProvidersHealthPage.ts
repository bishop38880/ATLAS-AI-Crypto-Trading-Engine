import { useQuery } from "@tanstack/react-query";
import { useEffect } from "react";

import { map_provider_ws_payload } from "../lib/channel-mappers";
import { apiUrl } from "../lib/url";
import { wsManager } from "../lib/ws-manager";
import { useProviderStore, type ProviderHealth } from "../store/index";

export interface ProvidersHealthPageState {
  providers: ProviderHealth[];
  isLoading: boolean;
  isError: boolean;
  /** True during background REST refetch while an existing snapshot is shown. */
  isRefreshing: boolean;
}

/**
 * REST bootstrap + polling for {@link useProviderStore}. WebSocket updates (global bootstrap) merge in the same store.
 */
export function useProvidersHealthPage(): ProvidersHealthPageState {
  const providers = useProviderStore((s) => s.providers);
  const setProviders = useProviderStore((s) => s.setProviders);

  const query = useQuery({
    queryKey: ["providers", "health"],
    staleTime: 30_000,
    retry: 3,
    retryDelay: (attempt) => Math.min(500 * 2 ** attempt, 8_000),
    queryFn: async () => {
      const response = await fetch(apiUrl("/api/providers/health"), {
        headers: { Accept: "application/json" },
      });
      if (!response.ok) {
        throw new Error("providers_health_http_error");
      }
      const raw: unknown = await response.json();
      return map_provider_ws_payload(raw);
    },
    /** If `/ws/providers` is open but the store is still empty, keep polling REST until one path delivers rows. */
    refetchInterval: () => {
      const wsUp = wsManager.getState("providers") === "OPEN";
      const haveRows = useProviderStore.getState().providers.length > 0;
      if (wsUp && haveRows) {
        return false;
      }
      return 5_000;
    },
  });

  useEffect(() => {
    if (query.data !== undefined && query.data.length > 0) {
      setProviders(query.data);
    }
  }, [query.data, setProviders]);

  const isLoading = query.isPending && providers.length === 0;
  const isRefreshing =
    providers.length > 0 && query.isFetching && !query.isPending;
  return {
    providers,
    isLoading,
    isError: query.isError,
    isRefreshing,
  };
}
