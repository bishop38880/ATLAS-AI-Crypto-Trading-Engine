import { useEffect, useMemo, useRef, useState } from "react";

import { map_system_ws_payload } from "../lib/channel-mappers";
import type { PipelineAgentPulseUi } from "../lib/channel-mappers";
import { apiUrl } from "../lib/url";
import { parse_agents_for_pipeline, useRealTimeData } from "../hooks/useRealTimeData";
import type { SystemHealth } from "../store/index";
import type { ConfluenceStreamData } from "../types/confluence";
import { parse_confluence_frame } from "../types/confluence-guard";
import { PageHeader } from "./ui/PageHeader";

declare global {
  interface Window {
    TradingView?: {
      widget: new (options: Record<string, unknown>) => unknown;
    };
  }
}

type SocketStatus = "connecting" | "open" | "closed" | "error";
type ApiStatus = "healthy" | "down";

interface EndpointStatus {
  label: string;
  path: string;
  status: ApiStatus;
  statusCode?: number;
  latencyMs?: number;
  error?: string;
}

interface PipelineEvent {
  type: string;
  cycleId?: string;
  asset?: string;
  stage?: string;
  status?: string;
  details?: Record<string, unknown>;
  agentName?: string;
  category?: string;
  score?: number;
  latencyMs?: number;
  cycleTs?: string;
}

interface AssetScoreSnapshot {
  asset: string;
  score: number;
  cycleId: string;
  cycleTs?: string;
}

interface MonitorLlmOutput {
  invoked: boolean;
  decision: string;
  confidence: number;
  crossCorrelationGrade: string;
  reasoning: string;
  wouldChangeIf: string;
  keyConvergences: string[];
  keyRisks: string[];
}

interface AssetAnalysis {
  asset: string;
  decision: string;
  score: number;
  rawConfluenceScore: number;
  confidence: number;
  pipelineConfidence: number;
  confidenceTier: string;
  reasoningSummary: string;
  keyConvergences: string[];
  keyRisks: string[];
  llm: MonitorLlmOutput;
}

interface MonitorResponse {
  analyses: AssetAnalysis[];
}

const PIPELINE_STAGES = [
  {
    id: "cycle_started",
    label: "Cycle Intake",
    description: "Asset, timeframe, cached market state, and query depth enter the loop.",
  },
  {
    id: "context_ready",
    label: "Context + RAG",
    description: "Provider context and retrieved memory are assembled for scoring.",
  },
  {
    id: "agent_activity",
    label: "Agent Fan-Out",
    description: "Technical, derivatives, on-chain, sentiment, regime, and risk agents score.",
  },
  {
    id: "risk_gate",
    label: "Risk Gate",
    description: "Risk vetoes, quorum checks, and degraded agents are surfaced.",
  },
  {
    id: "synthesis",
    label: "Synthesiser",
    description: "Agent outputs are merged into a canonical signal and confluence score.",
  },
  {
    id: "cycle_completed",
    label: "Output",
    description: "Final decision, confidence, telemetry, and downstream publish state.",
  },
];

const STATUS_CLASSES: Record<SocketStatus, string> = {
  connecting: "bg-amber-300 shadow-[0_0_16px_rgba(252,211,77,0.45)]",
  open: "bg-emerald-400 shadow-[0_0_16px_rgba(52,211,153,0.55)]",
  closed: "bg-slate-500",
  error: "bg-red-400 shadow-[0_0_16px_rgba(248,113,113,0.55)]",
};

const TRADING_VIEW_SCRIPT_SRC = "https://s3.tradingview.com/tv.js";

/** Production builds default off unless `VITE_FEATURE_TRADINGVIEW=true`; dev server defaults on unless explicitly `false`. */
function is_tradingview_embed_enabled(): boolean {
  const token = import.meta.env.VITE_FEATURE_TRADINGVIEW?.trim().toLowerCase() ?? "";
  if (token === "true" || token === "1" || token === "on") {
    return true;
  }
  if (token === "false" || token === "0" || token === "off") {
    return false;
  }
  return import.meta.env.DEV;
}

const API_HEALTH_CHECK_INTERVAL_MS = 7_500;
const MAX_PIPELINE_EVENTS = 80;
const ASSET_SCORE_CYCLE_MS = 1_400;

const PIPELINE_API_ENDPOINTS = [
  { label: "analysis", path: "/api/executive/analysis-monitor" },
  { label: "signals", path: "/api/signals/latest" },
  { label: "metrics", path: "/api/monitoring/metrics" },
  { label: "startup", path: "/api/monitoring/startup" },
  { label: "alerts", path: "/api/monitoring/alerts" },
  { label: "latency", path: "/api/monitoring/latency" },
  { label: "agent-zero", path: "/api/monitoring/agent-zero" },
  { label: "test-floor", path: "/api/test-floor" },
];

