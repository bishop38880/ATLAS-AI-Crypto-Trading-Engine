import { Link } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { type ReactElement } from "react";

import {
  fetch_asset_inspector_json,
  map_asset_inspector_payload,
  type AssetInspectorPayload,
  type ConfluenceScoreHistoryPoint,
  type DerivativesFundingPoint,
  type DerivativesOiPoint,
} from "../../lib/asset-inspector";
import { calculate_ratio_change_display } from "../../lib/calculate-ratio-change-display";
import { calculate_sparkline_polyline_points } from "../../lib/calculate_sparkline_polyline";
import { CONFLUENCE_SCORE_CAP } from "../../lib/confluence-score-constants";
import { formatDecisionGlyph } from "../../lib/format-decision-glyph";
import { format_last_compact_from_iso } from "../../lib/format-relative-age";
import { format_usd_compact_display } from "../../lib/format-usd-compact";
import {
  calculate_exchange_flow_section_open,
  calculate_obti_interpretation,
  calculate_options_flow_section_open,
  map_signal_detail_payload,
  type AgentBreakdownCellView,
  type SignalDetailViewModel,
} from "../../lib/signal-detail-mapper";
import { apiUrl } from "../../lib/url";
import { ConfluenceThresholdScoreBar } from "../ui/ConfluenceThresholdScoreBar";
import { ScoreBar } from "../ui/ScoreBar";
import { Skeleton } from "../ui/Skeleton";
import { EmptyState } from "../ui/EmptyState";
import { AssetAskAtlasSection } from "./AssetAskAtlasSection";
import { TradingViewPanel } from "./TradingViewPanel";

export interface SignalAssetDetailPageProps {
  asset: string;
}

async function fetch_signal_detail_json(asset: string): Promise<unknown> {
  const slug = encodeURIComponent(asset);
  const response = await fetch(apiUrl(`/api/signals/${slug}`), {
    headers: { Accept: "application/json" },
  });
  if (!response.ok) {
    throw new Error(`signal_detail_failed_${response.status}`);
  }
  return response.json() as Promise<unknown>;
}

function HeroSection(props: { model: SignalDetailViewModel }): ReactElement {
  const { model } = props;
  const decision_glyph = formatDecisionGlyph(model.decision);
  const change_copy = calculate_ratio_change_display(model.change24h);
  const norm_pct = Math.min(100, Math.max(0, model.normalizedScore));

  const gate_glyph = model.passesGate ? "✓" : "✗";
  const gate_tone = model.passesGate ? "text-[var(--success)]" : "text-[var(--danger)]";

  return (
    <section aria-live="polite" className="rounded-[var(--radius-lg)] border border-[var(--border)] bg-[var(--bg-surface)] p-5">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <Link
            to="/"
            className="mb-2 mr-3 inline-flex rounded-[var(--radius-sm)] text-[11px] font-black uppercase tracking-wide text-[var(--accent-cyan)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--accent-cyan)]"
          >
            ← Dashboard
          </Link>
          <Link
            to="/signals"
            className="mb-2 inline-flex rounded-[var(--radius-sm)] text-[11px] font-black uppercase tracking-wide text-[var(--accent-cyan)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--accent-cyan)]"
          >
            Feed
          </Link>
          <p className="font-mono text-xl font-black text-[var(--text-primary)]">
            {model.asset}/USDT
          </p>
          <p className="mt-2 flex flex-wrap items-center gap-2 font-mono text-sm tabular-nums text-[var(--text-secondary)]">
            <span className="text-[var(--text-primary)]">{format_usd_compact_display(model.price)}</span>
            <span aria-hidden className="text-[var(--text-tertiary)]">
              |
            </span>
            <span className={change_copy.glyph === "▲" ? "text-[var(--buy)]" : change_copy.glyph === "▼" ? "text-[var(--sell)]" : ""}>
              <span aria-hidden>{change_copy.glyph}</span> {change_copy.label}
            </span>
            <span aria-hidden className="text-[var(--text-tertiary)]">
              |
            </span>
            <span>
              Cycle {model.cycleNumber.toLocaleString("en-US")}{" "}
              <span className="text-[var(--text-tertiary)]">|</span> {format_last_compact_from_iso(model.cycleTs)}
            </span>
          </p>
        </div>

        <div className="grid min-w-[280px] max-w-xl flex-1 grid-cols-1 gap-3 rounded-[var(--radius-md)] border border-[var(--border-hover)] bg-[var(--bg-elevated)]/60 p-4 md:grid-cols-2">
          <div className="space-y-2 border-[var(--border)] pb-3 md:border-0 md:pb-0 md:pr-3 md:border-r">
            <p className="text-[10px] font-black uppercase tracking-wide text-[var(--text-tertiary)]">Decision</p>
            <p className="font-mono text-lg font-black text-[var(--text-primary)]">
              <span aria-hidden>{decision_glyph}</span> {model.decision}
            </p>
            <p className="text-xs text-[var(--text-secondary)] tabular-nums">
              conf {(model.confidence).toFixed(2)}
            </p>
          </div>
          <div className="space-y-2">
            <p className="text-[10px] font-black uppercase tracking-wide text-[var(--text-tertiary)]">Score</p>
            <p className="font-mono text-lg font-black tabular-nums text-[var(--accent-cyan)]">
              {model.totalScore} / {CONFLUENCE_SCORE_CAP}
            </p>
            <ConfluenceThresholdScoreBar value={model.totalScore} label="Ladder" size="sm" />
            <div className="space-y-1">
              <div className="flex justify-between text-[10px] text-[var(--text-tertiary)]">
                <span>Normalized</span>
                <span className="tabular-nums">{norm_pct}%</span>
              </div>
              <div className="h-2 w-full overflow-hidden rounded-full bg-[var(--bg-base)]">
                <div className="h-full rounded-full bg-[var(--accent-cyan)]" style={{ width: `${norm_pct}%` }} />
              </div>
            </div>
            <p className={`font-mono text-xs font-bold ${gate_tone}`}>
              <span aria-hidden>{gate_glyph}</span> Gate {model.passesGate ? "passes" : "blocked"} (≥{model.gateThreshold})
            </p>
            <p className="text-xs text-[var(--text-secondary)]">
              LLM: <span className="font-mono text-[var(--text-primary)]">{model.llmTierLabel ?? "—"}</span>
            </p>
          </div>
        </div>
      </div>
    </section>
  );
}

