import { useAgentChannel } from "./useAgentChannel";
import { usePricesChannel } from "./usePricesChannel";
import { useProviderChannel } from "./useProviderChannel";
import { useScoresChannel } from "./useScoresChannel";
import { useSystemChannel } from "./useSystemChannel";

/** Mount once under {@link AppShell} so routed pages share warm Zustand streams. */
export function usePolarisRealtimeBootstrap(): void {
  useAgentChannel();
  useScoresChannel();
  usePricesChannel();
  useProviderChannel();
  useSystemChannel();
}
