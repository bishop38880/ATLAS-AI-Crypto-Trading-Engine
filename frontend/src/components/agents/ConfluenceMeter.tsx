import { useMemo } from "react";

import { CONFLUENCE_GATE_THRESHOLD_DEFAULT, CONFLUENCE_SCORE_CAP } from "../../lib/confluence-score-constants";
import { FALLBACK_ACTIVE_33_BASES_ORDER } from "../../lib/dashboard-universe";
import type { ScoresPayload } from "../../store/index";
import { AssetLogo } from "../AssetLogo";
import { Badge } from "../ui/Badge";
import { CommandCard } from "../ui/CommandCard";
import { ConfluenceThresholdScoreBar } from "../ui/ConfluenceThresholdScoreBar";
import { formatDecisionGlyph } from "../../lib/format-decision-glyph";

export interface ConfluenceMeterProps {
  asset: string;
  onAssetChange: (asset: string) => void;
  snapshot: ScoresPayload | undefined;
}

interface CategoryPart {
  label: string;
  value: number;
  color: string;
}

interface Slice extends CategoryPart {
  startAngle: number;
  endAngle: number;
}

function polar(cx: number, cy: number, radius: number, angle_deg: number) {
  const rad = ((angle_deg - 90) * Math.PI) / 180;
  return { x: cx + radius * Math.cos(rad), y: cy + radius * Math.sin(rad) };
}

function describe_donut_segment(
  cx: number,
  cy: number,
  radius_inner: number,
  radius_outer: number,
  start_angle: number,
  end_angle: number,
): string {
  const large_arc = end_angle - start_angle <= 180 ? 0 : 1;
  const outer_start = polar(cx, cy, radius_outer, start_angle);
  const outer_end = polar(cx, cy, radius_outer, end_angle);
  const inner_end = polar(cx, cy, radius_inner, end_angle);
  const inner_start = polar(cx, cy, radius_inner, start_angle);
  return [
    `M ${outer_start.x.toFixed(2)} ${outer_start.y.toFixed(2)}`,
    `A ${radius_outer} ${radius_outer} 0 ${large_arc} 1 ${outer_end.x.toFixed(2)} ${outer_end.y.toFixed(2)}`,
    `L ${inner_end.x.toFixed(2)} ${inner_end.y.toFixed(2)}`,
    `A ${radius_inner} ${radius_inner} 0 ${large_arc} 0 ${inner_start.x.toFixed(2)} ${inner_start.y.toFixed(2)}`,
    `Z`,
  ].join(" ");
}