function CategorySection(props: { model: SignalDetailViewModel }): ReactElement {
  const { model } = props;
  const rows = [
    { key: "derivatives", label: "Derivatives", value: model.categoryScores.derivatives },
    { key: "onchain", label: "On-chain", value: model.categoryScores.onchain },
    { key: "technical", label: "Technical", value: model.categoryScores.technical },
    { key: "sentiment", label: "Sentiment", value: model.categoryScores.sentiment },
    { key: "marketContext", label: "Market context", value: model.categoryScores.marketContext },
  ];

  return (
    <section aria-live="polite" className="space-y-3 rounded-[var(--radius-lg)] border border-[var(--border)] bg-[var(--bg-surface)] p-5">
      <h2 className="text-[11px] font-black uppercase tracking-wide text-[var(--text-tertiary)]">
        Category breakdown
      </h2>
      <div className="space-y-4">
        {rows.map((row) => {
          const cap = Math.round(model.categoryMaxScores[row.key] ?? 40);
          return <ScoreBar key={row.key} label={row.label} max={cap} value={row.value} size="lg" />;
        })}
      </div>
    </section>
  );
}

function ObtiSection(props: { detail: SignalDetailViewModel["obtiDetail"] }): ReactElement {
  const { detail } = props;

  if (detail === null) {
    return (
      <section className="rounded-[var(--radius-lg)] border border-[var(--border)] bg-[var(--bg-surface)] p-5">
        <h2 className="text-[11px] font-black uppercase tracking-wide text-[var(--text-tertiary)]">
          OBTI — Order-Book Toxicity (flattened summary)
        </h2>
        <p className="mt-2 text-sm text-[var(--text-secondary)]">
          No OBTI block on this snapshot yet. PROMETHEUS publishes the flattened summary onto the Redis signal when the execution agent finishes warming.
        </p>
      </section>
    );
  }

  const interpretation = calculate_obti_interpretation(detail);
  const dominant_side = detail.obtiAsk >= detail.obtiBid ? "ask" : "bid";
  const dominant_val = dominant_side === "ask" ? detail.obtiAsk : detail.obtiBid;
  const sum = detail.obtiBid + detail.obtiAsk + 1e-9;
  const bid_bar_pct = (detail.obtiBid / sum) * 100;
  const ask_bar_pct = (detail.obtiAsk / sum) * 100;
  const spark = calculate_sparkline_polyline_points(detail.history.length > 0 ? detail.history : [dominant_val]);

  const level_token = detail.level.toUpperCase();
  const side_glyph = dominant_side === "ask" ? "▼" : "▲";

  let book_age_label = "—";
  if (detail.lastBookUpdateMs > 0 && detail.lastBookUpdateMs < 3_600_000) {
    book_age_label = `${(detail.lastBookUpdateMs / 1000).toFixed(1)}s ago`;
  }

  return (
    <section aria-live="polite" className="space-y-4 rounded-[var(--radius-lg)] border border-[var(--border)] bg-[var(--bg-surface)] p-5">
      <h2 className="text-[11px] font-black uppercase tracking-wide text-[var(--text-tertiary)]">
        OBTI — Order-Book Toxicity (flattened summary)
      </h2>

      <div className="grid gap-4 md:grid-cols-2">
        <div className="space-y-2 text-sm text-[var(--text-secondary)]">
          <p>
            Current:{" "}
            <span className="font-mono text-[var(--text-primary)]">
              {detail.level}
              {detail.side ? ` (${detail.side}-side)` : ""}
            </span>{" "}
            <span aria-hidden>{detail.level === "moderate" ? "◐" : detail.level === "extreme" ? "●" : "○"}</span>
          </p>
          <p className="font-mono text-xs">
            MAD-adaptive thresholds: moderate≥{detail.moderateThreshold.toFixed(3)} extreme≥{detail.extremeThreshold.toFixed(3)}
          </p>
          <p>
            Direction:{" "}
            <span className="font-mono tabular-nums text-[var(--text-primary)]">
              bid: {detail.obtiBid.toFixed(2)} | ask: {detail.obtiAsk.toFixed(2)}
            </span>{" "}
            ← dominant {dominant_side}
          </p>
          <p className="font-mono text-xs tabular-nums">
            Samples {detail.samples} / {detail.minSamples}{" "}
            {detail.isWarmingUp ? <span className="text-[var(--warning)]">(warming)</span> : null}
          </p>
          <p className="font-mono text-xs">Last book update: {book_age_label}</p>
        </div>

        <div className="space-y-3">
          <div className="space-y-2">
            <p className="text-[10px] font-bold uppercase text-[var(--text-tertiary)]">Side pressure</p>
            <div className="space-y-1">
              <div className="flex items-center gap-2 text-xs font-mono tabular-nums">
                <span className="w-8">BID</span>
                <div className="h-2 flex-1 overflow-hidden rounded-full bg-[var(--bg-base)]">
                  <div className="h-full rounded-full bg-[var(--buy)]" style={{ width: `${bid_bar_pct}%` }} />
                </div>
                <span>{detail.obtiBid.toFixed(2)}</span>
              </div>
              <div className="flex items-center gap-2 text-xs font-mono tabular-nums">
                <span className="w-8">ASK</span>
                <div className="h-2 flex-1 overflow-hidden rounded-full bg-[var(--bg-base)]">
                  <div className="h-full rounded-full bg-[var(--sell)]" style={{ width: `${ask_bar_pct}%` }} />
                </div>
                <span>{detail.obtiAsk.toFixed(2)}</span>
              </div>
            </div>
            <p className="text-center font-mono text-[10px] text-[var(--warning)]">
              <span aria-hidden>{side_glyph}</span> {level_token}
            </p>
          </div>

          <div>
            <p className="mb-1 text-[10px] font-bold uppercase text-[var(--text-tertiary)]">
              Dominant-side OBTI (recent window)
            </p>
            <svg viewBox="0 0 100 32" className="w-full text-[var(--accent-cyan)]" aria-hidden>
              <polyline
                fill="none"
                stroke="currentColor"
                strokeWidth="1.5"
                points={spark.pointsAttr}
              />
            </svg>
          </div>
        </div>
      </div>

      <aside className="rounded-[var(--radius-md)] border border-[var(--border-hover)] bg-[var(--bg-elevated)]/60 p-3 text-sm text-[var(--text-secondary)]">
        <p className="text-[10px] font-black uppercase tracking-wide text-[var(--text-tertiary)]">Interpretation</p>
        <p className="mt-1">{interpretation}</p>
      </aside>
    </section>
  );
}

