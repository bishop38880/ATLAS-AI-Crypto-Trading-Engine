import { useEffect, useMemo, useRef } from "react";

import { useAgentHistoryQuery } from "../../hooks/useAgentHistoryQuery";
import type { AgentScoreboardDisplayRow } from "../../lib/calculate_agent_scoreboard_rows";
import { normalize_agent_match_token } from "../../lib/agent-scoreboard-catalog";
import { calculate_sparkline_polyline_points } from "../../lib/calculate_sparkline_polyline";
import { CONFLUENCE_SCORE_CAP } from "../../lib/confluence-score-constants";
import { AgentTypeBadge } from "../ui/AgentTypeBadge";

function format_cycle_timestamp(iso: string | undefined): string {
  if (iso === undefined || iso.length === 0) {
    return "—";
  }
  const parsed = Date.parse(iso);
  if (!Number.isFinite(parsed)) {
    return iso;
  }
  return new Date(parsed).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function format_vote_label(direction: string | null | undefined): string {
  if (direction === null || direction === undefined) {
    return "—";
  }
  const trimmed = direction.trim();
  return trimmed.length > 0 ? trimmed : "—";
}

function correctness_cell(correct: boolean | null | undefined): { text: string; title: string } {
  if (correct === true) {
    return { text: "Yes", title: "Directional vote matched the realised trade outcome." };
  }
  if (correct === false) {
    return { text: "No", title: "Directional vote disagreed with how the labelled trade resolved." };
  }
  return {
    text: "—",
    title: "Not scored: pending outcome, neutral vote, hold/no-trade, or SCRATCH.",
  };
}

export interface AgentDetailDrawerProps {
  row: AgentScoreboardDisplayRow | null;
  asset: string;
  onClose: () => void;
}

export function AgentDetailDrawer({ row, asset, onClose }: AgentDetailDrawerProps) {
  const close_button_ref = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    const on_key_down = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        onClose();
      }
    };
    window.addEventListener("keydown", on_key_down);
    return () => {
      window.removeEventListener("keydown", on_key_down);
    };
  }, [onClose]);

  useEffect(() => {
    if (row !== null) {
      queueMicrotask(() => {
        close_button_ref.current?.focus();
      });
    }
  }, [row]);

  const api_agent_name =
    row !== null
      ? row.liveName !== undefined && row.liveName !== null && row.liveName.trim().length > 0
        ? row.liveName.trim()
        : normalize_agent_match_token(row.catalog.matchTokens[0] ?? "")
      : "";

  const history_query = useAgentHistoryQuery({
    agentWsName: api_agent_name.length > 0 ? api_agent_name : null,
    asset,
    enabled: row !== null && api_agent_name.length > 0,
  });

  const spark_values = useMemo(() => {
    const cycles = history_query.data ?? [];
    return cycles.slice(-20).map((cycle) => cycle.totalScore);
  }, [history_query.data]);

  const sparkline = useMemo(() => calculate_sparkline_polyline_points(spark_values), [spark_values]);

  const latest_cycle = history_query.data?.[history_query.data.length - 1];

  const calibration_rollup = useMemo(() => {
    const cycles = history_query.data ?? [];
    let resolved = 0;
    let wins = 0;
    for (const cycle of cycles) {
      if (typeof cycle.correct === "boolean") {
        resolved += 1;
        if (cycle.correct) {
          wins += 1;
        }
      }
    }
    return { resolved, wins };
  }, [history_query.data]);

  const decision_rows = useMemo(() => {
    const cycles = history_query.data ?? [];
    return [...cycles].reverse();
  }, [history_query.data]);

  if (row === null) {
    return null;
  }

  return (
    <div
      className="drawer-scrim"
      role="presentation"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) {
          onClose();
        }
      }}
    >
      <aside
        className="drawer-panel drawer-panel-enter"
        role="dialog"
        aria-modal="true"
        aria-labelledby="agent-drawer-title"
      >
        <header className="drawer-panel-header">
          <div className="min-w-0">
            <h2 id="agent-drawer-title" className="truncate text-lg font-semibold text-slate-50">
              {row.catalog.label}
            </h2>
            <p className="mt-1 flex flex-wrap items-center gap-2 truncate text-xs text-slate-500">
              <span className="font-data">Redis {row.liveName ?? "not linked"}</span>
              <AgentTypeBadge role={row.catalog.role} />
            </p>
          </div>
          <button
            ref={close_button_ref}
            type="button"
            aria-label="Close agent detail drawer"
            className="btn-pill px-3 py-1.5 text-xs"
            onClick={onClose}
          >
            Close
          </button>
        </header>

        <div className="space-y-5 px-4 py-4 text-sm text-slate-400">
          <section aria-live="polite">
            <h3 className="section-label">Explanation</h3>
            <p className="mt-2 leading-relaxed text-slate-200">
              {row.explanation !== undefined && row.explanation.length > 0 ? row.explanation : "No explanation cached for this pulse yet."}
            </p>
          </section>

          <section>
            <h3 className="section-label">
              Voting history ({asset.trim().toUpperCase() || "—"})
            </h3>
            <p className="mt-2 text-xs leading-relaxed text-[var(--text-tertiary)]">
              Last {history_query.data?.length ?? 0} decisions from signal history. “Right?” compares this agent&apos;s directional vote to how the emitted trade resolved once{" "}
              <span className="text-[var(--text-secondary)]">outcome_label</span> is WIN or LOSS (LONG vs SHORT vs hold-aware).
            </p>
            {calibration_rollup.resolved > 0 ? (
              <p className="mt-2 text-xs font-medium text-[var(--text-primary)]">
                Resolved calibration sample: {calibration_rollup.wins}/{calibration_rollup.resolved} directional calls matched realised outcomes.
              </p>
            ) : (
              <p className="mt-2 text-xs text-[var(--text-tertiary)]">No resolved WIN/LOSS outcomes yet for this sample — scores stay pending until PROMETHEUS writes outcomes.</p>
            )}
            {history_query.isLoading ? (
              <p className="mt-3 text-xs text-[var(--text-tertiary)]">Loading decision ledger…</p>
            ) : decision_rows.length === 0 ? (
              <p className="mt-3 text-xs text-[var(--text-tertiary)]">No rows in signal_history for this asset yet.</p>
            ) : (
              <div className="mt-3 overflow-x-auto rounded-md border border-slate-800">
                <table className="data-table data-table-compact min-w-[26rem] text-xs">
                  <thead>
                    <tr>
                      <th>When</th>
                      <th>Final call</th>
                      <th>Agent vote</th>
                      <th>Outcome</th>
                      <th className="text-right">Right?</th>
                    </tr>
                  </thead>
                  <tbody className="font-data text-slate-200">
                    {decision_rows.map((cycle, idx) => {
                      const cell = correctness_cell(cycle.correct);
                      const outcome_display =
                        cycle.outcomeLabel !== null && cycle.outcomeLabel !== undefined && cycle.outcomeLabel.length > 0
                          ? cycle.outcomeLabel
                          : "—";
                      return (
                        <tr key={`${cycle.signalId ?? "row"}-${idx}`}>
                          <td className="whitespace-nowrap text-slate-400">{format_cycle_timestamp(cycle.timestamp)}</td>
                          <td className="max-w-[7rem] truncate" title={cycle.finalDecision}>
                            {cycle.finalDecision !== undefined && cycle.finalDecision.length > 0 ? cycle.finalDecision : "—"}
                          </td>
                          <td className="capitalize">{format_vote_label(cycle.agentDirection)}</td>
                          <td>{outcome_display}</td>
                          <td className="text-right">
                            <span
                              title={cell.title}
                              className={
                                cycle.correct === true
                                  ? "text-emerald-400"
                                  : cycle.correct === false
                                    ? "text-rose-400"
                                    : "text-[var(--text-tertiary)]"
                              }
                            >
                              {cell.text}
                            </span>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </section>

          <section>
            <h3 className="section-label">Live ladder slice</h3>
            <dl className="stat-tile mt-2 grid gap-2 font-data text-xs tabular-nums">
              <div className="flex justify-between gap-3">
                <dt className="text-[var(--text-tertiary)]">Score</dt>
                <dd>{row.catalog.role === "VETO" ? (row.vetoBlocking ? "▼ ■ VETO" : "▲ ✓ PASS") : row.score}</dd>
              </div>
              <div className="flex justify-between gap-3">
                <dt className="text-[var(--text-tertiary)]">Max</dt>
                <dd>{row.catalog.role === "VETO" ? "—" : row.max}</dd>
              </div>
              <div className="flex justify-between gap-3">
                <dt className="text-[var(--text-tertiary)]">Operator ceiling</dt>
                <dd>{CONFLUENCE_SCORE_CAP} pts ladder</dd>
              </div>
            </dl>
          </section>

          <section>
            <h3 className="section-label">Feature scores (REST)</h3>
            {latest_cycle?.featureScores !== undefined && Object.keys(latest_cycle.featureScores).length > 0 ? (
              <ul className="mt-2 grid gap-1 font-data text-xs">
                {Object.entries(latest_cycle.featureScores).map(([key, value]) => (
                  <li key={key} className="flex justify-between gap-3 tabular-nums">
                    <span className="text-[var(--text-tertiary)]">{key}</span>
                    <span>{value}</span>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="mt-2 text-xs text-[var(--text-tertiary)]">No per-feature breakdown included in history payloads yet.</p>
            )}
          </section>

          <section>
            <h3 className="section-label">Score trend</h3>
            {spark_values.length === 0 ? (
              <p className="mt-2 text-xs text-[var(--text-tertiary)]">No history samples yet (last 20 cycles).</p>
            ) : (
              <svg viewBox="0 0 100 32" className="mt-2 h-24 w-full text-[var(--accent-cyan)]" role="img" aria-label="Last twenty ladder totals sparkline">
                <polyline
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="1.5"
                  points={sparkline.pointsAttr}
                />
              </svg>
            )}
          </section>
        </div>
      </aside>
    </div>
  );
}
