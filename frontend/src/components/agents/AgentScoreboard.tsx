import { Fragment, memo, useMemo, type KeyboardEvent } from "react";

import type { AgentScoreboardDisplayRow } from "../../lib/calculate_agent_scoreboard_rows";
import { calculate_confluence_subtotal_score } from "../../lib/calculate_agent_scoreboard_rows";
import { CONFLUENCE_SCORE_CAP } from "../../lib/confluence-score-constants";
import { AgentTypeBadge } from "../ui/AgentTypeBadge";
import { Badge } from "../ui/Badge";
import { CommandCard } from "../ui/CommandCard";
import { DataValue } from "../ui/DataValue";
import { StatusPill } from "../ui/StatusPill";

export interface AgentScoreboardProps {
  rows: AgentScoreboardDisplayRow[];
  onOpenRow: (row: AgentScoreboardDisplayRow) => void;
}

function interpolate_meter_rgb(ratio: number): string {
  const t = Math.min(1, Math.max(0, ratio));
  const rLow = { r: 239, g: 83, b: 80 };
  const rMid = { r: 255, g: 167, b: 38 };
  const rHigh = { r: 52, g: 211, b: 153 };
  const lerp = (
    a: { r: number; g: number; b: number },
    b: { r: number; g: number; b: number },
    x: number,
  ) => ({
    r: Math.round(a.r + (b.r - a.r) * x),
    g: Math.round(a.g + (b.g - a.g) * x),
    b: Math.round(a.b + (b.b - a.b) * x),
  });
  let rgb: { r: number; g: number; b: number };
  if (t < 1 / 3) {
    rgb = lerp(rLow, rMid, t * 3);
  } else if (t < 2 / 3) {
    rgb = lerp(rMid, rHigh, (t - 1 / 3) * 3);
  } else {
    rgb = rHigh;
  }
  return `rgb(${rgb.r} ${rgb.g} ${rgb.b})`;
}

function InlineScoreMeter({ value, max }: { value: number; max: number }) {
  const safe_max = max > 0 ? max : 1;
  const ratio = value / safe_max;
  const fill = interpolate_meter_rgb(ratio);

  return (
    <div
      className="h-2 w-full overflow-hidden rounded-full border border-slate-800/80 bg-slate-950/80 ring-1 ring-inset ring-white/[0.03]"
      aria-hidden
    >
      <div
        className="h-full rounded-full shadow-[0_0_8px_rgba(0,0,0,0.35)] transition-[width] duration-500 ease-out"
        style={{ width: `${ratio * 100}%`, backgroundColor: fill }}
      />
    </div>
  );
}

function status_dot_level(status: AgentScoreboardDisplayRow["status"]): "healthy" | "degraded" | "offline" | "error" {
  if (status === "HEALTHY") {
    return "healthy";
  }
  if (status === "DEGRADED" || status === "TIMEOUT") {
    return "degraded";
  }
  if (status === "OFFLINE") {
    return "offline";
  }
  return "error";
}

function status_operator_label(status: AgentScoreboardDisplayRow["status"]): string {
  if (status === "TIMEOUT") {
    return "TIMEOUT";
  }
  if (status === "HEALTHY") {
    return "HEALTHY";
  }
  if (status === "DEGRADED") {
    return "DEGRADED";
  }
  if (status === "OFFLINE") {
    return "OFFLINE";
  }
  return "UNKNOWN";
}

function AgentScoreboardInner({ rows, onOpenRow }: AgentScoreboardProps) {
  const subtotal_score = useMemo(() => calculate_confluence_subtotal_score(rows), [rows]);

  return (
    <CommandCard
      accent="cyan"
      title="Agent scoreboard"
      subtitle={`CONFLUENCE rows feed the ${CONFLUENCE_SCORE_CAP}-point ladder. Overlays resize positions only — never sum overlays into confluence.`}
    >
      <div className="overflow-x-auto rounded-md border border-slate-800">
        <table className="data-table data-table-sticky" aria-label="Agent scoreboard">
          <thead>
            <tr>
              <th scope="col">Agent</th>
              <th scope="col" className="text-right">
                Score
              </th>
              <th scope="col" className="text-right">
                Max
              </th>
              <th scope="col">Type</th>
              <th scope="col">Status</th>
              <th scope="col">Meter</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row, index) => {
              const prev = rows[index - 1];
              const show_subtotal_break =
                row.catalog.role === "OVERLAY" && prev !== undefined && prev.catalog.role === "CONFLUENCE";

              const open_handler = () => {
                onOpenRow(row);
              };

              const on_row_key_down = (event: KeyboardEvent<HTMLTableRowElement>) => {
                if (event.key === "Enter" || event.key === " ") {
                  event.preventDefault();
                  open_handler();
                }
              };

              const aria_open = `Open ${row.catalog.label} detail`;

              return (
                <Fragment key={row.catalog.id}>
                  {show_subtotal_break ? (
                    <tr className="data-table-subtotal-row">
                      <td colSpan={2} className="font-medium text-slate-400">
                        Confluence subtotal
                      </td>
                      <td className="text-right font-data font-bold text-cyan-300">
                        {subtotal_score} / {CONFLUENCE_SCORE_CAP}
                      </td>
                      <td colSpan={3} className="text-slate-500">
                        ladder ≤ {CONFLUENCE_SCORE_CAP}
                      </td>
                    </tr>
                  ) : null}

                  <tr
                    tabIndex={0}
                    role="button"
                    className="data-row-interactive"
                    aria-label={aria_open}
                    title={row.catalog.role === "VETO" ? row.explanation : undefined}
                    onClick={open_handler}
                    onKeyDown={on_row_key_down}
                  >
                    <td className="font-medium text-slate-100">{row.catalog.label}</td>
                    <td className="text-right font-data">
                      {row.catalog.role === "VETO" ? (
                        row.vetoBlocking ? (
                          <span className="font-semibold text-red-400">VETO</span>
                        ) : (
                          <span className="font-semibold text-emerald-400">PASS</span>
                        )
                      ) : (
                        <DataValue value={row.score} size="sm" />
                      )}
                    </td>
                    <td className="text-right font-data text-slate-400">
                      {row.catalog.role === "VETO" ? "—" : row.max}
                    </td>
                    <td>
                      <AgentTypeBadge role={row.catalog.role} />
                    </td>
                    <td>
                      <div className="flex flex-wrap items-center gap-2">
                        <StatusPill
                          level={status_dot_level(row.status)}
                          label={status_operator_label(row.status)}
                        />
                        {row.catalog.role === "SHADOW" ? (
                          <Badge variant="shadow" className="text-[9px]">
                            SHADOW MODE
                          </Badge>
                        ) : null}
                      </div>
                    </td>
                    <td className="min-w-[7rem]">
                      {row.catalog.role === "CONFLUENCE" || row.catalog.role === "OVERLAY" ? (
                        <InlineScoreMeter value={row.score} max={row.max} />
                      ) : (
                        <span className="text-slate-500">—</span>
                      )}
                    </td>
                  </tr>
                </Fragment>
              );
            })}
          </tbody>
        </table>
      </div>

      <footer className="mt-3 space-y-1 border-t border-slate-800/80 pt-3 text-[11px] text-slate-400">
        <p className="font-data font-semibold tabular-nums text-slate-200">
          Confluence score (sum CONFLUENCE only) → {subtotal_score} / {CONFLUENCE_SCORE_CAP}
        </p>
        <p>Displayed overlays change sizing, not the published confluence ladder.</p>
      </footer>
    </CommandCard>
  );
}

export const AgentScoreboard = memo(AgentScoreboardInner);