function FracSection(props: { frac: SignalDetailViewModel["fracDiff"] }): ReactElement {
  const { frac } = props;
  if (frac === null) {
    return (
      <section className="rounded-[var(--radius-lg)] border border-dashed border-[var(--border-hover)] bg-[var(--bg-surface)] p-5">
        <h2 className="text-[11px] font-black uppercase tracking-wide text-[var(--text-tertiary)]">
          Fractional differentiation
        </h2>
        <p className="mt-2 text-sm text-[var(--text-secondary)]">
          Fractional differentiation telemetry is not published on this snapshot yet.
        </p>
      </section>
    );
  }

  const station_glyph = frac.isStationary ? "✓" : "○";
  const mem_pct = Math.min(100, Math.max(0, frac.memoryPreserved * 100));

  return (
    <section aria-live="polite" className="space-y-3 rounded-[var(--radius-lg)] border border-[var(--border)] bg-[var(--bg-surface)] p-5">
      <h2 className="text-[11px] font-black uppercase tracking-wide text-[var(--text-tertiary)]">
        Fractional differentiation
      </h2>
      <dl className="grid gap-2 font-mono text-sm text-[var(--text-secondary)] md:grid-cols-2">
        <div>
          <dt className="text-[10px] uppercase text-[var(--text-tertiary)]">d* (optimal order)</dt>
          <dd className="text-[var(--text-primary)]">{frac.optimalOrder.toFixed(2)}</dd>
        </div>
        <div>
          <dt className="text-[10px] uppercase text-[var(--text-tertiary)]">ADF p-value</dt>
          <dd className="text-[var(--text-primary)] tabular-nums">
            {frac.adfPValue.toFixed(3)} <span aria-hidden>{station_glyph}</span>{" "}
            {frac.isStationary ? "Stationary" : "Non-stationary"}
          </dd>
        </div>
        <div className="md:col-span-2">
          <dt className="text-[10px] uppercase text-[var(--text-tertiary)]">Memory preserved</dt>
          <dd className="mt-1 text-[var(--text-primary)] tabular-nums">
            {(frac.memoryPreserved * 100).toFixed(1)}%
            <div className="mt-1 h-2 w-full max-w-md overflow-hidden rounded-full bg-[var(--bg-base)]">
              <div className="h-full rounded-full bg-[var(--accent-teal)]" style={{ width: `${mem_pct}%` }} />
            </div>
          </dd>
        </div>
        <div className="md:col-span-2">
          <dt className="text-[10px] uppercase text-[var(--text-tertiary)]">Last calibrated</dt>
          <dd className="text-[var(--text-primary)]">{frac.lastCalibrated.length > 0 ? frac.lastCalibrated : "—"}</dd>
        </div>
      </dl>
      <aside className="text-xs text-[var(--text-secondary)]">
        <p className="font-bold text-[var(--text-tertiary)]">What this means</p>
        <p className="mt-1">
          Price series differentiated at d={frac.optimalOrder.toFixed(2)} (not full d=1 returns). {(frac.memoryPreserved * 100).toFixed(0)}% of price memory preserved — regime context intact. Lower d = more memory; higher d = more stationarity.
        </p>
      </aside>
    </section>
  );
}