const DAILY_ROTATION_ASSETS = [
  { symbol: "BTC", logo: "B", gradient: "from-orange-400 to-amber-600" },
  { symbol: "ETH", logo: "E", gradient: "from-indigo-300 to-blue-600" },
  { symbol: "SOL", logo: "S", gradient: "from-fuchsia-400 to-emerald-400" },
  { symbol: "ZEC", logo: "Z", gradient: "from-amber-300 to-yellow-600" },
  { symbol: "XMR", logo: "M", gradient: "from-orange-500 to-slate-600" },
  { symbol: "HYPE", logo: "H", gradient: "from-lime-300 to-emerald-600" },
  { symbol: "PENGU", logo: "P", gradient: "from-sky-300 to-violet-500" },
  { symbol: "XAG", logo: "Ag", gradient: "from-slate-200 to-slate-500" },
  { symbol: "XAU", logo: "Au", gradient: "from-yellow-200 to-amber-500" },
];

const DAILY_ROTATION_SYMBOLS = new Set(
  DAILY_ROTATION_ASSETS.map((asset) => asset.symbol),
);

function normalizeAssetSymbol(asset: string | undefined): string {
  if (!asset || asset === "WAITING") {
    return "";
  }

  return asset
    .replace("/USDT", "")
    .replace("USDT", "")
    .replace("/", "")
    .replace("-", "")
    .toUpperCase();
}

function isDailyRotationAsset(asset: string | undefined): boolean {
  const symbol = normalizeAssetSymbol(asset);
  return DAILY_ROTATION_SYMBOLS.has(symbol);
}

function tradingViewSymbol(asset: string | undefined): string {
  if (!asset || asset === "WAITING") {
    return "BITGET:BTCUSDT";
  }

  const compactAsset = asset.replace("/", "").replace("-", "").toUpperCase();
  return `BITGET:${compactAsset}`;
}

function useApiHealthChecks(): EndpointStatus[] {
  const [endpoints, setEndpoints] = useState<EndpointStatus[]>(
    PIPELINE_API_ENDPOINTS.map((endpoint) => ({
      ...endpoint,
      status: "down",
    })),
  );

  useEffect(() => {
    let cancelled = false;

    const checkEndpoints = async () => {
      const checked = await Promise.all(
        PIPELINE_API_ENDPOINTS.map(async (endpoint) => {
          const startedAt = performance.now();
          try {
            const response = await fetch(apiUrl(endpoint.path), {
              headers: { Accept: "application/json" },
            });
            const latencyMs = Math.round(performance.now() - startedAt);
            return {
              ...endpoint,
              status: response.ok || response.status === 204 ? "healthy" : "down",
              statusCode: response.status,
              latencyMs,
            } satisfies EndpointStatus;
          } catch (exc) {
            return {
              ...endpoint,
              status: "down",
              error: exc instanceof Error ? exc.message : "request_failed",
            } satisfies EndpointStatus;
          }
        }),
      );

      if (!cancelled) {
        setEndpoints(checked);
      }
    };

    void checkEndpoints();
    const intervalId = window.setInterval(() => void checkEndpoints(), API_HEALTH_CHECK_INTERVAL_MS);

    return () => {
      cancelled = true;
      window.clearInterval(intervalId);
    };
  }, []);

  return endpoints;
}

function useAnalysisMonitor(): AssetAnalysis[] {
  const [analyses, setAnalyses] = useState<AssetAnalysis[]>([]);

  useEffect(() => {
    let cancelled = false;

    const refresh = async () => {
      try {
        const response = await fetch(apiUrl("/api/executive/analysis-monitor"), {
          headers: { Accept: "application/json" },
        });
        if (!response.ok) {
          return;
        }

        const body = (await response.json()) as MonitorResponse;
        if (!cancelled) {
          setAnalyses(body.analyses);
        }
      } catch {
        if (!cancelled) {
          setAnalyses([]);
        }
      }
    };

    void refresh();
    const intervalId = window.setInterval(() => void refresh(), 5_000);

    return () => {
      cancelled = true;
      window.clearInterval(intervalId);
    };
  }, []);

  return analyses;
}

function formatTimestamp(value: string | number | undefined): string {
  if (value === undefined || value === "") {
    return "waiting";
  }

  const timestamp = typeof value === "number" && value < 10_000_000_000 ? value * 1_000 : value;
  const parsed = typeof timestamp === "number" ? timestamp : Date.parse(timestamp);
  if (Number.isNaN(parsed)) {
    return String(value);
  }

  return new Intl.DateTimeFormat("en", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).format(parsed);
}

