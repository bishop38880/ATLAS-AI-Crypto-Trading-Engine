import { useCallback, useEffect, useMemo, useState } from "react";

import { apiUrl } from "../lib/url";

interface MonitorSubSignal {
  name: string;
  value: string | number | boolean | null;
  flag: string;
}

interface MonitorAgent {
  name: string;
  score: number;
  maxScore: number;
  direction: string;
  explanation: string;
  risks: string[];
  convergences: string[];
  subSignals: MonitorSubSignal[];
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
  timestamp: string;
  timestampEpoch: number;
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
  agents: MonitorAgent[];
}

interface MonitorResponse {
  fetched_at: string;
  asset_count: number;
  analyses: AssetAnalysis[];
}

const DEFAULT_MONITOR_PATH = "/api/executive/analysis-monitor";
const REFRESH_INTERVAL_MS = 5_000;

function formatIsoTime(timestamp: string): string {
  if (!timestamp) {
    return "waiting";
  }

  const parsed = Date.parse(timestamp);
  if (Number.isNaN(parsed)) {
    return timestamp;
  }

  return new Intl.DateTimeFormat("en", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).format(parsed);
}

function formatPercent(value: number): string {
  return `${Math.round(value * 100)}%`;
}

function confidenceClass(value: number): string {
  if (value >= 0.75) {
    return "text-emerald-200";
  }

  if (value >= 0.45) {
    return "text-amber-200";
  }

  return "text-red-200";
}

function PillList({ items, tone }: { items: string[]; tone: "cyan" | "red" }) {
  const classes =
    tone === "cyan"
      ? "border-cyan-300/20 bg-cyan-300/10 text-cyan-100"
      : "border-red-300/20 bg-red-300/10 text-red-100";

  if (items.length === 0) {
    return <p className="text-sm text-slate-500">None reported.</p>;
  }

  return (
    <div className="flex flex-wrap gap-2">
      {items.map((item) => (
        <span className={`rounded-full border px-3 py-1 text-xs ${classes}`} key={item}>
          {item}
        </span>
      ))}
    </div>
  );
}

function AssetSelector({
  analyses,
  selectedAsset,
  onSelect,
}: {
  analyses: AssetAnalysis[];
  selectedAsset: string | null;
  onSelect: (asset: string) => void;
}) {
  return (
    <div className="flex gap-2 overflow-x-auto pb-2">
      {analyses.map((analysis) => {
        const isSelected = analysis.asset === selectedAsset;
        return (
          <button
            className={`min-w-36 rounded-2xl border px-4 py-3 text-left transition ${
              isSelected
                ? "border-cyan-300/60 bg-cyan-300/15"
                : "border-white/10 bg-white/[0.04] hover:border-cyan-300/30"
            }`}
            key={analysis.asset}
            onClick={() => onSelect(analysis.asset)}
            type="button"
          >
            <span className="block text-sm font-black text-white">{analysis.asset}</span>
            <span className="mt-1 block text-xs text-slate-500">
              {analysis.decision} · {Math.round(analysis.rawConfluenceScore)}
            </span>
          </button>
        );
      })}
    </div>
  );
}

function AgentOutputList({ agents }: { agents: MonitorAgent[] }) {
  if (agents.length === 0) {
    return (
      <div className="rounded-2xl border border-dashed border-slate-700 p-4 text-sm text-slate-500">
        No agent breakdown has been cached for this asset yet.
      </div>
    );
  }

  return (
    <div className="grid gap-3 lg:grid-cols-2">
      {agents.map((agent) => (
        <article className="rounded-2xl border border-white/10 bg-slate-950/45 p-4" key={agent.name}>
          <div className="flex items-start justify-between gap-4">
            <div>
              <h4 className="font-bold text-white">{agent.name}</h4>
              <p className="mt-1 text-xs uppercase tracking-[0.25em] text-slate-500">
                {agent.direction}
              </p>
            </div>
            <p className="text-xl font-black text-cyan-200">
              {Math.round(agent.score)}
              <span className="text-xs text-slate-500"> / {Math.round(agent.maxScore)}</span>
            </p>
          </div>
          <p className="mt-3 text-sm text-slate-300">{agent.explanation || "No explanation."}</p>
          <div className="mt-4 flex flex-wrap gap-2">
            {agent.subSignals.slice(0, 6).map((signal) => (
              <span
                className="rounded-full border border-white/10 bg-white/[0.04] px-3 py-1 text-xs text-slate-300"
                key={`${agent.name}-${signal.name}`}
                title={signal.flag}
              >
                {signal.name}: {String(signal.value)}
              </span>
            ))}
          </div>
        </article>
      ))}
    </div>
  );
}