function OptionsSection(props: { flow: SignalDetailViewModel["optionsFlow"] }): ReactElement {
  const { flow } = props;
  const open = calculate_options_flow_section_open(flow);

  if (!open || flow === null) {
    return (
      <section className="rounded-[var(--radius-lg)] border border-[var(--border)] bg-[var(--bg-surface)] p-5">
        <h2 className="text-[11px] font-black uppercase tracking-wide text-[var(--text-tertiary)]">
          Options intelligence
        </h2>
        <p className="mt-2 text-sm text-[var(--text-secondary)]">
          Options intelligence not yet wired — awaiting Deribit-style provider data with a closed circuit breaker.
        </p>
      </section>
    );
  }

  return (
    <section aria-live="polite" className="space-y-3 rounded-[var(--radius-lg)] border border-[var(--border)] bg-[var(--bg-surface)] p-5">
      <header className="flex flex-wrap items-center gap-2">
        <h2 className="text-[11px] font-black uppercase tracking-wide text-[var(--text-tertiary)]">
          Options intelligence
        </h2>
        <span className="rounded border border-[var(--border-hover)] px-2 py-0.5 font-mono text-[10px] text-[var(--text-secondary)]">
          {flow.provider.name ?? "provider"}
        </span>
      </header>
      <dl className="grid gap-2 font-mono text-sm text-[var(--text-secondary)] md:grid-cols-2">
        <div>
          <dt className="text-[10px] uppercase text-[var(--text-tertiary)]">DVOL</dt>
          <dd className="tabular-nums text-[var(--text-primary)]">{flow.dvol ?? "—"}</dd>
        </div>
        <div>
          <dt className="text-[10px] uppercase text-[var(--text-tertiary)]">ATM IV</dt>
          <dd className="tabular-nums text-[var(--text-primary)]">
            {flow.atmIv !== null ? `${flow.atmIv.toFixed(1)}% annualised` : "—"}
          </dd>
        </div>
        <div>
          <dt className="text-[10px] uppercase text-[var(--text-tertiary)]">25D Skew</dt>
          <dd className="tabular-nums text-[var(--text-primary)]">{flow.skew25d ?? "—"}</dd>
        </div>
        <div>
          <dt className="text-[10px] uppercase text-[var(--text-tertiary)]">VRP</dt>
          <dd className="tabular-nums text-[var(--text-primary)]">{flow.vrp ?? "—"}</dd>
        </div>
        <div className="md:col-span-2">
          <dt className="text-[10px] uppercase text-[var(--text-tertiary)]">Call/Put OI</dt>
          <dd className="text-[var(--text-primary)]">{flow.callPutOiRatio ?? "—"}</dd>
        </div>
        <div className="md:col-span-2">
          <dt className="text-[10px] uppercase text-[var(--text-tertiary)]">Regime</dt>
          <dd className="text-[var(--text-primary)]">{flow.regime ?? "—"}</dd>
        </div>
        <div className="md:col-span-2">
          <dt className="text-[10px] uppercase text-[var(--text-tertiary)]">Unusual activity</dt>
          <dd className="text-[var(--text-primary)]">{flow.unusualActivity ?? "None detected"}</dd>
        </div>
      </dl>
    </section>
  );
}

