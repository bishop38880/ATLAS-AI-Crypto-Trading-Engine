import { create } from "zustand";

import {
  parse_provider_test_result,
  parse_provider_test_result_list,
  type ProviderTestResult,
} from "../lib/provider-test-result";
import { apiUrl } from "../lib/url";

// ─── Types ───────────────────────────────────────────────

export interface AgentStatus {
  name: string;
  category: "TECHNICAL" | "DERIVATIVES" | "ONCHAIN" | "SENTIMENT" | "RISK" | "CORRELATION";
  status: "HEALTHY" | "DEGRADED" | "TIMEOUT" | "OFFLINE";
  lastScore: number;
  maxPoints: number;
  lastPingMs: number;
  veto: boolean;
  lastUpdated: string;
  /** Best-effort verdict copy from Redis (`AgentStatusRedis.explanation`). */
  explanation?: string;
  direction?: string;
}

export type ObtiSummaryLevel = "low" | "moderate" | "extreme";

export interface ScoresPayload {
  asset: string;
  totalScore: number;
  normalizedScore: number;
  decision: SignalDecision;
  confidence: number;
  categoryScores: {
    derivatives: number;
    onchain: number;
    technical: number;
    sentiment: number;
    marketContext: number;
  };
  passesGate: boolean;
  /** Conviction gate threshold for this snapshot (integer ladder points). */
  gateThreshold: number;
  /** When false or null and gate failed, UI shows “R1 LLM skipped”. */
  r1LlmInvoked: boolean | null;
  llmModelLabel: string;
  llmTierLabel: string;
  llmLatencySeconds: number | null;
  /** True when the execution path reports a veto / hard block on this snapshot. */
  vetoActive: boolean;
  /** Flattened OBTI summary from PROMETHEUS; frontend never derives OBTI client-side. */
  obtiSummary: ObtiSummaryLevel | null;
  obtiSide: string | null;
  marlRevision: number | null;
  cycleNumber: number | null;
  cycleTimestamp: string;
  cycleTs: string;
  price: string;
  fundingRate: string;
  openInterest: string;
  /** One-paragraph entry rationale from signal.reasoning_summary (cached synthesis). */
  reasoningSummary: string;
}

export type SignalDecision =
  | "Strong Buy"
  | "Buy"
  | "Hold"
  | "Sell"
  | "Strong Sell"
  | "No Position";

export interface PricePayload {
  symbol: string;
  price: string;
  change24h: string;
  volume24h: string;
  fundingRate: string;
  openInterest: string;
  lastUpdated: string;
}

export interface SignalHistoryEntry {
  id: string;
  asset: string;
  timestamp: string;
  totalScore: number;
  decision: SignalDecision;
  conviction: number;
  passesGate: boolean;
}

export type { ProviderTestResult };

export interface ProviderHealth {
  name: string;
  tier: number;
  trustRank: number;
  state: "CLOSED" | "DEGRADED" | "OPEN" | "HALF_OPEN";
  healthScore: number;
  failureRate: number;
  slowCallRate: number;
  avgLatencyMs: number;
  p50Ms: number;
  p95Ms: number;
  p99Ms: number;
  requestsPerMin: number;
  rateLimitMax: number;
  lastFetchMs: number;
  cacheStatus: string;
  windowSize: number;
  windowFailures: number;
  windowSlowCalls: number;
  lastFailureTs: string | null;
}

export interface SystemHealth {
  overallStatus: "HEALTHY" | "DEGRADED" | "HALTED";
  currentRegime: "BULL" | "BEAR" | "RANGING" | "UNKNOWN";
  regimeConfidence: number;
  activeCycle: boolean;
  nextCycleSeconds: number;
  openPositions: number;
  portfolioPnl24h: string;
  /** Best-effort pipeline stats when backend sends them on `/ws/system`. */
  cycleCount?: number;
  btcPrice?: string;
  btcChange24h?: string;
  /** True when `POST /api/system/start-autonomous-engine` has started the RAG analysis loop. */
  autonomousEngineActive?: boolean;
  /** Compact base (e.g. BTC) for the pair whose signal drove the regime readout. */
  regimeContextAsset?: string;
  /** Primary signal timeframe (e.g. 30m, 4h). */
  regimeContextTimeframe?: string;
  /** ISO timestamp of the cached signal used for regime (UTC). */
  regimeContextAsOf?: string;
  /** HMM bars in the current state from regime agent sub_signals. */
  regimeContextDurationBars?: number;
  /** Human-readable runner-up from HMM probabilities. */
  regimeContextRunnerUp?: string;
  /** Short excerpt from regime agent explanation. */
  regimeContextExplanation?: string;
  /** Plain-language note on when classification would change. */
  regimeContextTransitionHint?: string;
  /** Raw HMM label before dashboard bucket coercion (bull / bear / volatile). */
  hmmRegimeNative?: "bull" | "bear" | "volatile";
}