export function ConfluenceMeter({ asset, onAssetChange, snapshot }: ConfluenceMeterProps) {
  const category_parts = useMemo<CategoryPart[]>(() => {
    if (!snapshot) {
      return [];
    }
    const scores = snapshot.categoryScores;
    return [
      { label: "Derivatives", value: Math.max(0, scores.derivatives), color: "var(--accent-cyan)" },
      { label: "On-chain", value: Math.max(0, scores.onchain), color: "var(--accent-teal)" },
      { label: "Technical", value: Math.max(0, scores.technical), color: "var(--accent-violet)" },
      { label: "Sentiment", value: Math.max(0, scores.sentiment), color: "var(--accent-amber)" },
      { label: "Context", value: Math.max(0, scores.marketContext), color: "var(--success)" },
    ];
  }, [snapshot]);

  const donut_slices = useMemo<Slice[]>(() => {
    const total_weight = category_parts.reduce((sum, cell) => sum + cell.value, 0);
    if (total_weight <= 0) {
      return [];
    }
    let cursor = 0;
    return category_parts.map((cell) => {
      const sweep = (cell.value / total_weight) * 360;
      const slice: Slice = {
        ...cell,
        startAngle: cursor,
        endAngle: cursor + sweep,
      };
      cursor += sweep;
      return slice;
    });
  }, [category_parts]);

  const asset_options = useMemo(() => [...FALLBACK_ACTIVE_33_BASES_ORDER].slice(0, 24), []);

  const ladder_display = snapshot !== undefined ? Math.min(snapshot.totalScore, CONFLUENCE_SCORE_CAP) : 0;
  const gate_threshold =
    snapshot !== undefined &&
    typeof snapshot.gateThreshold === "number" &&
    Number.isFinite(snapshot.gateThreshold)
      ? snapshot.gateThreshold
      : CONFLUENCE_GATE_THRESHOLD_DEFAULT;
  const gate_pass_visual = snapshot !== undefined ? ladder_display >= gate_threshold : false;

  const llm_invoke_line =
    snapshot !== undefined ? (
      snapshot.passesGate && snapshot.r1LlmInvoked !== false ? (
        <span>
          R1 LLM invoked {snapshot.llmModelLabel}
          {snapshot.llmLatencySeconds !== null ? (
            <span className="tabular-nums"> {snapshot.llmLatencySeconds}s</span>
          ) : null}
        </span>
      ) : (
        <span className="text-[var(--danger)]">gated: R1 LLM skipped</span>
      )
    ) : (
      <span className="text-[var(--text-tertiary)]">Awaiting ladder data</span>
    );

  const glyph = snapshot !== undefined ? formatDecisionGlyph(snapshot.decision) : "—";

  const badge_variant =
    snapshot !== undefined
      ? snapshot.decision.includes("Sell") || snapshot.decision === "Strong Sell"
        ? "bear"
        : snapshot.decision.includes("Buy") || snapshot.decision === "Strong Buy"
          ? "bull"
          : "hold"
      : "hold";

  const snapshot_asset_label =
    !snapshot || snapshot.asset.length === 0 ? asset : snapshot.asset;

  return (
    <CommandCard
      accent="teal"
      title="Asset ladder"
      subtitle={`Snapshot asset ${snapshot_asset_label}`}
      headerActions={
        <span className="flex items-center gap-2">
          <AssetLogo symbol={asset} size="md" />
          <select
            value={asset}
            aria-label="Select asset for ladder"
            onChange={(event) => {
              onAssetChange(event.target.value);
            }}
            className="input-control min-w-[8rem] py-1.5 text-sm"
          >
            {asset_options.map((symbol) => (
              <option key={symbol} value={symbol}>
                {symbol}
              </option>
            ))}
          </select>
        </span>
      }
    >
      <div aria-live="polite" className="space-y-4">
        {!snapshot ? (
          <div className="empty-state">
            <p className="mx-auto max-w-md">
              Waiting for a ladder snapshot for{" "}
              <span className="font-data text-slate-200">{asset}</span>.
            </p>
            <p className="mx-auto mt-2 max-w-lg text-xs text-slate-500">
              Appears when the pipeline publishes this symbol to Redis. Until then, meter values from other assets are
              hidden.
            </p>
          </div>
        ) : (
          <>
            <div className="flex flex-wrap items-start gap-5">
              <div className="stat-tile min-w-[220px] flex-1">
                <p className="stat-tile-label text-center">Ladder total</p>
                <p className="metric-glow stat-tile-value text-center text-5xl">
                  {ladder_display}
                </p>
                <p className="text-center text-xs text-slate-500">out of {CONFLUENCE_SCORE_CAP}</p>
                <div className="mt-3">
                  <ConfluenceThresholdScoreBar value={ladder_display} label="Ladder" size="md" animated />
                </div>
                <div className="mt-3 flex justify-center">
                  <Badge variant={badge_variant} className="inline-flex items-center gap-1 px-3 py-1 text-xs">
                    <span aria-hidden className="font-data">
                      {glyph}
                    </span>
                    {snapshot.decision}
                  </Badge>
                </div>
              </div>

              <div className="flex flex-col items-center gap-3">
                <svg viewBox="0 0 120 120" className="h-32 w-32" aria-label="Category wedge donut">
                  <circle cx="60" cy="60" r="46" fill="none" stroke="#1e293b" strokeWidth="18" />
                  {donut_slices.map((slice) => (
                    <path
                      key={slice.label}
                      d={describe_donut_segment(60, 60, 34, 52, slice.startAngle, slice.endAngle)}
                      fill={slice.color}
                      opacity={0.92}
                    />
                  ))}
                </svg>
                <ul className="grid w-full min-w-[11rem] gap-1 text-[11px] text-slate-400">
                  {category_parts.map((cell) => (
                    <li key={cell.label} className="flex items-center gap-2">
                      <span
                        className="inline-block size-2 rounded-full ring-1 ring-white/10"
                        style={{ backgroundColor: cell.color }}
                        aria-hidden
                      />
                      <span>{cell.label}</span>
                      <span className="ml-auto font-data tabular-nums text-slate-200">{cell.value}</span>
                    </li>
                  ))}
                </ul>
              </div>
            </div>

            <footer className="space-y-1.5 border-t border-slate-800 pt-3 text-[11px] text-slate-400">
              <p className="flex flex-wrap items-center gap-2 font-data tabular-nums">
                <span>Gate ≥ {gate_threshold}</span>
                <span
                  className={
                    gate_pass_visual ? "gate-pill gate-pill-pass" : "gate-pill gate-pill-fail"
                  }
                >
                  {gate_pass_visual ? "PASS" : "FAIL"}
                </span>
              </p>
              <p>{llm_invoke_line}</p>
            </footer>
          </>
        )}
      </div>
    </CommandCard>
  );
}