export function AnalysisMonitorWindow() {
  const [analyses, setAnalyses] = useState<AssetAnalysis[]>([]);
  const [selectedAsset, setSelectedAsset] = useState<string | null>(null);
  const [lastFetchedAt, setLastFetchedAt] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const selectedAnalysis = useMemo(() => {
    return analyses.find((analysis) => analysis.asset === selectedAsset) ?? analyses[0] ?? null;
  }, [analyses, selectedAsset]);

  const refresh = useCallback(async () => {
    setIsLoading(true);
    try {
      const response = await fetch(apiUrl(DEFAULT_MONITOR_PATH), {
        headers: { Accept: "application/json" },
      });
      if (!response.ok) {
        throw new Error(`monitor_status_${response.status}`);
      }

      const body = (await response.json()) as MonitorResponse;
      setAnalyses(body.analyses);
      setLastFetchedAt(body.fetched_at);
      setError(null);
      setSelectedAsset((current) => current ?? body.analyses[0]?.asset ?? null);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "analysis_monitor_failed");
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    const startId = window.setTimeout(() => void refresh(), 0);
    const intervalId = window.setInterval(() => void refresh(), REFRESH_INTERVAL_MS);
    return () => {
      window.clearTimeout(startId);
      window.clearInterval(intervalId);
    };
  }, [refresh]);

  return (
    <section className="mt-8 overflow-hidden rounded-[2rem] border border-cyan-300/20 bg-slate-950/90 shadow-2xl shadow-cyan-950/30">
      <div className="flex flex-col gap-4 border-b border-white/10 bg-white/[0.035] px-6 py-5 lg:flex-row lg:items-center lg:justify-between">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.45em] text-cyan-300">
            LLM Analysis Monitor
          </p>
          <h2 className="mt-2 text-2xl font-black text-white">Per-Asset Intelligence Window</h2>
          <p className="mt-2 text-sm text-slate-400">
            Watches cached RAG, DeepSeek, and agent outputs from the autonomous loop.
          </p>
        </div>
        <button
          className="rounded-2xl border border-cyan-300/30 bg-cyan-300/10 px-4 py-3 text-sm font-bold text-cyan-100 transition hover:border-cyan-200/60 disabled:opacity-50"
          disabled={isLoading}
          onClick={() => void refresh()}
          type="button"
        >
          {isLoading ? "Refreshing..." : "Refresh"}
        </button>
      </div>

      <div className="space-y-6 p-6">
        {error ? (
          <div className="rounded-2xl border border-red-400/30 bg-red-500/10 px-4 py-3 text-sm text-red-100">
            {error}
          </div>
        ) : null}

        {analyses.length === 0 ? (
          <div className="rounded-3xl border border-dashed border-slate-700 bg-slate-900/45 p-6 text-slate-400">
            No per-asset analysis has been cached yet. Start RAG analysis and this window will
            populate as assets complete.
          </div>
        ) : (
          <AssetSelector
            analyses={analyses}
            onSelect={setSelectedAsset}
            selectedAsset={selectedAnalysis?.asset ?? null}
          />
        )}

        {selectedAnalysis ? (
          <div className="grid gap-6 xl:grid-cols-[360px_1fr]">
            <aside className="space-y-4 rounded-3xl border border-white/10 bg-white/[0.04] p-5">
              <div className="flex items-start justify-between gap-4">
                <div>
                  <h3 className="text-3xl font-black text-white">{selectedAnalysis.asset}</h3>
                  <p className="mt-1 text-sm text-slate-500">
                    Updated {formatIsoTime(selectedAnalysis.timestamp)}
                  </p>
                </div>
                <span className="rounded-full border border-emerald-300/20 bg-emerald-300/10 px-3 py-1 text-xs font-bold text-emerald-100">
                  {selectedAnalysis.decision}
                </span>
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div className="rounded-2xl border border-white/10 bg-slate-950/45 p-4">
                  <p className="text-xs text-slate-500">Raw Score</p>
                  <p className="mt-1 text-2xl font-black text-cyan-200">
                    {Math.round(selectedAnalysis.rawConfluenceScore)}
                  </p>
                </div>
                <div className="rounded-2xl border border-white/10 bg-slate-950/45 p-4">
                  <p className="text-xs text-slate-500">Pipeline</p>
                  <p className={`mt-1 text-2xl font-black ${confidenceClass(selectedAnalysis.pipelineConfidence)}`}>
                    {formatPercent(selectedAnalysis.pipelineConfidence)}
                  </p>
                </div>
              </div>

              <div className="rounded-2xl border border-white/10 bg-slate-950/45 p-4">
                <p className="text-xs text-slate-500">Last Fetch</p>
                <p className="mt-1 text-sm text-slate-300">
                  {lastFetchedAt ? formatIsoTime(lastFetchedAt) : "not fetched"}
                </p>
              </div>
            </aside>

            <div className="space-y-5">
              <section className="rounded-3xl border border-white/10 bg-white/[0.04] p-5">
                <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
                  <div>
                    <h3 className="text-xl font-black text-white">LLM Output</h3>
                    <p className="mt-1 text-sm text-slate-500">
                      {selectedAnalysis.llm.invoked
                        ? `${selectedAnalysis.llm.decision} · ${selectedAnalysis.llm.crossCorrelationGrade}`
                        : "DeepSeek was not invoked for this cycle."}
                    </p>
                  </div>
                  <p className={`text-2xl font-black ${confidenceClass(selectedAnalysis.llm.confidence)}`}>
                    {formatPercent(selectedAnalysis.llm.confidence)}
                  </p>
                </div>
                <p className="mt-4 text-sm leading-6 text-slate-300">
                  {selectedAnalysis.llm.reasoning ||
                    selectedAnalysis.reasoningSummary ||
                    "No reasoning has been cached for this asset yet."}
                </p>
                <div className="mt-5 grid gap-4 md:grid-cols-2">
                  <div>
                    <p className="mb-2 text-xs font-bold uppercase tracking-[0.25em] text-slate-500">
                      Convergences
                    </p>
                    <PillList
                      items={
                        selectedAnalysis.llm.keyConvergences.length > 0
                          ? selectedAnalysis.llm.keyConvergences
                          : selectedAnalysis.keyConvergences
                      }
                      tone="cyan"
                    />
                  </div>
                  <div>
                    <p className="mb-2 text-xs font-bold uppercase tracking-[0.25em] text-slate-500">
                      Risks
                    </p>
                    <PillList
                      items={
                        selectedAnalysis.llm.keyRisks.length > 0
                          ? selectedAnalysis.llm.keyRisks
                          : selectedAnalysis.keyRisks
                      }
                      tone="red"
                    />
                  </div>
                </div>
                {selectedAnalysis.llm.wouldChangeIf ? (
                  <div className="mt-5 rounded-2xl border border-amber-300/20 bg-amber-300/10 p-4 text-sm text-amber-100">
                    Would change if: {selectedAnalysis.llm.wouldChangeIf}
                  </div>
                ) : null}
              </section>

              <section className="rounded-3xl border border-white/10 bg-white/[0.04] p-5">
                <h3 className="text-xl font-black text-white">Agent Analysis Outputs</h3>
                <div className="mt-4">
                  <AgentOutputList agents={selectedAnalysis.agents} />
                </div>
              </section>
            </div>
          </div>
        ) : null}
      </div>
    </section>
  );
}