function ExchangeFlowSection(props: { flow: SignalDetailViewModel["exchangeFlow"] }): ReactElement {
  const { flow } = props;
  const open = calculate_exchange_flow_section_open(flow);

  if (!open || flow === null) {
    return (
      <section className="rounded-[var(--radius-lg)] border border-[var(--border)] bg-[var(--bg-surface)] p-5">
        <h2 className="text-[11px] font-black uppercase tracking-wide text-[var(--text-tertiary)]">
          Exchange flows
        </h2>
        <p className="mt-2 text-sm text-[var(--text-secondary)]">
          Exchange-flow intelligence not yet wired — awaiting CryptoQuant-style provider data with a closed breaker.
        </p>
      </section>
    );
  }

  return (
    <section aria-live="polite" className="space-y-3 rounded-[var(--radius-lg)] border border-[var(--border)] bg-[var(--bg-surface)] p-5">
      <header className="flex flex-wrap items-center gap-2">
        <h2 className="text-[11px] font-black uppercase tracking-wide text-[var(--text-tertiary)]">
          Exchange flows
        </h2>
        <span className="rounded border border-[var(--border-hover)] px-2 py-0.5 font-mono text-[10px] text-[var(--text-secondary)]">
          {flow.provider.name ?? "provider"}
        </span>
      </header>
      <dl className="grid gap-2 font-mono text-sm text-[var(--text-secondary)] md:grid-cols-2">
        <div className="md:col-span-2">
          <dt className="text-[10px] uppercase text-[var(--text-tertiary)]">Net flow (24h)</dt>
          <dd className="text-[var(--text-primary)]">{flow.netFlow24h ?? "—"}</dd>
        </div>
        <div>
          <dt className="text-[10px] uppercase text-[var(--text-tertiary)]">Flow Z-score</dt>
          <dd className="tabular-nums text-[var(--text-primary)]">{flow.flowZScore ?? "—"}</dd>
        </div>
        <div>
          <dt className="text-[10px] uppercase text-[var(--text-tertiary)]">Flow trend</dt>
          <dd className="text-[var(--text-primary)]">{flow.flowTrend ?? "—"}</dd>
        </div>
        <div className="md:col-span-2">
          <dt className="text-[10px] uppercase text-[var(--text-tertiary)]">Stablecoin reserves</dt>
          <dd className="text-[var(--text-primary)]">{flow.stablecoinReserves ?? "—"}</dd>
        </div>
        <div className="md:col-span-2">
          <dt className="text-[10px] uppercase text-[var(--text-tertiary)]">Signal</dt>
          <dd className="text-[var(--text-primary)]">{flow.signal ?? "—"}</dd>
        </div>
      </dl>
    </section>
  );
}

function format_sub_signal_display(cell: Record<string, unknown>): string {
  const v = cell.value;
  if (v !== undefined && v !== null && String(v).length > 0) {
    return String(v);
  }
  const flag = cell.flag !== undefined && cell.flag !== null ? String(cell.flag) : "";
  const bits: string[] = [];
  for (const [k, val] of Object.entries(cell)) {
    if (k === "value" || k === "flag") {
      continue;
    }
    bits.push(`${k}=${String(val)}`);
  }
  const core = bits.length > 0 ? bits.join(" · ") : "—";
  return flag.length > 0 ? `${core} (${flag})` : core;
}

