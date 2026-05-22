import { useQuery } from "@tanstack/react-query";
import type { ReactElement } from "react";
import { useEffect, useMemo, useState } from "react";

import { cn } from "../../lib/cn";
import { apiUrl } from "../../lib/url";
import { AssetLogo } from "../AssetLogo";
import { EmptyState } from "../ui/EmptyState";
import { Skeleton } from "../ui/Skeleton";

interface OutcomeHorizons {
  pct1h: number | null;
  pct4h: number | null;
  pct24h: number | null;
}

interface AgentVerdictRow {
  agentName: string;
  state: string;
  score: number;
  maxScore: number;
  direction: string;
  veto: boolean;
}

interface GateEventRow {
  name: string;
  fired: boolean;
  detail: string;
}

interface RiskManagerRow {
  passed: boolean;
  vetoed: boolean;
  reason: string;
}

interface DecisionJournalEntry {
  signalId: string;
  asset: string;
  timestamp: string;
  timeframe: string;
  scoreBreakdown: string;
  normalizedScore: number;
  rawScore: number;
  decision: string;
  confidence: number;
  actionTaken: string;
  entryPrice: string;
  outcomePct: number | null;
  outcomeHorizons: OutcomeHorizons;
  reasoningSummary: string;
  primaryAgent: string | null;
  agentVerdicts: AgentVerdictRow[];
  gates: GateEventRow[];
  riskManager: RiskManagerRow;
  pipelineConfidence: number | null;
  confidenceTier: string | null;
  exitReason: string | null;
}

interface DecisionJournalPayload {
  entries: DecisionJournalEntry[];
  total: number;
  hasMore: boolean;
  nextOffset: number | null;
}

function build_journal_url(params: {
  limit: number;
  offset: number;
  since: string;
  outcome: string;
  score_min: number;
  score_max: number;
  agent: string;
}): string {
  const search = new URLSearchParams({
    limit: String(params.limit),
    offset: String(params.offset),
    since: params.since,
    outcome: params.outcome,
    score_min: String(params.score_min),
    score_max: String(params.score_max),
    agent: params.agent,
  });
  return apiUrl(`/api/decisions/journal?${search.toString()}`);
}

async function fetch_decision_journal(url: string): Promise<DecisionJournalPayload> {
  const response = await fetch(url, { headers: { Accept: "application/json" } });
  if (!response.ok) {
    throw new Error(`decision_journal_${response.status}`);
  }
  return response.json() as Promise<DecisionJournalPayload>;
}

function format_pct(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return "—";
  }
  const sign = value > 0 ? "+" : "";
  return `${sign}${value.toFixed(2)}%`;
}

function outcome_class(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return "text-[var(--text-tertiary)]";
  }
  if (value > 0.05) {
    return "text-[var(--success)]";
  }
  if (value < -0.05) {
    return "text-[var(--sell)]";
  }
  return "text-[var(--text-secondary)]";
}