function normalizeActivityPayload(payload: unknown): PipelineEvent[] {
  if (payload === null || payload === undefined) {
    return [];
  }

  return Array.isArray(payload) ? (payload as PipelineEvent[]) : [payload as PipelineEvent];
}

function extractCompletedAssetScores(events: PipelineEvent[]): AssetScoreSnapshot[] {
  return events
    .filter((event) => event.type === "pipeline_stage")
    .filter((event) => event.stage === "cycle_completed")
    .filter((event) => event.asset && event.cycleId)
    .map((event) => ({
      asset: event.asset ?? "",
      score: Number(event.details?.score ?? 0),
      cycleId: event.cycleId ?? "",
      cycleTs: event.cycleTs,
    }))
    .filter((event) => event.asset !== "" && event.cycleId !== "")
    .filter((event) => isDailyRotationAsset(event.asset));
}

function stageStatus(stageId: string, events: PipelineEvent[], agents: PipelineAgentPulseUi[]): string {
  if (stageId === "agent_activity") {
    if (agents.some((agent) => agent.status === "RED")) {
      return "error";
    }
    if (agents.some((agent) => agent.status === "YELLOW")) {
      return "running";
    }
    return agents.length > 0 ? "complete" : "waiting";
  }

  if (stageId === "risk_gate") {
    if (agents.some((agent) => agent.category === "risk" && agent.status === "RED")) {
      return "error";
    }
    return agents.some((agent) => agent.category === "risk") ? "complete" : "waiting";
  }

  if (stageId === "synthesis") {
    return events.some((event) => event.stage === "cycle_completed") ? "complete" : "waiting";
  }

  const match = events.find((event) => event.stage === stageId);
  return match?.status ?? "waiting";
}

function stageClasses(status: string): string {
  if (status === "complete") {
    return "border-emerald-300/35 bg-emerald-300/10";
  }

  if (status === "running") {
    return "border-cyan-300/40 bg-cyan-300/10";
  }

  if (status === "error") {
    return "border-red-300/40 bg-red-300/10";
  }

  return "border-white/10 bg-white/[0.035]";
}

function ChannelBadge({ label, status }: { label: string; status: SocketStatus }) {
  return (
    <div className="rounded-2xl border border-white/10 bg-white/[0.04] px-4 py-3">
      <div className="flex items-center gap-3">
        <span className={`h-3 w-3 rounded-full ${STATUS_CLASSES[status]}`} />
        <span className="text-xs font-bold uppercase tracking-[0.25em] text-slate-300">{label}</span>
      </div>
      <p className="mt-1 text-xs text-slate-500">{status}</p>
    </div>
  );
}

function ConnectionPill({
  label,
  path,
  isHealthy,
  detail,
}: {
  label: string;
  path: string;
  isHealthy: boolean;
  detail: string;
}) {
  return (
    <div
      className={`rounded-2xl border px-4 py-3 ${
        isHealthy
          ? "border-emerald-300/35 bg-emerald-400/10"
          : "border-red-300/40 bg-red-400/10"
      }`}
      title={path}
    >
      <div className="flex items-center justify-between gap-3">
        <span className="truncate text-xs font-black uppercase tracking-[0.25em] text-white">
          {label}
        </span>
        <span
          className={`h-3 w-3 shrink-0 rounded-full ${
            isHealthy
              ? "bg-emerald-300 shadow-[0_0_16px_rgba(52,211,153,0.65)]"
              : "bg-red-400 shadow-[0_0_16px_rgba(248,113,113,0.65)]"
          }`}
        />
      </div>
      <p className={isHealthy ? "mt-1 text-xs text-emerald-100" : "mt-1 text-xs text-red-100"}>
        {detail}
      </p>
    </div>
  );
}

function ConnectionOverview({
  websocketStatuses,
  apiStatuses,
}: {
  websocketStatuses: Array<{ label: string; path: string; status: SocketStatus }>;
  apiStatuses: EndpointStatus[];
}) {
  return (
    <section className="mb-6 rounded-[2rem] border border-white/10 bg-white/[0.04] p-5">
      <div className="flex flex-col gap-2 md:flex-row md:items-end md:justify-between">
        <div>
          <h2 className="text-xl font-black text-white">Connections</h2>
          <p className="mt-1 text-sm text-slate-500">
            Green means connected and healthy. Red means disconnected or failing.
          </p>
        </div>
      </div>

      <div className="mt-5">
        <p className="mb-3 text-xs font-bold uppercase tracking-[0.3em] text-cyan-200">
          WebSockets
        </p>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          {websocketStatuses.map((endpoint) => (
            <ConnectionPill
              detail={endpoint.status}
              isHealthy={endpoint.status === "open"}
              key={endpoint.path}
              label={endpoint.label}
              path={endpoint.path}
            />
          ))}
        </div>
      </div>

      <div className="mt-5">
        <p className="mb-3 text-xs font-bold uppercase tracking-[0.3em] text-cyan-200">
          APIs
        </p>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          {apiStatuses.map((endpoint) => (
            <ConnectionPill
              detail={
                endpoint.status === "healthy"
                  ? `${endpoint.statusCode ?? 200} · ${endpoint.latencyMs ?? 0} ms`
                  : endpoint.error ?? `${endpoint.statusCode ?? "down"}`
              }
              isHealthy={endpoint.status === "healthy"}
              key={endpoint.path}
              label={endpoint.label}
              path={endpoint.path}
            />
          ))}
        </div>
      </div>
    </section>
  );
}