function AgentScorecardSection(props: {
  cells: Record<string, AgentBreakdownCellView>;
}): ReactElement {
  const entries = Object.entries(props.cells).sort(([a], [b]) => a.localeCompare(b));
  if (entries.length === 0) {
    return (
      <section className="rounded-[var(--radius-lg)] border border-dashed border-[var(--border-hover)] bg-[var(--bg-surface)] p-5">
        <h2 className="text-[11px] font-black uppercase tracking-wide text-[var(--text-tertiary)]">
          220-point scorecard — agent matrix
        </h2>
        <p className="mt-2 text-sm text-[var(--text-secondary)]">
          No agent breakdown on this snapshot. When Redis carries{" "}
          <span className="font-mono text-[var(--text-primary)]">agent_breakdown</span>, every sub-signal shows here.
        </p>
      </section>
    );
  }

  return (
    <section className="space-y-3 rounded-[var(--radius-lg)] border border-[var(--border)] bg-[var(--bg-surface)] p-5">
      <h2 className="text-[11px] font-black uppercase tracking-wide text-[var(--text-tertiary)]">
        220-point scorecard — agents and sub-signals
      </h2>
      <div className="space-y-3">
        {entries.map(([name, cell]) => (
          <details
            key={name}
            className="rounded-[var(--radius-md)] border border-[var(--border-hover)] bg-[var(--bg-elevated)]/40 open:border-[var(--accent-cyan)]/40"
          >
            <summary className="cursor-pointer list-none px-3 py-3 [&::-webkit-details-marker]:hidden">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <span className="font-mono text-sm font-bold text-[var(--text-primary)]">{name}</span>
                <span className="font-mono text-xs tabular-nums text-[var(--accent-cyan)]">
                  {Math.round(cell.score)} / {Math.round(cell.maxScore)} pts
                  {cell.veto ? (
                    <span className="ml-2 text-[var(--danger)]">VETO</span>
                  ) : null}
                </span>
              </div>
              {cell.explanation.length > 0 ? (
                <p className="mt-2 text-xs text-[var(--text-secondary)]">{cell.explanation}</p>
              ) : null}
            </summary>
            <div className="border-t border-[var(--border)] px-3 py-3">
              <p className="text-[10px] font-bold uppercase text-[var(--text-tertiary)]">Sub-signals</p>
              <dl className="mt-2 grid gap-2 font-mono text-[11px] text-[var(--text-secondary)] md:grid-cols-2">
                {Object.entries(cell.subSignals).map(([label, sub]) => (
                  <div key={label} className="rounded border border-[var(--border-hover)] bg-[var(--bg-base)]/80 p-2">
                    <dt className="text-[var(--text-tertiary)]">{label}</dt>
                    <dd className="mt-1 text-[var(--text-primary)]">{format_sub_signal_display(sub)}</dd>
                  </div>
                ))}
              </dl>
              {cell.convergences.length > 0 ? (
                <p className="mt-2 text-xs text-[var(--success)]">
                  <span className="font-bold">Convergences: </span>
                  {cell.convergences.join(" · ")}
                </p>
              ) : null}
              {cell.risks.length > 0 ? (
                <p className="mt-2 text-xs text-[var(--warning)]">
                  <span className="font-bold">Risks: </span>
                  {cell.risks.join(" · ")}
                </p>
              ) : null}
            </div>
          </details>
        ))}
      </div>
    </section>
  );
}

function build_series_points(values: number[], y_min: number, y_hi: number): string {
  if (values.length === 0) {
    return "";
  }
  const span = Math.max(1e-6, y_hi - y_min);
  const w = 100;
  const h = 48;
  const pad_y = 2;
  const usable_h = h - 2 * pad_y;
  return values
    .map((v, i) => {
      const x = values.length === 1 ? w / 2 : (i / (values.length - 1)) * w;
      const t = (v - y_min) / span;
      const y = pad_y + usable_h * (1 - t);
      return `${x.toFixed(2)},${y.toFixed(2)}`;
    })
    .join(" ");
}