export function DecisionJournalPage(): ReactElement {
  const [since, setSince] = useState<string>("30d");
  const [outcome, setOutcome] = useState<string>("all");
  const [scoreMin, setScoreMin] = useState<number>(0);
  const [scoreMax, setScoreMax] = useState<number>(100);
  const [agent, setAgent] = useState<string>("");
  const [offset, setOffset] = useState<number>(0);
  const [expanded, setExpanded] = useState<ReadonlySet<string>>(new Set());

  const page_limit = 40;
  const url = useMemo(
    () =>
      build_journal_url({
        limit: page_limit,
        offset,
        since,
        outcome,
        score_min: scoreMin,
        score_max: scoreMax,
        agent: agent.trim(),
      }),
    [since, outcome, scoreMin, scoreMax, agent, offset],
  );

  const query = useQuery({
    queryKey: ["decisions", "journal", url],
    queryFn: () => fetch_decision_journal(url),
    staleTime: 20_000,
  });

  const [accumulated, setAccumulated] = useState<DecisionJournalEntry[]>([]);

  useEffect(() => {
    if (query.data === undefined) {
      return;
    }
    if (offset === 0) {
      setAccumulated(query.data.entries);
    } else {
      setAccumulated((previous) => [...previous, ...query.data.entries]);
    }
  }, [query.data, offset]);

  const toggle = (id: string): void => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });
  };

  if (query.isLoading) {
    return (
      <section className="space-y-4" aria-busy="true" aria-label="Decision journal loading">
        <Skeleton className="block h-10 w-72" height={40} />
        <Skeleton className="block h-64 w-full" height={256} />
      </section>
    );
  }

  if (query.error !== null) {
    return (
      <EmptyState
        title="Decision journal unavailable"
        description="The `/api/decisions/journal` endpoint requires PostgreSQL (`signal_history`). Confirm the API is running and the database is reachable."
      />
    );
  }

  const data = query.data;
  const rows = accumulated;

  return (
    <div className="space-y-6" aria-live="polite">
      <header className="space-y-1">
        <h1 className="text-lg font-black text-[var(--text-primary)]">Trade Journal</h1>
        <p className="max-w-3xl text-xs text-[var(--text-secondary)]">
          Queryable log of scoring decisions from `signal_history`, joined to `decision_provenance` when
          available. Expand any row for per-agent votes, gate hits, Risk Manager disposition, and horizon
          outcomes (metadata-driven 1h/4h/24h when PROMETHEUS back-fills them).
        </p>
      </header>

      <div className="flex flex-wrap items-end gap-3 rounded-[var(--radius-md)] border border-[var(--border)] bg-[var(--bg-elevated)]/40 p-3">
        <label className="flex flex-col gap-1 text-[10px] font-semibold uppercase tracking-wide text-[var(--text-tertiary)]">
          Window
          <select
            value={since}
            onChange={(event) => {
              setOffset(0);
              setSince(event.target.value);
            }}
            className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[var(--bg-base)] px-2 py-1.5 text-xs text-[var(--text-primary)]"
          >
            <option value="24h">24 hours</option>
            <option value="7d">7 days</option>
            <option value="30d">30 days</option>
            <option value="90d">90 days</option>
          </select>
        </label>
        <label className="flex flex-col gap-1 text-[10px] font-semibold uppercase tracking-wide text-[var(--text-tertiary)]">
          Outcome
          <select
            value={outcome}
            onChange={(event) => {
              setOffset(0);
              setOutcome(event.target.value);
            }}
            className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[var(--bg-base)] px-2 py-1.5 text-xs text-[var(--text-primary)]"
          >
            <option value="all">All</option>
            <option value="pending">Pending</option>
            <option value="win">Winners</option>
            <option value="loss">Losers</option>
            <option value="scratch">Scratch</option>
          </select>
        </label>
        <label className="flex flex-col gap-1 text-[10px] font-semibold uppercase tracking-wide text-[var(--text-tertiary)]">
          Score min
          <input
            type="number"
            min={0}
            max={100}
            value={scoreMin}
            onChange={(event) => {
              setOffset(0);
              setScoreMin(Number.parseInt(event.target.value, 10) || 0);
            }}
            className="w-20 rounded-[var(--radius-sm)] border border-[var(--border)] bg-[var(--bg-base)] px-2 py-1.5 font-mono text-xs text-[var(--text-primary)]"
          />
        </label>
        <label className="flex flex-col gap-1 text-[10px] font-semibold uppercase tracking-wide text-[var(--text-tertiary)]">
          Score max
          <input
            type="number"
            min={0}
            max={100}
            value={scoreMax}
            onChange={(event) => {
              setOffset(0);
              setScoreMax(Number.parseInt(event.target.value, 10) || 100);
            }}
            className="w-20 rounded-[var(--radius-sm)] border border-[var(--border)] bg-[var(--bg-base)] px-2 py-1.5 font-mono text-xs text-[var(--text-primary)]"
          />
        </label>
        <label className="flex min-w-[160px] flex-1 flex-col gap-1 text-[10px] font-semibold uppercase tracking-wide text-[var(--text-tertiary)]">
          Agent filter
          <input
            type="search"
            placeholder="e.g. risk, derivatives…"
            value={agent}
            onChange={(event) => {
              setOffset(0);
              setAgent(event.target.value);
            }}
            className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[var(--bg-base)] px-2 py-1.5 text-xs text-[var(--text-primary)]"
          />
        </label>
        <button
          type="button"
          className="ml-auto rounded-[var(--radius-sm)] border border-[var(--border)] px-3 py-1.5 text-xs text-[var(--text-secondary)] hover:border-[var(--accent-cyan)] hover:text-[var(--accent-cyan)]"
          onClick={() => {
            setScoreMin(0);
            setScoreMax(100);
            setOutcome("all");
            setSince("30d");
            setAgent("");
            setOffset(0);
          }}
        >
          Reset filters
        </button>
      </div>

      <p className="text-[11px] text-[var(--text-tertiary)]">
        Showing {rows.length} of {data?.total ?? 0} in range
        {query.isFetching ? " · Refreshing…" : ""}
      </p>

      {rows.length === 0 ? (
        <EmptyState
          title="No journal rows yet"
          description="Once `signal_history` captures embedded signals from the RAG writer, decisions appear here with post-trade outcomes."
        />
      ) : (
        <div className="overflow-x-auto rounded-[var(--radius-md)] border border-[var(--border)]">
          <table className="w-full min-w-[880px] border-collapse text-left text-xs">
            <thead className="bg-[var(--bg-elevated)]/80 text-[10px] font-semibold uppercase tracking-wide text-[var(--text-tertiary)]">
              <tr>
                <th className="px-3 py-2">Time</th>
                <th className="px-3 py-2">Symbol</th>
                <th className="px-3 py-2">Score</th>
                <th className="px-3 py-2">Action</th>
                <th className="px-3 py-2">Entry</th>
                <th className="px-3 py-2">Outcome</th>
                <th className="px-3 py-2">Summary</th>
                <th className="w-10 px-3 py-2" aria-label="Expand" />
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => {
                const open = expanded.has(row.signalId);
                return (
                  <FragmentRow
                    key={row.signalId}
                    row={row}
                    open={open}
                    onToggle={() => toggle(row.signalId)}
                  />
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {data?.hasMore === true && data.nextOffset !== null ? (
        <div className="flex justify-center">
          <button
            type="button"
            className="rounded-[var(--radius-sm)] border border-[var(--accent-cyan)]/50 bg-[var(--bg-elevated)] px-4 py-2 text-xs font-semibold text-[var(--accent-cyan)] hover:bg-[var(--bg-elevated)]/80"
            onClick={() => setOffset(data.nextOffset ?? 0)}
          >
            Load more
          </button>
        </div>
      ) : null}
    </div>
  );
}

function FragmentRow(props: {
  row: DecisionJournalEntry;
  open: boolean;
  onToggle: () => void;
}): ReactElement {
  const { row, open, onToggle } = props;
  const ts = row.timestamp.slice(0, 19).replace("T", " ");

  return (
    <>
      <tr className="border-t border-[var(--border)] bg-[var(--bg-base)]/40 hover:bg-[var(--bg-elevated)]/30">
        <td className="whitespace-nowrap px-3 py-2 font-mono text-[11px] text-[var(--text-secondary)]">
          {ts}
        </td>
        <td className="px-3 py-2">
          <span className="inline-flex items-center gap-2">
            <AssetLogo symbol={row.asset} size="sm" />
            <span className="font-semibold text-[var(--text-primary)]">{row.asset}</span>
          </span>
        </td>
        <td className="max-w-[200px] px-3 py-2 text-[11px] text-[var(--text-secondary)]">
          {row.scoreBreakdown}
        </td>
        <td className="px-3 py-2">
          <span
            className={cn(
              "rounded px-1.5 py-0.5 font-mono text-[10px] uppercase",
              row.actionTaken === "executed" && "bg-emerald-500/15 text-emerald-300",
              row.actionTaken === "skipped" && "bg-slate-500/15 text-slate-300",
              row.actionTaken === "rejected" && "bg-rose-500/15 text-rose-300",
            )}
          >
            {row.actionTaken}
          </span>
        </td>
        <td className="whitespace-nowrap px-3 py-2 font-mono text-[11px] text-[var(--text-secondary)]">
          {row.entryPrice}
        </td>
        <td
          className={cn(
            "whitespace-nowrap px-3 py-2 font-mono text-[11px] tabular-nums",
            outcome_class(row.outcomePct ?? undefined),
          )}
        >
          {format_pct(row.outcomePct)}
        </td>
        <td className="max-w-[320px] px-3 py-2 text-[11px] leading-snug text-[var(--text-secondary)]">
          {row.reasoningSummary.length > 0 ? row.reasoningSummary : "—"}
        </td>
        <td className="px-1 py-2 text-center">
          <button
            type="button"
            className="rounded border border-transparent px-2 py-1 text-[10px] text-[var(--accent-cyan)] hover:border-[var(--accent-cyan)]/40"
            aria-expanded={open}
            onClick={onToggle}
          >
            {open ? "▾" : "▸"}
          </button>
        </td>
      </tr>
      {open ? (
        <tr className="border-t border-[var(--border)] bg-[var(--bg-elevated)]/25">
          <td colSpan={8} className="px-4 py-4">
            <JournalDetail row={row} />
          </td>
        </tr>
      ) : null}
    </>
  );
}

function JournalDetail(props: { row: DecisionJournalEntry }): ReactElement {
  const { row } = props;
  const oh = row.outcomeHorizons;

  return (
    <div className="grid gap-4 text-xs text-[var(--text-secondary)] md:grid-cols-2">
      <div className="space-y-2">
        <h3 className="text-[10px] font-bold uppercase tracking-wide text-[var(--text-tertiary)]">
          Signal
        </h3>
        <p className="font-mono text-[11px] text-[var(--text-primary)]">{row.signalId}</p>
        <p>
          <span className="text-[var(--text-tertiary)]">Decision:</span>{" "}
          <span className="font-semibold text-[var(--text-primary)]">{row.decision}</span>
        </p>
        <p>
          <span className="text-[var(--text-tertiary)]">Primary agent:</span>{" "}
          {row.primaryAgent ?? "—"}
        </p>
        {row.confidenceTier !== null ? (
          <p>
            <span className="text-[var(--text-tertiary)]">Confidence tier:</span>{" "}
            {row.confidenceTier}
            {row.pipelineConfidence !== null ? ` (${row.pipelineConfidence.toFixed(2)})` : ""}
          </p>
        ) : null}
        {row.exitReason !== null ? (
          <p>
            <span className="text-[var(--text-tertiary)]">Exit:</span> {row.exitReason}
          </p>
        ) : null}
      </div>
      <div className="space-y-2">
        <h3 className="text-[10px] font-bold uppercase tracking-wide text-[var(--text-tertiary)]">
          Outcomes (1h / 4h / 24h)
        </h3>
        <div className="flex flex-wrap gap-3 font-mono text-[11px] tabular-nums">
          <span className={outcome_class(oh.pct1h ?? undefined)}>1h {format_pct(oh.pct1h)}</span>
          <span className={outcome_class(oh.pct4h ?? undefined)}>4h {format_pct(oh.pct4h)}</span>
          <span className={outcome_class(oh.pct24h ?? undefined)}>24h {format_pct(oh.pct24h)}</span>
        </div>
      </div>
      <div className="md:col-span-2">
        <h3 className="mb-2 text-[10px] font-bold uppercase tracking-wide text-[var(--text-tertiary)]">
          Risk Manager
        </h3>
        <div className="rounded border border-[var(--border)] bg-[var(--bg-base)]/50 p-3">
          <p>
            <span className={row.riskManager.vetoed ? "text-rose-400" : "text-emerald-400"}>
              {row.riskManager.vetoed ? "VETO" : "CLEAR"}
            </span>
            {" · "}
            {row.riskManager.reason}
          </p>
        </div>
      </div>
      <div className="md:col-span-2">
        <h3 className="mb-2 text-[10px] font-bold uppercase tracking-wide text-[var(--text-tertiary)]">
          Gates
        </h3>
        {row.gates.length === 0 ? (
          <p className="italic text-[var(--text-tertiary)]">No structured gate events on record.</p>
        ) : (
          <ul className="space-y-1">
            {row.gates.map((g) => (
              <li key={g.name} className="font-mono text-[11px]">
                <span className={g.fired ? "text-amber-400" : "text-slate-400"}>
                  {g.fired ? "FIRED" : "ok"}
                </span>{" "}
                {g.name}
                {g.detail ? ` — ${g.detail}` : ""}
              </li>
            ))}
          </ul>
        )}
      </div>
      <div className="md:col-span-2">
        <h3 className="mb-2 text-[10px] font-bold uppercase tracking-wide text-[var(--text-tertiary)]">
          Agent votes
        </h3>
        <div className="overflow-x-auto">
          <table className="w-full min-w-[640px] border-collapse text-left text-[11px]">
            <thead className="text-[10px] uppercase text-[var(--text-tertiary)]">
              <tr>
                <th className="py-1 pr-2">Agent</th>
                <th className="py-1 pr-2">State</th>
                <th className="py-1 pr-2">Score</th>
                <th className="py-1 pr-2">Dir</th>
                <th className="py-1 pr-2">Veto</th>
              </tr>
            </thead>
            <tbody>
              {row.agentVerdicts.length === 0 ? (
                <tr>
                  <td colSpan={5} className="py-2 italic text-[var(--text-tertiary)]">
                    No agent_verdicts on this row — back-fill from provenance or metadata in writers.
                  </td>
                </tr>
              ) : (
                row.agentVerdicts.map((agent_row) => (
                  <tr key={agent_row.agentName} className="border-t border-[var(--border)]/60">
                    <td className="py-1 pr-2 font-medium text-[var(--text-primary)]">
                      {agent_row.agentName}
                    </td>
                    <td className="py-1 pr-2">{agent_row.state}</td>
                    <td className="py-1 pr-2 tabular-nums">
                      {agent_row.score}/{agent_row.maxScore}
                    </td>
                    <td className="py-1 pr-2">{agent_row.direction}</td>
                    <td className="py-1 pr-2">{agent_row.veto ? "yes" : ""}</td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
