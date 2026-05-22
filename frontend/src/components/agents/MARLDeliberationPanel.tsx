import { memo, useMemo, useState } from "react";

import { useMarlLatestQuery } from "../../hooks/useMarlLatestQuery";
import { cn } from "../../lib/cn";
import { CONFLUENCE_SCORE_CAP } from "../../lib/confluence-score-constants";
import { Badge } from "../ui/Badge";
import { Button } from "../ui/Button";
import { CommandCard } from "../ui/CommandCard";
import { Skeleton } from "../ui/Skeleton";

export interface MARLDeliberationPanelProps {
  asset: string;
  snapshotTotal: number | null;
}

function revision_glyph_percent(pct: number): string {
  if (pct > 0.05) {
    return "▲";
  }
  if (pct < -0.05) {
    return "▼";
  }
  return "→";
}

function MARLDeliberationPanelInner({ asset, snapshotTotal }: MARLDeliberationPanelProps) {
  const [collapsed, set_collapsed] = useState(false);
  const query = useMarlLatestQuery(asset);

  const deliberation = query.data?.deliberation ?? null;
  const shows_placeholder = deliberation === null;

  const delta_pts = useMemo(() => {
    if (!deliberation) {
      return null;
    }
    return deliberation.netScoreAfter - deliberation.netScoreBefore;
  }, [deliberation]);

  return (
    <CommandCard
      accent="violet"
      title="MARL cooperative deliberation"
      subtitle={`Asset ${asset.trim().toUpperCase() || "—"} · cooperative revision layer`}
      headerActions={
        <>
          {query.data?.shadowMode === true ? (
            <Badge variant="shadow" className="shrink-0">
              SHADOW MODE
            </Badge>
          ) : null}
          <Button
            variant="pill"
            size="xs"
            aria-expanded={!collapsed}
            aria-controls="marl-deliberation-body"
            onClick={() => {
              set_collapsed((value) => !value);
            }}
          >
            {collapsed ? "Expand" : "Collapse"}
          </Button>
        </>
      }
    >
      {!collapsed ? (
        <div
          id="marl-deliberation-body"
          aria-live="polite"
          aria-atomic={false}
          className="space-y-4 text-xs text-slate-400"
        >
          {query.isPending && query.data === undefined ? (
            <div className="space-y-2" aria-busy="true">
              <Skeleton height={14} className="block w-2/3" />
              <Skeleton height={14} className="block w-full" />
              <Skeleton height={14} className="block w-4/5" />
            </div>
          ) : shows_placeholder ? (
            <p className="rounded-lg border border-dashed border-slate-700 bg-slate-950/40 px-4 py-3 leading-relaxed text-slate-400">
              MARL deliberation is in shadow mode. Enable{" "}
              <span className="font-data text-slate-300">ATLAS_MARL_ENABLED=true</span> after Phase F validation.
            </p>
          ) : deliberation !== null ? (
            <>
              <div className="stat-tile">
                <p className="stat-tile-label">Revision arc</p>
                <p className="mt-2 font-medium text-slate-200">
                  {deliberation.agentLabel}: Round 1 → Round 2
                </p>
                <p className="mt-2 font-data text-sm tabular-nums text-cyan-200/90">
                  {deliberation.round1Points} pts ─── {revision_glyph_percent(deliberation.revisionClampedPct)}{" "}
                  {deliberation.revisionClampedPct.toFixed(1)}% ───→ {deliberation.round2Points.toFixed(1)} pts
                </p>
              </div>

              <div>
                <p className="section-label">Shadow metrics injected</p>
                <ul className="mt-2 grid gap-2">
                  {deliberation.shadowMetrics.map((row) => (
                    <li key={row.label} className="marl-metric-row">
                      <span className="min-w-[140px] text-slate-500">{row.label}</span>
                      <span className="font-medium text-cyan-300">{row.value}</span>
                      {row.hint.length > 0 ? <span className="text-slate-500">[{row.hint}]</span> : null}
                    </li>
                  ))}
                </ul>
              </div>

              <div className="stat-tile font-data text-[11px] leading-relaxed text-slate-300">
                <p>Raw revision: {deliberation.revisionRawPct.toFixed(1)}%</p>
                <p>Clamped: {deliberation.revisionClampedPct.toFixed(1)}% (±15% ceiling applied)</p>
                <p>Checkpoint: {deliberation.checkpointName}</p>
              </div>

              <div className="rounded-lg border border-slate-800 bg-slate-900/50 px-3 py-2.5 text-[11px] text-slate-200">
                <p className="font-data tabular-nums">
                  Net score impact: {deliberation.netScoreBefore} → {deliberation.netScoreAfter}
                  {delta_pts !== null ? (
                    <span
                      className={cn(
                        delta_pts < 0 ? "text-red-400" : delta_pts > 0 ? "text-emerald-400" : "text-slate-400",
                      )}
                    >
                      {" "}
                      (Δ {delta_pts > 0 ? "+" : ""}
                      {delta_pts} pts)
                    </span>
                  ) : null}
                </p>
                <p className="mt-1">Verdict: {deliberation.verdict}</p>
                {snapshotTotal !== null ? (
                  <p className="mt-1 font-data tabular-nums text-slate-500">
                    Live ladder reference {snapshotTotal}/{CONFLUENCE_SCORE_CAP}
                  </p>
                ) : null}
              </div>
            </>
          ) : null}

          {query.isError ? (
            <p className="text-red-400" role="status">
              MARL REST poll failed — retrying on interval.
            </p>
          ) : null}
        </div>
      ) : (
        <p className="text-[11px] text-slate-500">Panel collapsed — expand to view deliberation trace.</p>
      )}
    </CommandCard>
  );
}

export const MARLDeliberationPanel = memo(MARLDeliberationPanelInner);