function ConfluenceHistorySection(props: { points: ConfluenceScoreHistoryPoint[] }): ReactElement {
  const { points } = props;
  const has_pillars = points.some(
    (p) =>
      p.derivatives !== null ||
      p.onchain !== null ||
      p.technical !== null ||
      p.sentiment !== null ||
      p.marketContext !== null,
  );

  if (points.length < 2) {
    return (
      <section className="rounded-[var(--radius-lg)] border border-[var(--border)] bg-[var(--bg-surface)] p-5">
        <h2 className="text-[11px] font-black uppercase tracking-wide text-[var(--text-tertiary)]">
          Confluence history (~30d)
        </h2>
        <p className="mt-2 text-sm text-[var(--text-secondary)]">
          Need at least two RAG snapshots in{" "}
          <span className="font-mono text-[var(--text-primary)]">signal_history</span> for a trend. Pillar splits are
          stored when new rows are written (category scores in metadata).
        </p>
      </section>
    );
  }

  const totals = points.map((p) => p.rawScore);
  const y_hi = Math.max(220, Math.max(...totals, 1));
  const y_min = 0;
  const total_pts = build_series_points(totals, y_min, y_hi);

  const deriv =
    has_pillars && points.every((p) => p.derivatives !== null)
      ? build_series_points(
          points.map((p) => p.derivatives ?? 0),
          y_min,
          y_hi,
        )
      : "";
  const chain =
    has_pillars && points.every((p) => p.onchain !== null)
      ? build_series_points(
          points.map((p) => p.onchain ?? 0),
          y_min,
          y_hi,
        )
      : "";

  return (
    <section className="space-y-3 rounded-[var(--radius-lg)] border border-[var(--border)] bg-[var(--bg-surface)] p-5">
      <header>
        <h2 className="text-[11px] font-black uppercase tracking-wide text-[var(--text-tertiary)]">
          Confluence history (RAG, ~30d)
        </h2>
        <p className="mt-1 text-xs text-[var(--text-secondary)]">
          Total raw score (220 ladder) vs time. {has_pillars ? "Pillar overlays when every row carries category metadata." : "Pillar overlays appear once category metadata backfills from new writes."}
        </p>
      </header>
      <svg
        viewBox="0 0 100 48"
        className="w-full text-[var(--accent-cyan)]"
        role="img"
        aria-label="Confluence score history"
      >
        <polyline fill="none" stroke="currentColor" strokeWidth="1.2" points={total_pts} />
        {deriv.length > 0 ? (
          <polyline fill="none" stroke="#38bdf8" strokeWidth="0.9" opacity={0.85} points={deriv} />
        ) : null}
        {chain.length > 0 ? (
          <polyline fill="none" stroke="#a78bfa" strokeWidth="0.9" opacity={0.85} points={chain} />
        ) : null}
      </svg>
      <div className="flex flex-wrap gap-3 text-[10px] font-mono text-[var(--text-tertiary)]">
        <span>
          <span className="text-[var(--accent-cyan)]">■</span> total raw
        </span>
        {deriv.length > 0 ? (
          <span>
            <span className="text-sky-400">■</span> derivatives pillar
          </span>
        ) : null}
        {chain.length > 0 ? (
          <span>
            <span className="text-violet-400">■</span> on-chain pillar
          </span>
        ) : null}
        <span className="tabular-nums">
          n={points.length} from {format_last_compact_from_iso(points[0]?.timestamp ?? "")} →{" "}
          {format_last_compact_from_iso(points[points.length - 1]?.timestamp ?? "")}
        </span>
      </div>
    </section>
  );
}