interface AgentStore {
  agents: AgentStatus[];
  isConnected: boolean;
  lastUpdated: Date | null;
  setAgents: (agents: AgentStatus[]) => void;
  setConnected: (value: boolean) => void;
}

interface ScoresStore {
  scoresByAsset: Map<string, ScoresPayload>;
  isConnected: boolean;
  lastUpdated: Date | null;
  setScores: (asset: string, scores: ScoresPayload) => void;
  setConnected: (value: boolean) => void;
}

interface PricesStore {
  prices: Map<string, PricePayload>;
  isConnected: boolean;
  lastUpdated: Date | null;
  updatePrice: (symbol: string, payload: PricePayload) => void;
  setConnected: (value: boolean) => void;
}

interface ProviderStore {
  providers: ProviderHealth[];
  isConnected: boolean;
  lastUpdated: Date | null;
  setProviders: (providers: ProviderHealth[]) => void;
  setConnected: (value: boolean) => void;
  isTesting: Record<string, boolean>;
  isTestingAll: boolean;
  lastTestResults: Record<string, ProviderTestResult | undefined>;
  testProvider: (name: string) => Promise<void>;
  testAllProviders: () => Promise<void>;
}

function synth_error_row(name: string, message: string): ProviderTestResult {
  const now = new Date().toISOString();
  return {
    provider: name,
    status: "error",
    latency_ms: 0,
    error: message.slice(0, 200),
    circuit_state: "CLOSED",
    tested_at: now,
  };
}

interface SystemStore {
  health: SystemHealth | null;
  setHealth: (next: SystemHealth) => void;
}

export const useAgentStore = create<AgentStore>((set) => ({
  agents: [],
  isConnected: false,
  lastUpdated: null,
  setAgents: (agents) => set({ agents, lastUpdated: new Date() }),
  setConnected: (value) => set({ isConnected: value }),
}));

export const useScoresStore = create<ScoresStore>((set) => ({
  scoresByAsset: new Map(),
  isConnected: false,
  lastUpdated: null,
  setScores: (asset, scores) =>
    set((state) => {
      const next = new Map(state.scoresByAsset);
      next.set(asset, scores);
      return { scoresByAsset: next, lastUpdated: new Date() };
    }),
  setConnected: (value) => set({ isConnected: value }),
}));

export const usePricesStore = create<PricesStore>((set) => ({
  prices: new Map(),
  isConnected: false,
  lastUpdated: null,
  updatePrice: (symbol, payload) =>
    set((state) => {
      const next = new Map(state.prices);
      next.set(symbol, payload);
      return { prices: next, lastUpdated: new Date() };
    }),
  setConnected: (value) => set({ isConnected: value }),
}));

export const useProviderStore = create<ProviderStore>((set, get) => ({
  providers: [],
  isConnected: false,
  lastUpdated: null,
  setProviders: (providers) => set({ providers, lastUpdated: new Date() }),
  setConnected: (value) => set({ isConnected: value }),
  isTesting: {},
  isTestingAll: false,
  lastTestResults: {},
  testProvider: async (name) => {
    if (get().isTesting[name]) {
      return;
    }
    set((s) => ({ isTesting: { ...s.isTesting, [name]: true } }));
    try {
      const slug = encodeURIComponent(name);
      const res = await fetch(apiUrl(`/api/v1/providers/${slug}/test`), {
        method: "POST",
        headers: { Accept: "application/json" },
      });
      const raw: unknown = await res.json();
      const fallbackStatus = synth_error_row(name, `http_${String(res.status)}`);
      const parsed =
        parse_provider_test_result(raw) ??
        (res.ok ? synth_error_row(name, "invalid_response") : fallbackStatus);
      set((s) => ({
        lastTestResults: { ...s.lastTestResults, [name]: parsed },
      }));
    } catch {
      set((s) => ({
        lastTestResults: { ...s.lastTestResults, [name]: synth_error_row(name, "network_error") },
      }));
    } finally {
      set((s) => {
        const next = { ...s.isTesting };
        delete next[name];
        return { isTesting: next };
      });
    }
  },
  testAllProviders: async () => {
    if (get().isTestingAll) {
      return;
    }
    if (Object.values(get().isTesting).some(Boolean)) {
      return;
    }
    set({ isTestingAll: true });
    try {
      const res = await fetch(apiUrl("/api/v1/providers/test-all"), {
        method: "POST",
        headers: { Accept: "application/json" },
      });
      const raw: unknown = await res.json();
      const rows = parse_provider_test_result_list(raw);
      if (!res.ok && rows.length === 0) {
        return;
      }
      set((s) => {
        const next = { ...s.lastTestResults };
        for (const r of rows) {
          next[r.provider] = r;
        }
        return { lastTestResults: next };
      });
    } catch {
      /* Avoid synthetic rows — operators rely on REST + WS snapshots. */
    } finally {
      set({ isTestingAll: false });
    }
  },
}));

export const useSystemStore = create<SystemStore>((set) => ({
  health: null,
  setHealth: (next) => set({ health: next }),
}));