function DailyRotationStrip({
  currentAsset,
  assetScores,
}: {
  currentAsset: string | undefined;
  assetScores: Record<string, AssetScoreSnapshot>;
}) {
  const currentSymbol = normalizeAssetSymbol(currentAsset);

  return (
    <section className="mb-6 rounded-[2rem] border border-white/10 bg-white/[0.04] p-5">
      <div className="flex flex-col gap-2 md:flex-row md:items-end md:justify-between">
        <div>
          <h2 className="text-xl font-black text-white">Daily Rotation Assets</h2>
          <p className="mt-1 text-sm text-slate-500">
            Monitoring universe for this run. Assets light up as fresh scores land.
          </p>
        </div>
        <span className="rounded-full border border-cyan-300/20 bg-cyan-300/10 px-3 py-1 text-xs font-bold text-cyan-100">
          {currentSymbol || "WAITING"}
        </span>
      </div>

      <div className="mt-5 flex gap-3 overflow-x-auto pb-2">
        {DAILY_ROTATION_ASSETS.map((asset) => {
          const isActive = asset.symbol === currentSymbol;
          const scoreSnapshot = assetScores[asset.symbol];

          return (
            <div
              className={`flex min-w-32 items-center gap-3 rounded-2xl border px-4 py-3 transition ${
                isActive
                  ? "border-cyan-200/70 bg-cyan-300/15 shadow-[0_0_28px_rgba(34,211,238,0.25)]"
                  : "border-white/10 bg-slate-950/45"
              }`}
              key={asset.symbol}
            >
              <div
                className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-gradient-to-br ${asset.gradient} text-sm font-black text-white shadow-lg shadow-black/30`}
              >
                {asset.logo}
              </div>
              <div>
                <p className="font-black text-white">{asset.symbol}</p>
                <p className={isActive ? "text-xs text-cyan-100" : "text-xs text-slate-500"}>
                  {isActive
                    ? `new score ${Math.round(scoreSnapshot?.score ?? 0)}`
                    : scoreSnapshot
                      ? `${Math.round(scoreSnapshot.score)} scored`
                      : "queued"}
                </p>
              </div>
            </div>
          );
        })}
      </div>
    </section>
  );
}

function StageMap({ events, agents }: { events: PipelineEvent[]; agents: PipelineAgentPulseUi[] }) {
  return (
    <section className="grid gap-4 xl:grid-cols-6">
      {PIPELINE_STAGES.map((stage, index) => {
        const status = stageStatus(stage.id, events, agents);
        return (
          <article className={`rounded-3xl border p-5 ${stageClasses(status)}`} key={stage.id}>
            <p className="text-xs font-black uppercase tracking-[0.3em] text-cyan-200">
              {String(index + 1).padStart(2, "0")}
            </p>
            <h3 className="mt-3 text-lg font-black text-white">{stage.label}</h3>
            <p className="mt-2 text-sm leading-5 text-slate-400">{stage.description}</p>
            <div className="mt-4 rounded-full border border-white/10 bg-slate-950/45 px-3 py-1 text-xs font-bold uppercase tracking-[0.2em] text-slate-300">
              {status}
            </div>
          </article>
        );
      })}
    </section>
  );
}

function AgentMatrix({ agents }: { agents: PipelineAgentPulseUi[] }) {
  return (
    <section className="rounded-3xl border border-white/10 bg-white/[0.04] p-5">
      <h2 className="text-xl font-black text-white">Agent Fan-Out</h2>
      <div className="mt-4 grid gap-3 md:grid-cols-2 xl:grid-cols-3">
        {agents.length === 0 ? (
          <p className="text-sm text-slate-500">Waiting for agent status frames.</p>
        ) : (
          agents.map((agent) => (
            <div className="rounded-2xl border border-white/10 bg-slate-950/45 p-4" key={agent.name}>
              <div className="flex items-start justify-between gap-3">
                <div>
                  <p className="font-bold text-white">{agent.name}</p>
                  <p className="mt-1 text-xs uppercase tracking-[0.25em] text-slate-500">
                    {agent.category}
                    {agent.direction ? ` · ${agent.direction}` : ""}
                  </p>
                </div>
                <span
                  className={`rounded-full px-3 py-1 text-xs font-black ${
                    agent.status === "GREEN"
                      ? "bg-emerald-300/10 text-emerald-100"
                      : agent.status === "YELLOW"
                        ? "bg-amber-300/10 text-amber-100"
                        : "bg-red-300/10 text-red-100"
                  }`}
                >
                  {agent.status}
                </span>
              </div>
              <div className="mt-4 flex items-end justify-between gap-4">
                <div>
                  <p className="text-xs text-slate-500">Agent Score</p>
                  <p className="mt-1 text-2xl font-black text-cyan-200">
                    {agent.lastScore === null || agent.lastScore === undefined
                      ? "--"
                      : Math.round(agent.lastScore)}
                    <span className="text-xs text-slate-500">
                      {" "}
                      / {agent.maxPoints === null || agent.maxPoints === undefined
                        ? "--"
                        : Math.round(agent.maxPoints)}
                    </span>
                  </p>
                </div>
                <p className="text-right text-xs text-slate-500">{agent.lastPingMs} ms</p>
              </div>
              {agent.explanation ? (
                <p className="mt-3 line-clamp-2 text-sm leading-5 text-slate-400">
                  {agent.explanation}
                </p>
              ) : null}
            </div>
          ))
        )}
      </div>
    </section>
  );
}

function TradingViewChart({ asset }: { asset: string | undefined }) {
  const embed_enabled = is_tradingview_embed_enabled();
  const containerId = useMemo(() => `tradingview-${crypto.randomUUID()}`, []);
  const symbol = tradingViewSymbol(asset);

  useEffect(() => {
    if (!embed_enabled) {
      return undefined;
    }

    let cancelled = false;

    const mountWidget = () => {
      if (cancelled || !window.TradingView) {
        return;
      }

      const container = document.getElementById(containerId);
      container?.replaceChildren();

      new window.TradingView.widget({
        autosize: true,
        symbol,
        interval: "15",
        timezone: "Etc/UTC",
        theme: "dark",
        style: "1",
        locale: "en",
        enable_publishing: false,
        allow_symbol_change: true,
        hide_side_toolbar: false,
        details: true,
        studies: ["Volume@tv-basicstudies"],
        container_id: containerId,
      });
    };

    if (window.TradingView) {
      mountWidget();
    } else {
      const existingScript = document.querySelector<HTMLScriptElement>(
        `script[src="${TRADING_VIEW_SCRIPT_SRC}"]`,
      );
      const script = existingScript ?? document.createElement("script");
      script.src = TRADING_VIEW_SCRIPT_SRC;
      script.async = true;
      script.onload = mountWidget;

      if (!existingScript) {
        document.body.appendChild(script);
      }
    }

    return () => {
      cancelled = true;
      const container = document.getElementById(containerId);
      container?.replaceChildren();
    };
  }, [containerId, embed_enabled, symbol]);

  if (!embed_enabled) {
    return (
      <section className="mt-6 rounded-3xl border border-white/10 bg-white/[0.04] p-5">
        <div className="mb-4 flex flex-col gap-2 md:flex-row md:items-end md:justify-between">
          <div>
            <h2 className="text-xl font-black text-white">TradingView Chart</h2>
            <p className="mt-1 text-sm text-slate-500">
              External chart embed is gated for supply-chain hygiene. Enable with{" "}
              <span className="font-mono text-slate-300">VITE_FEATURE_TRADINGVIEW=true</span> in production builds (dev defaults
              on).
            </p>
          </div>
        </div>
      </section>
    );
  }

  return (
    <section className="mt-6 rounded-3xl border border-white/10 bg-white/[0.04] p-5">
      <div className="mb-4 flex flex-col gap-2 md:flex-row md:items-end md:justify-between">
        <div>
          <h2 className="text-xl font-black text-white">TradingView Chart</h2>
          <p className="mt-1 text-sm text-slate-500">
            Live market context for {asset && asset !== "WAITING" ? asset : "BTC/USDT"}.
          </p>
        </div>
        <span className="rounded-full border border-cyan-300/20 bg-cyan-300/10 px-3 py-1 text-xs font-bold text-cyan-100">
          {symbol}
        </span>
      </div>
      <div className="h-[520px] overflow-hidden rounded-2xl border border-white/10 bg-slate-950/70">
        <div className="h-full w-full" id={containerId} />
      </div>
    </section>
  );
}

function LlmSynthesisPanel({ analysis }: { analysis: AssetAnalysis | undefined }) {
  const llm = analysis?.llm;
  const hasLlmOutput = Boolean(llm?.invoked && llm.reasoning);

  return (
    <section className="mb-6 rounded-3xl border border-white/10 bg-white/[0.04] p-5">
      <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
        <div>
          <h2 className="text-xl font-black text-white">LLM / Synthesis Output</h2>
          <p className="mt-1 text-sm text-slate-500">
            DeepSeek output when invoked. Deterministic synthesis is shown while the LLM path is idle.
          </p>
        </div>
        <span
          className={`rounded-full border px-3 py-1 text-xs font-black uppercase tracking-[0.2em] ${
            hasLlmOutput
              ? "border-emerald-300/25 bg-emerald-300/10 text-emerald-100"
              : "border-amber-300/25 bg-amber-300/10 text-amber-100"
          }`}
        >
          {hasLlmOutput ? "LLM invoked" : "LLM not invoked"}
        </span>
      </div>

      {analysis ? (
        <div className="mt-4 grid gap-4 lg:grid-cols-[1.2fr_0.8fr]">
          <div className="rounded-2xl border border-white/10 bg-slate-950/45 p-4">
            <p className="text-xs font-bold uppercase tracking-[0.25em] text-cyan-200">
              {hasLlmOutput ? "DeepSeek reasoning" : "Current synthesis reasoning"}
            </p>
            <p className="mt-3 text-sm leading-6 text-slate-300">
              {hasLlmOutput
                ? llm?.reasoning
                : analysis.reasoningSummary || "No synthesis output has been cached yet."}
            </p>
          </div>

          <div className="rounded-2xl border border-white/10 bg-slate-950/45 p-4">
            <p className="text-xs font-bold uppercase tracking-[0.25em] text-slate-500">
              Output state
            </p>
            <dl className="mt-3 space-y-2 text-sm">
              <div className="flex justify-between gap-3">
                <dt className="text-slate-500">Asset</dt>
                <dd className="font-bold text-white">{analysis.asset}</dd>
              </div>
              <div className="flex justify-between gap-3">
                <dt className="text-slate-500">Decision</dt>
                <dd className="font-bold text-white">{analysis.decision}</dd>
              </div>
              <div className="flex justify-between gap-3">
                <dt className="text-slate-500">Confidence tier</dt>
                <dd className="font-bold text-cyan-100">{analysis.confidenceTier}</dd>
              </div>
              <div className="flex justify-between gap-3">
                <dt className="text-slate-500">LLM reason</dt>
                <dd className="text-right text-amber-100">
                  {hasLlmOutput
                    ? llm?.crossCorrelationGrade
                    : "deepseek_evaluation is empty"}
                </dd>
              </div>
            </dl>
          </div>
        </div>
      ) : (
        <p className="mt-4 rounded-2xl border border-dashed border-slate-700 p-4 text-sm text-slate-500">
          Waiting for cached signal analysis.
        </p>
      )}
    </section>
  );
}

function RagContextDetails({ details }: { details: Record<string, unknown> }) {
  const status = String(details.rag_status ?? "unknown");
  const count = Number(details.rag_contexts ?? 0);
  const error = typeof details.rag_error === "string" ? details.rag_error : "";
  const summaries = Array.isArray(details.rag_summaries) ? details.rag_summaries : [];
  const statusClass =
    status === "ready"
      ? "border-emerald-300/25 bg-emerald-300/10 text-emerald-100"
      : status === "disabled" || status === "error"
        ? "border-red-300/25 bg-red-300/10 text-red-100"
        : "border-amber-300/25 bg-amber-300/10 text-amber-100";

  return (
    <div className="mt-3 rounded-xl border border-white/10 bg-black/25 p-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className={`rounded-full border px-3 py-1 text-xs font-black uppercase tracking-[0.18em] ${statusClass}`}>
          RAG {status.replace("_", " ")}
        </span>
        <span className="text-xs font-bold text-slate-300">{count} contexts retrieved</span>
      </div>

      {error ? (
        <p className="mt-3 text-xs leading-5 text-red-100">{error}</p>
      ) : null}

      {summaries.length > 0 ? (
        <div className="mt-3 space-y-2">
          {summaries.map((summary, index) => {
            const item = summary as Record<string, unknown>;
            return (
              <div className="rounded-lg bg-slate-950/60 p-3" key={`${String(item.title)}-${index}`}>
                <p className="text-xs font-bold text-cyan-100">{String(item.title ?? "context")}</p>
                <p className="mt-1 text-xs text-slate-400">{String(item.text ?? "")}</p>
              </div>
            );
          })}
        </div>
      ) : null}
    </div>
  );
}

function EventTimeline({ events }: { events: PipelineEvent[] }) {
  const visibleEvents = events.slice(0, 16);

  return (
    <section className="rounded-3xl border border-white/10 bg-white/[0.04] p-5">
      <h2 className="text-xl font-black text-white">Live Pipeline Events</h2>
      <div className="mt-4 space-y-3">
        {visibleEvents.length === 0 ? (
          <p className="text-sm text-slate-500">Waiting for pipeline telemetry.</p>
        ) : (
          visibleEvents.map((event, index) => (
            <div
              className="rounded-2xl border border-white/10 bg-slate-950/45 px-4 py-3"
              key={`${event.cycleId ?? event.agentName ?? event.type}-${event.stage ?? event.status}-${index}`}
            >
              <div className="flex flex-col gap-2 md:flex-row md:items-center md:justify-between">
                <div>
                  <p className="font-bold text-white">
                    {event.stage ?? event.agentName ?? event.type}
                  </p>
                  <p className="mt-1 text-xs text-slate-500">
                    {event.asset ?? event.category ?? "pipeline"} · {formatTimestamp(event.cycleTs)}
                  </p>
                </div>
                <span className="rounded-full border border-cyan-300/20 bg-cyan-300/10 px-3 py-1 text-xs font-bold uppercase tracking-[0.2em] text-cyan-100">
                  {event.status ?? "event"}
                </span>
              </div>
              {event.details && event.stage === "context_ready" ? (
                <RagContextDetails details={event.details} />
              ) : event.details ? (
                  <pre className="mt-3 overflow-x-auto rounded-xl bg-black/30 p-3 text-xs text-slate-300">
                    {JSON.stringify(event.details, null, 2)}
                  </pre>
                ) : null}
            </div>
          ))
        )}
      </div>
    </section>
  );
}

export function PipelineLivePage() {
  const agentsChannel = useRealTimeData<PipelineAgentPulseUi[]>("pipeline-live-agents", "/ws/agents", [], {
    parse: (raw) => parse_agents_for_pipeline(raw),
  });
  const activityChannel = useRealTimeData<PipelineEvent[]>("pipeline-live-activity", "/ws/pipeline-activity", [], {
    parse: (raw): PipelineEvent[] | null => normalizeActivityPayload(raw),
  });
  const systemChannel = useRealTimeData<SystemHealth | null>("pipeline-live-system", "/ws/system", null, {
    parse: (raw) => map_system_ws_payload(raw),
  });
  const confluenceChannel = useRealTimeData<ConfluenceStreamData | null>(
    "pipeline-live-confluence",
    "/ws/confluence",
    null,
    {
      parse: (raw): ConfluenceStreamData | null => parse_confluence_frame(raw),
    },
  );
  const apiStatuses = useApiHealthChecks();
  const analyses = useAnalysisMonitor();
  const [pipelineEvents, setPipelineEvents] = useState<PipelineEvent[]>([]);
  const [activeScoredSnapshot, setActiveScoredSnapshot] = useState<AssetScoreSnapshot | undefined>(undefined);
  const [assetScores, setAssetScores] = useState<Record<string, AssetScoreSnapshot>>({});
  const [assetScoreQueue, setAssetScoreQueue] = useState<AssetScoreSnapshot[]>([]);
  const seenCompletedCycles = useRef<Set<string>>(new Set());

  const agents: PipelineAgentPulseUi[] = agentsChannel.data ?? [];
  const system = systemChannel.data;
  const confluence = confluenceChannel.data;
  const confluenceAsset = isDailyRotationAsset(confluence?.asset) ? confluence?.asset : undefined;
  const displayAsset = activeScoredSnapshot?.asset ?? confluenceAsset;
  const displayScore = activeScoredSnapshot?.score ?? confluence?.total_score ?? 0;
  const currentAnalysis = analyses.find(
    (analysis) => normalizeAssetSymbol(analysis.asset) === normalizeAssetSymbol(displayAsset),
  );
  const websocketStatuses = [
    { label: "agents", path: "/ws/agents", status: agentsChannel.status },
    { label: "pipeline", path: "/ws/pipeline-activity", status: activityChannel.status },
    { label: "system", path: "/ws/system", status: systemChannel.status },
    { label: "confluence", path: "/ws/confluence", status: confluenceChannel.status },
  ];

  useEffect(() => {
    const incomingEvents = normalizeActivityPayload(activityChannel.data);
    if (incomingEvents.length === 0) {
      return;
    }

    const completedScores = extractCompletedAssetScores(incomingEvents)
      .filter((scoreEvent) => !seenCompletedCycles.current.has(scoreEvent.cycleId))
      .reverse();

    for (const scoreEvent of completedScores) {
      seenCompletedCycles.current.add(scoreEvent.cycleId);
    }

    if (completedScores.length > 0) {
      setAssetScoreQueue((currentQueue) => [...currentQueue, ...completedScores]);
    }

    setPipelineEvents((currentEvents) => {
      const mergedEvents = [...incomingEvents, ...currentEvents];
      const seenKeys = new Set<string>();
      const dedupedEvents: PipelineEvent[] = [];

      for (const event of mergedEvents) {
        const eventKey = [
          event.cycleId,
          event.asset,
          event.stage,
          event.agentName,
          event.status,
          event.cycleTs,
          JSON.stringify(event.details ?? {}),
        ].join("|");

        if (seenKeys.has(eventKey)) {
          continue;
        }

        seenKeys.add(eventKey);
        dedupedEvents.push(event);
      }

      return dedupedEvents.slice(0, MAX_PIPELINE_EVENTS);
    });
  }, [activityChannel.data]);

  useEffect(() => {
    if (assetScoreQueue.length === 0) {
      return;
    }

    const timerId = window.setTimeout(() => {
      const [nextScore] = assetScoreQueue;
      if (!nextScore) {
        return;
      }

      setActiveScoredSnapshot(nextScore);
      setAssetScores((currentScores) => ({
        ...currentScores,
        [normalizeAssetSymbol(nextScore.asset)]: nextScore,
      }));
      setAssetScoreQueue((currentQueue) => currentQueue.slice(1));
    }, ASSET_SCORE_CYCLE_MS);

    return () => window.clearTimeout(timerId);
  }, [assetScoreQueue]);

  return (
    <div className="relative space-y-6">
      <PageHeader
        kicker="POLARIS pipeline"
        title="Live Function Trace"
        description="Real-time view of the ATLAS analysis loop from context assembly through agent fan-out, risk gating, synthesis, confidence gating, and output."
        actions={
          <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
            <ChannelBadge label="agents" status={agentsChannel.status} />
            <ChannelBadge label="activity" status={activityChannel.status} />
            <ChannelBadge label="system" status={systemChannel.status} />
            <ChannelBadge label="confluence" status={confluenceChannel.status} />
          </div>
        }
      />

      <ConnectionOverview apiStatuses={apiStatuses} websocketStatuses={websocketStatuses} />
        <DailyRotationStrip assetScores={assetScores} currentAsset={displayAsset} />

        {[agentsChannel.error, activityChannel.error, systemChannel.error, confluenceChannel.error]
          .filter(Boolean)
          .map((error) => (
            <div
              className="mb-4 rounded-2xl border border-red-400/30 bg-red-500/10 px-5 py-3 text-sm text-red-100"
              key={error}
            >
              {error}
            </div>
          ))}

        <section className="mb-6 grid gap-4 md:grid-cols-2 xl:grid-cols-4">
          <div className="rounded-3xl border border-white/10 bg-white/[0.04] p-5">
            <p className="text-xs uppercase tracking-[0.3em] text-slate-500">Current Asset</p>
            <p className="mt-2 text-3xl font-black text-white">{displayAsset ?? "WAITING"}</p>
          </div>
          <div className="rounded-3xl border border-white/10 bg-white/[0.04] p-5">
            <p className="text-xs uppercase tracking-[0.3em] text-slate-500">Confluence</p>
            <p className="mt-2 text-3xl font-black text-cyan-200">
              {Math.round(displayScore)}
            </p>
          </div>
          <div className="rounded-3xl border border-white/10 bg-white/[0.04] p-5">
            <p className="text-xs uppercase tracking-[0.3em] text-slate-500">System</p>
            <p className="mt-2 text-3xl font-black text-white">
              {system?.overallStatus ?? "UNKNOWN"}
            </p>
          </div>
          <div className="rounded-3xl border border-white/10 bg-white/[0.04] p-5">
            <p className="text-xs uppercase tracking-[0.3em] text-slate-500">Cycles</p>
            <p className="mt-2 text-3xl font-black text-white">{system?.cycleCount ?? 0}</p>
          </div>
        </section>

        <StageMap agents={agents} events={pipelineEvents} />

        <div className="mt-6 grid gap-6 xl:grid-cols-[1fr_440px]">
          <div>
            <LlmSynthesisPanel analysis={currentAnalysis} />
            <AgentMatrix agents={agents} />
            <TradingViewChart asset={displayAsset} />
          </div>
          <EventTimeline events={pipelineEvents} />
        </div>
    </div>
  );
}