function RecentDecisionsSection(props: { rows: AssetInspectorPayload["recentDecisions"] }): ReactElement {
  const { rows } = props;
  return (
    <section className="rounded-[var(--radius-lg)] border border-[var(--border)] bg-[var(--bg-surface)] p-5">
      <h2 className="text-[11px] font-black uppercase tracking-wide text-[var(--text-tertiary)]">
        Last 10 decisions (signal_history)
      </h2>
      {rows.length === 0 ? (
        <p className="mt-2 text-sm text-[var(--text-secondary)]">No RAG rows for this symbol yet.</p>
      ) : (
        <div className="mt-3 overflow-x-auto">
          <table className="w-full min-w-[640px] border-collapse text-left text-xs">
            <thead>
              <tr className="border-b border-[var(--border)] text-[10px] uppercase text-[var(--text-tertiary)]">
                <th className="py-2 pr-2">Time</th>
                <th className="py-2 pr-2">Decision</th>
                <th className="py-2 pr-2 tabular-nums">Raw</th>
                <th className="py-2 pr-2">Outcome</th>
                <th className="py-2 pr-2 tabular-nums">PnL %</th>
              </tr>
            </thead>
            <tbody className="font-mono text-[var(--text-secondary)]">
              {rows.map((row) => (
                <tr key={row.signalId} className="border-b border-[var(--border-hover)]/60">
                  <td className="py-2 pr-2 whitespace-nowrap">{format_last_compact_from_iso(row.timestamp)}</td>
                  <td className="py-2 pr-2 text-[var(--text-primary)]">{row.decision}</td>
                  <td className="py-2 pr-2 tabular-nums">{Math.round(row.rawScore)}</td>
                  <td className="py-2 pr-2">
                    {row.outcomeLabel === null ? "—" : row.outcomeLabel}
                  </td>
                  <td className="py-2 pr-2 tabular-nums">
                    {row.pnlPct === null ? "—" : `${row.pnlPct >= 0 ? "+" : ""}${row.pnlPct.toFixed(2)}`}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function build_scalar_series(values: number[]): string {
  if (values.length === 0) {
    return "";
  }
  const vmin = Math.min(...values);
  const vmax = Math.max(...values);
  const span = Math.max(1e-9, vmax - vmin);
  const w = 100;
  const h = 40;
  const pad = 2;
  const uh = h - 2 * pad;
  return values
    .map((v, i) => {
      const x = values.length === 1 ? w / 2 : (i / (values.length - 1)) * w;
      const t = (v - vmin) / span;
      const y = pad + uh * (1 - t);
      return `${x.toFixed(2)},${y.toFixed(2)}`;
    })
    .join(" ");
}

function DerivativesChartsSection(props: {
  funding: DerivativesFundingPoint[];
  oi: DerivativesOiPoint[];
  source: AssetInspectorPayload["derivativesSource"];
}): ReactElement {
  const { funding, oi, source } = props;
  const fund_pts = build_scalar_series(funding.map((f) => f.fundingRate));
  const oi_pts = build_scalar_series(oi.map((x) => x.oiUsd));

  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <section className="rounded-[var(--radius-lg)] border border-[var(--border)] bg-[var(--bg-surface)] p-5">
        <h2 className="text-[11px] font-black uppercase tracking-wide text-[var(--text-tertiary)]">
          Funding rate history
        </h2>
        <p className="mt-1 text-[10px] text-[var(--text-tertiary)]">Source: {source} (OKX MCP Redis cache when warm)</p>
        {funding.length === 0 ? (
          <p className="mt-2 text-sm text-[var(--text-secondary)]">No cached funding bars for this symbol.</p>
        ) : (
          <svg viewBox="0 0 100 40" className="mt-2 w-full text-[var(--buy)]" aria-hidden>
            {fund_pts.length > 0 ? (
              <polyline fill="none" stroke="currentColor" strokeWidth="1.2" points={fund_pts} />
            ) : null}
          </svg>
        )}
      </section>
      <section className="rounded-[var(--radius-lg)] border border-[var(--border)] bg-[var(--bg-surface)] p-5">
        <h2 className="text-[11px] font-black uppercase tracking-wide text-[var(--text-tertiary)]">
          Open interest
        </h2>
        <p className="mt-1 text-[10px] text-[var(--text-tertiary)]">Source: {source}</p>
        {oi.length === 0 ? (
          <p className="mt-2 text-sm text-[var(--text-secondary)]">No cached OI bars for this symbol.</p>
        ) : (
          <svg viewBox="0 0 100 40" className="mt-2 w-full text-[var(--accent-teal)]" aria-hidden>
            {oi_pts.length > 0 ? (
              <polyline fill="none" stroke="currentColor" strokeWidth="1.2" points={oi_pts} />
            ) : null}
          </svg>
        )}
      </section>
    </div>
  );
}

export function SignalAssetDetailPage(props: SignalAssetDetailPageProps): ReactElement {
  const { asset } = props;

  const { data, isLoading, error } = useQuery({
    queryKey: ["signals", "detail", asset],
    queryFn: () => fetch_signal_detail_json(asset),
    staleTime: 5_000,
    select: map_signal_detail_payload,
  });

  const inspector_query = useQuery({
    queryKey: ["signals", "inspector", asset, 30],
    queryFn: async () => fetch_asset_inspector_json(asset, 30),
    staleTime: 15_000,
    select: map_asset_inspector_payload,
  });

  if (isLoading) {
    return (
      <section className="space-y-4" aria-busy="true" aria-label="Signal detail loading">
        <Skeleton className="block h-40 w-full" height={160} />
        <Skeleton className="block h-32 w-full" height={128} />
      </section>
    );
  }

  if (error !== null || data === null || data === undefined) {
    return (
      <EmptyState
        title="Could not load signal detail"
        description="The polaris signal snapshot for this asset is missing or the API returned an error."
      />
    );
  }

  const inspector = inspector_query.data;
  const score_hist = inspector?.scoreHistory ?? [];
  const recent = inspector?.recentDecisions ?? [];
  const funding_hist = inspector?.fundingHistory ?? [];
  const oi_hist = inspector?.oiHistory ?? [];
  const deriv_src = inspector?.derivativesSource ?? "none";

  return (
    <div className="space-y-6">
      <HeroSection model={data} />
      <TradingViewPanel
        asset_base={data.asset}
        subtitle="Executive ladder is anchored on 30m — shown here in 30m for alignment with ATLAS."
      />
      <AgentScorecardSection cells={data.agentBreakdown} />
      <CategorySection model={data} />
      <ConfluenceHistorySection points={score_hist} />
      <RecentDecisionsSection rows={recent} />
      <DerivativesChartsSection funding={funding_hist} oi={oi_hist} source={deriv_src} />
      <AssetAskAtlasSection asset={data.asset} />
      <ObtiSection detail={data.obtiDetail} />
      <FracSection frac={data.fracDiff} />
      <OptionsSection flow={data.optionsFlow} />
      <ExchangeFlowSection flow={data.exchangeFlow} />
    </div>
  );
}
