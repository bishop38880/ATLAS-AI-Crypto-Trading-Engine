import { useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useState, type ReactElement } from "react";

import { apiUrl } from "../../lib/url";
import { cn } from "../../lib/cn";
import { CONFLUENCE_SCORE_CAP } from "../../lib/confluence-score-constants";
import {
  calculate_age_ms_from_iso,
  format_last_compact_from_iso,
} from "../../lib/format-relative-age";
import { useScoresStore, useSystemStore } from "../../store/index";
import { Badge } from "../ui/Badge";
import { CommandCard } from "../ui/CommandCard";
import { ConfluenceThresholdScoreBar } from "../ui/ConfluenceThresholdScoreBar";
import { EmptyState } from "../ui/EmptyState";
import { PageHeader } from "../ui/PageHeader";
import { Skeleton } from "../ui/Skeleton";
import { Spinner } from "../ui/Spinner";

interface RegimeClassificationRow {
  readonly label: string;
  readonly detail: string;
}

interface RegimeAdjustmentRow {
  readonly label: string;
  readonly value?: string;
  readonly detail: string;
}

interface RegimeTimelineSegment {
  readonly startTs: string;
  readonly endTs: string;
  readonly hmmRegime: string;
  readonly dashboardRegime: string;
  readonly startPriceUsd?: string | null;
}

interface RegimeControlCenterResponse {
  readonly asOf: string;
  readonly focusAsset: string;
  readonly signalAsset: string;
  readonly hmmRegime: string | null;
  readonly dashboardRegime: string;
  readonly regimeConfidence: number;
  readonly sinceUtc: string | null;
  readonly dwellLabel: string;
  readonly dwellStartedAt: string | null;
  readonly gateThreshold: number;
  readonly positionSizeModifier: number;
  readonly confidenceTier: string;
  readonly regimeProbabilities: Record<string, number>;
  readonly classificationRows: RegimeClassificationRow[];
  readonly adjustmentRows: RegimeAdjustmentRow[];
  readonly timeline: RegimeTimelineSegment[];
  readonly regimeSwitches7d: number;
  readonly historySamples7d: number;
  readonly noteDashboardRanging: string;
}

const REGIME_INFO_TOOLTIP =
  "POLARIS dashboard bucket maps HMM volatile and other non-trending states into RANGING — this page surfaces the underlying HMM label so a silent RANGING badge cannot hide volatile misclassification.";

const FRESHNESS_WARN_MS = 45_000;
const FRESHNESS_STALE_MS = 90_000;

function regime_glyph(hmm: string | null): string {
  if (hmm === null) {
    return "—";
  }
  const token = hmm.toLowerCase();
  if (token === "bull") {
    return "▲";
  }
  if (token === "bear") {
    return "▼";
  }
  if (token === "volatile") {
    return "~";
  }
  return "→";
}

function regime_hero_text_class(hmm: string | null): string {
  if (hmm === null) {
    return "text-slate-300";
  }
  const token = hmm.toLowerCase();
  if (token === "bull") {
    return "text-emerald-300";
  }
  if (token === "bear") {
    return "text-rose-300";
  }
  if (token === "volatile") {
    return "text-amber-300";
  }
  return "text-slate-200";
}

function regime_hero_accent(hmm: string | null): "cyan" | "amber" | "teal" | "none" {
  if (hmm === null) {
    return "none";
  }
  const token = hmm.toLowerCase();
  if (token === "bull") {
    return "teal";
  }
  if (token === "bear") {
    return "none";
  }
  if (token === "volatile") {
    return "amber";
  }
  return "cyan";
}

function regime_hero_shell_class(hmm: string | null): string {
  if (hmm === null) {
    return "";
  }
  const token = hmm.toLowerCase();
  if (token === "bull") {
    return "border-emerald-500/25 shadow-[0_0_40px_rgba(16,185,129,0.08)]";
  }
  if (token === "bear") {
    return "border-rose-500/25 border-t-rose-500/50 shadow-[0_0_40px_rgba(244,63,94,0.08)]";
  }
  if (token === "volatile") {
    return "border-amber-500/25 shadow-[0_0_40px_rgba(245,158,11,0.07)]";
  }
  return "";
}

function regime_hero_glow_class(hmm: string | null): string {
  if (hmm === null) {
    return "from-slate-500/10 to-transparent";
  }
  const token = hmm.toLowerCase();
  if (token === "bull") {
    return "from-emerald-500/20 via-emerald-600/5 to-transparent";
  }
  if (token === "bear") {
    return "from-rose-500/20 via-rose-600/5 to-transparent";
  }
  if (token === "volatile") {
    return "from-amber-500/18 via-amber-600/5 to-transparent";
  }
  return "from-cyan-500/10 to-transparent";
}

function detects_dashboard_mask(hmm: string | null, dashboard_regime: string): boolean {
  if (hmm === null) {
    return false;
  }
  return hmm.toLowerCase() === "volatile" && dashboard_regime.toUpperCase() === "RANGING";
}

function format_freshness_label(as_of_iso: string): string {
  const age_ms = calculate_age_ms_from_iso(as_of_iso);
  if (age_ms === null) {
    return "Unknown";
  }
  if (age_ms < 5_000) {
    return "Just now";
  }
  if (age_ms < 60_000) {
    return `${Math.floor(age_ms / 1000)}s ago`;
  }
  return format_last_compact_from_iso(as_of_iso);
}

function freshness_level(as_of_iso: string): "fresh" | "warn" | "stale" | "unknown" {
  const age_ms = calculate_age_ms_from_iso(as_of_iso);
  if (age_ms === null) {
    return "unknown";
  }
  if (age_ms >= FRESHNESS_STALE_MS) {
    return "stale";
  }
  if (age_ms >= FRESHNESS_WARN_MS) {
    return "warn";
  }
  return "fresh";
}

function freshness_tone_class(as_of_iso: string): string {
  const level = freshness_level(as_of_iso);
  if (level === "stale") {
    return "text-rose-400";
  }
  if (level === "warn") {
    return "text-amber-400";
  }
  if (level === "fresh") {
    return "text-cyan-300/90";
  }
  return "text-slate-500";
}

function freshness_dot_class(as_of_iso: string): string {
  const level = freshness_level(as_of_iso);
  if (level === "stale") {
    return "bg-rose-400";
  }
  if (level === "warn") {
    return "bg-amber-400";
  }
  if (level === "fresh") {
    return "bg-cyan-400";
  }
  return "bg-slate-500";
}

function regime_solid_class(hmm: string): string {
  const token = hmm.toLowerCase();
  if (token === "bull") {
    return "bg-emerald-600/90";
  }
  if (token === "bear") {
    return "bg-rose-700/85";
  }
  if (token === "volatile") {
    return "bg-amber-600/75";
  }
  return "bg-slate-600/80";
}

function regime_bar_class(hmm: string): string {
  const token = hmm.toLowerCase();
  if (token === "bull") {
    return "bg-emerald-500";
  }
  if (token === "bear") {
    return "bg-rose-600/90";
  }
  if (token === "volatile") {
    return "bg-amber-500/85";
  }
  return "bg-slate-500/80";
}

function hmm_badge_variant(
  hmm: string | null,
): "bull" | "bear" | "ranging" | "shadow" | "stale" {
  if (hmm === null) {
    return "stale";
  }
  const t = hmm.toLowerCase();
  if (t === "bull") {
    return "bull";
  }
  if (t === "bear") {
    return "bear";
  }
  if (t === "volatile") {
    return "ranging";
  }
  return "shadow";
}

function parse_percent_detail(detail: string): number | null {
  const match = detail.match(/([\d.]+)\s*%/);
  if (match === null) {
    return null;
  }
  const value = Number(match[1]);
  return Number.isFinite(value) ? value : null;
}

function parse_hmm_posterior_label(label: string): string | null {
  const match = label.match(/HMM posterior ·\s*(.+)/i);
  return match !== null ? match[1].trim().toLowerCase() : null;
}

function parse_pillar_weights(detail: string): Array<{ key: string; multiplier: number }> {
  const parts = detail.split(";").map((part) => part.trim()).filter(Boolean);
  const rows: Array<{ key: string; multiplier: number }> = [];
  for (const part of parts) {
    const match = part.match(/^([a-z_]+)[×x]([\d.]+)$/i);
    if (match === null) {
      continue;
    }
    const multiplier = Number(match[2]);
    if (!Number.isFinite(multiplier)) {
      continue;
    }
    rows.push({ key: match[1].replace(/_/g, " "), multiplier });
  }
  return rows;
}

function resolve_focus_asset_score(
  scores_by_asset: Map<string, { totalScore: number }>,
  focus_asset: string,
  signal_asset: string,
): number | null {
  const candidates = [
    signal_asset,
    focus_asset,
    `${focus_asset}USDT`,
    `${focus_asset}/USDT`,
  ]
    .map((value) => value.trim().toUpperCase())
    .filter(Boolean);

  for (const candidate of candidates) {
    for (const [asset, row] of scores_by_asset) {
      if (asset.toUpperCase() === candidate || asset.toUpperCase().startsWith(candidate)) {
        return row.totalScore;
      }
    }
  }

  for (const row of scores_by_asset.values()) {
    return row.totalScore;
  }

  return null;
}

function InfoTooltip(props: { readonly text: string }): ReactElement {
  return (
    <span className="group relative inline-flex">
      <button
        type="button"
        className="inline-flex h-4 w-4 shrink-0 items-center justify-center rounded-full border border-slate-600/80 bg-slate-900/80 text-[10px] font-semibold leading-none text-slate-400 transition-colors hover:border-cyan-500/50 hover:text-cyan-300"
        aria-label="About regime classification mapping"
      >
        i
      </button>
      <span
        role="tooltip"
        className="pointer-events-none absolute left-1/2 top-full z-20 mt-2 hidden w-[min(22rem,65ch)] -translate-x-1/2 rounded-md border border-slate-700 bg-slate-950/95 px-3 py-2 text-left text-[11px] font-normal normal-case leading-relaxed tracking-normal text-slate-300 shadow-xl group-hover:block group-focus-within:block"
      >
        {props.text}
      </span>
    </span>
  );
}

function MetricTile(props: {
  readonly label: string;
  readonly value: string;
  readonly mono?: boolean;
}): ReactElement {
  return (
    <div className="rounded-md border border-slate-800/90 bg-slate-950/40 px-3 py-2">
      <dt className="text-[10px] font-medium uppercase tracking-wide text-slate-500">{props.label}</dt>
      <dd
        className={cn(
          "mt-0.5 text-sm text-slate-100",
          props.mono !== false && "font-data tabular-nums",
        )}
      >
        {props.value}
      </dd>
    </div>
  );
}

function SemanticProgressBar(props: {
  readonly label: string;
  readonly valuePct: number;
  readonly regime?: string | null;
  readonly detail?: string;
  readonly variant?: "risk";
}): ReactElement {
  const clamped = Math.min(100, Math.max(0, props.valuePct));
  const fill_class =
    props.variant === "risk"
      ? clamped >= 75
        ? "bg-rose-500/90"
        : clamped >= 50
          ? "bg-amber-500/85"
          : clamped >= 25
            ? "bg-slate-400/70"
            : "bg-slate-500/60"
      : regime_bar_class(props.regime ?? "");

  const risk_band =
    clamped >= 75 ? "Critical" : clamped >= 50 ? "Elevated" : "Low";

  return (
    <div className="space-y-1.5">
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-[11px] font-medium text-slate-200">{props.label}</span>
        <span className="flex items-baseline gap-2">
          {props.variant === "risk" ? (
            <span
              className={cn(
                "text-[10px] font-medium uppercase tracking-wide",
                clamped >= 75
                  ? "text-rose-400"
                  : clamped >= 50
                    ? "text-amber-400"
                    : "text-slate-400",
              )}
            >
              {risk_band}
            </span>
          ) : null}
          {props.detail !== undefined ? (
            <span className="font-data text-[11px] tabular-nums text-slate-300">{props.detail}</span>
          ) : null}
        </span>
      </div>
      <div className="relative">
        {props.variant === "risk" ? (
          <div
            className="pointer-events-none absolute inset-x-0 top-0 flex h-2 justify-between px-px"
            aria-hidden
          >
            {[25, 50, 75].map((tick) => (
              <span
                key={tick}
                className="h-full w-px bg-slate-700/80"
                style={{ marginLeft: tick === 25 ? `${tick}%` : undefined, position: "absolute", left: `${tick}%` }}
              />
            ))}
          </div>
        ) : null}
        <div
          className="relative h-2 w-full overflow-hidden rounded-full border border-slate-800/90 bg-slate-950/60"
          role="meter"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={Math.round(clamped)}
          aria-label={`${props.label}: ${clamped.toFixed(1)}%${props.variant === "risk" ? ` (${risk_band})` : ""}`}
        >
          <div
            className={cn("h-full rounded-full transition-[width] duration-500", fill_class)}
            style={{ width: `${clamped}%` }}
          />
        </div>
        {props.variant === "risk" ? (
          <div className="relative mt-1 h-3 font-data text-[8px] uppercase tracking-wide text-slate-600">
            <span className="absolute left-0 top-0">Low</span>
            <span className="absolute left-1/4 top-0 -translate-x-1/2 tabular-nums">25%</span>
            <span className="absolute left-1/2 top-0 -translate-x-1/2">Elevated</span>
            <span className="absolute left-3/4 top-0 -translate-x-1/2 tabular-nums">75%</span>
            <span className="absolute right-0 top-0">Critical</span>
          </div>
        ) : null}
      </div>
    </div>
  );
}

function ConvictionGateSection(props: {
  readonly score: number | null;
  readonly gate: number;
  readonly detail?: string;
}): ReactElement {
  const ladder_score = props.score ?? 0;
  const passes_gate = props.score !== null && props.score >= props.gate;

  return (
    <div className="space-y-2">
      <ConfluenceThresholdScoreBar
        value={ladder_score}
        label="Conviction gate (ladder)"
        showZoneLegend={false}
        size="md"
      />
      <p className="font-data text-[10px] tabular-nums text-slate-400">
        Router gate{" "}
        <span className={passes_gate ? "text-emerald-400" : "text-amber-400"}>
          {passes_gate ? "passes" : "blocked"}
        </span>{" "}
        · requires ≥ {props.gate} / {CONFLUENCE_SCORE_CAP}
        {props.score === null ? " · awaiting live score" : null}
      </p>
      {props.detail !== undefined ? (
        <p className="text-[11px] leading-snug text-slate-500">{props.detail}</p>
      ) : null}
    </div>
  );
}

function PosteriorMixBars(props: { readonly probabilities: Record<string, number> }): ReactElement {
  const rows = useMemo(
    () =>
      Object.entries(props.probabilities)
        .map(([regime, value]) => ({ regime, value: Math.max(0, value) }))
        .sort((left, right) => right.value - left.value),
    [props.probabilities],
  );

  if (rows.length === 0) {
    return <p className="font-data text-[11px] tabular-nums text-slate-500">—</p>;
  }

  return (
    <div className="space-y-2">
      {rows.map((row) => {
        const pct = row.value * 100;
        return (
          <SemanticProgressBar
            key={row.regime}
            label={row.regime.charAt(0).toUpperCase() + row.regime.slice(1)}
            valuePct={pct}
            regime={row.regime}
            detail={`${pct.toFixed(1)}%`}
          />
        );
      })}
    </div>
  );
}

function RegimeFreshnessBadge(props: { readonly asOf: string }): ReactElement {
  const [, set_tick] = useState(0);

  useEffect(() => {
    const id = window.setInterval(() => set_tick((value) => value + 1), 1000);
    return () => window.clearInterval(id);
  }, []);

  const label = format_freshness_label(props.asOf);
  const tone = freshness_tone_class(props.asOf);

  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-md border border-slate-800/90 bg-slate-950/50 px-2 py-1 font-data text-[10px] tabular-nums",
        tone,
      )}
      title={`Snapshot assembled ${props.asOf}`}
    >
      <span className={cn("h-1.5 w-1.5 rounded-full", freshness_dot_class(props.asOf))} aria-hidden />
      Updated {label}
    </span>
  );
}

function DashboardMaskCallout(props: {
  readonly hmm: string | null;
  readonly dashboardRegime: string;
}): ReactElement | null {
  if (!detects_dashboard_mask(props.hmm, props.dashboardRegime)) {
    return null;
  }

  return (
    <div
      className="flex items-start gap-2 rounded-md border border-amber-500/35 bg-amber-950/30 px-3 py-2"
      role="status"
    >
      <span className="mt-0.5 font-data text-amber-400" aria-hidden>
        ⚠
      </span>
      <p className="text-[11px] leading-snug text-amber-100/90">
        Dashboard bucket shows <span className="font-semibold text-amber-200">RANGING</span> while HMM reads{" "}
        <span className="font-semibold text-amber-200">VOLATILE</span> — sizing and gates on this page use the
        underlying HMM label.
      </p>
    </div>
  );
}

function RegimeHeroPanel(props: { readonly data: RegimeControlCenterResponse }): ReactElement {
  const { data } = props;
  const live_health = useSystemStore((state) => state.health);
  const hmm_label = (data.hmmRegime ?? "—").toUpperCase();
  const glyph = regime_glyph(data.hmmRegime);
  const runner_up = live_health?.regimeContextRunnerUp?.trim();
  const transition_hint = live_health?.regimeContextTransitionHint?.trim();

  return (
    <CommandCard
      title="HMM regime (BTC)"
      subtitle={data.noteDashboardRanging}
      accent={regime_hero_accent(data.hmmRegime)}
      className={cn("relative overflow-hidden", regime_hero_shell_class(data.hmmRegime))}
      headerActions={<RegimeFreshnessBadge asOf={data.asOf} />}
    >
      <div
        className={cn(
          "pointer-events-none absolute inset-x-0 top-0 h-32 bg-gradient-to-b opacity-80",
          regime_hero_glow_class(data.hmmRegime),
        )}
        aria-hidden
      />

      <div className="relative space-y-4">
        <DashboardMaskCallout hmm={data.hmmRegime} dashboardRegime={data.dashboardRegime} />

        <div className="flex flex-wrap items-end gap-3">
          <div className="flex items-end gap-2">
            <span
              className={cn(
                "pb-1 font-data text-2xl leading-none",
                regime_hero_text_class(data.hmmRegime),
              )}
              aria-hidden
            >
              {glyph}
            </span>
            <div>
              <p
                className={cn(
                  "font-data text-4xl font-black tabular-nums tracking-tight drop-shadow-[0_0_24px_rgba(255,255,255,0.06)]",
                  regime_hero_text_class(data.hmmRegime),
                )}
              >
                {hmm_label}
              </p>
              {runner_up !== undefined && runner_up.length > 0 ? (
                <p className="mt-1 font-data text-[11px] tabular-nums text-slate-400">{runner_up}</p>
              ) : null}
              {transition_hint !== undefined && transition_hint.length > 0 ? (
                <p className="mt-0.5 max-w-[42ch] text-[11px] leading-snug text-cyan-300/85">{transition_hint}</p>
              ) : null}
            </div>
          </div>
          <Badge variant={hmm_badge_variant(data.hmmRegime)} className="text-[11px]">
            Dashboard bucket · {data.dashboardRegime}
          </Badge>
        </div>

        <dl className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
          <MetricTile label="Dwell in HMM state" value={data.dwellLabel} />
          <MetricTile label="Since (UTC)" value={data.dwellStartedAt ?? "—"} />
          <MetricTile label="Wire asset" value={data.signalAsset || "—"} />
          <MetricTile label="Snapshot confidence" value={`${(data.regimeConfidence * 100).toFixed(0)}%`} />
        </dl>
      </div>
    </CommandCard>
  );
}

function PillarSparkBar(props: { readonly multiplier: number }): ReactElement {
  const neutral_pct = 50;
  const span = 0.5;
  const deviation = Math.min(span, Math.max(-span, props.multiplier - 1));
  const bar_pct = (Math.abs(deviation) / span) * neutral_pct;
  const extends_right = deviation > 0.01;
  const extends_left = deviation < -0.01;

  return (
    <div className="relative mt-1.5 h-1.5 w-full overflow-hidden rounded-full bg-slate-800/80">
      <span
        className="absolute top-0 h-full w-px bg-slate-600/90"
        style={{ left: `${neutral_pct}%` }}
        aria-hidden
      />
      {extends_right ? (
        <span
          className="absolute top-0 h-full rounded-r-full bg-emerald-500/75"
          style={{ left: `${neutral_pct}%`, width: `${bar_pct}%` }}
          aria-hidden
        />
      ) : null}
      {extends_left ? (
        <span
          className="absolute top-0 h-full rounded-l-full bg-slate-500/70"
          style={{ left: `${neutral_pct - bar_pct}%`, width: `${bar_pct}%` }}
          aria-hidden
        />
      ) : null}
    </div>
  );
}

function PillarEmphasisBadges(props: { readonly detail: string }): ReactElement {
  const weights = parse_pillar_weights(props.detail);
  if (weights.length === 0) {
    return <p className="text-[11px] leading-snug text-slate-400">{props.detail}</p>;
  }

  return (
    <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
      {weights.map((row) => {
        const emphasis = row.multiplier >= 1.05 ? "high" : row.multiplier <= 0.95 ? "low" : "neutral";
        return (
          <div
            key={row.key}
            className={cn(
              "flex flex-col rounded-md border px-2.5 py-2",
              emphasis === "high" && "border-emerald-500/30 bg-emerald-950/25",
              emphasis === "low" && "border-slate-700/80 bg-slate-950/40",
              emphasis === "neutral" && "border-slate-700/70 bg-slate-950/35",
            )}
          >
            <span className="truncate text-[10px] font-medium uppercase tracking-wide text-slate-400">
              {row.key}
            </span>
            <span className="mt-0.5 font-data text-sm tabular-nums text-slate-100">×{row.multiplier.toFixed(2)}</span>
            <PillarSparkBar multiplier={row.multiplier} />
          </div>
        );
      })}
    </div>
  );
}

function ClassificationSignalRow(props: { readonly row: RegimeClassificationRow }): ReactElement {
  const { row } = props;
  const posterior_regime = parse_hmm_posterior_label(row.label);

  if (posterior_regime !== null) {
    const pct = parse_percent_detail(row.detail) ?? 0;
    return (
      <SemanticProgressBar
        label={row.label}
        valuePct={pct}
        regime={posterior_regime}
        detail={row.detail}
      />
    );
  }

  if (row.label.toLowerCase().includes("transition risk")) {
    const pct = parse_percent_detail(row.detail) ?? 0;
    return (
      <SemanticProgressBar label={row.label} valuePct={pct} detail={row.detail} variant="risk" />
    );
  }

  return (
    <div className="rounded-md border border-slate-800/80 bg-slate-950/35 px-3 py-2">
      <p className="text-[11px] font-medium text-slate-200">{row.label}</p>
      <p className="mt-1 text-[11px] leading-snug text-slate-400">{row.detail}</p>
    </div>
  );
}

function AdjustmentPolicyRow(props: {
  readonly row: RegimeAdjustmentRow;
  readonly gateScore: number | null;
  readonly gateThreshold: number;
}): ReactElement | null {
  const { row } = props;

  if (row.label.toLowerCase().includes("conviction gate")) {
    return (
      <div className="space-y-2 border-b border-slate-800/80 pb-4 last:border-b-0 last:pb-0">
        <ConvictionGateSection
          score={props.gateScore}
          gate={props.gateThreshold}
          detail={row.detail}
        />
      </div>
    );
  }

  if (row.label.toLowerCase().includes("pillar emphasis")) {
    return (
      <div className="space-y-2 border-b border-slate-800/80 pb-4 last:border-b-0 last:pb-0">
        <p className="text-[11px] font-medium text-slate-200">{row.label}</p>
        <PillarEmphasisBadges detail={row.detail} />
      </div>
    );
  }

  return (
    <div className="space-y-1 border-b border-slate-800/80 pb-3 last:border-b-0 last:pb-0">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <p className="text-[11px] font-medium text-slate-200">{row.label}</p>
        {row.value !== undefined ? (
          <span className="font-data text-[11px] tabular-nums text-cyan-300">{row.value}</span>
        ) : null}
      </div>
      <p className="text-[11px] leading-snug text-slate-500">{row.detail}</p>
    </div>
  );
}

function build_daily_heatmap(segments: RegimeTimelineSegment[]): Array<{ day: string; regime: string }> {
  if (segments.length === 0) {
    return [];
  }

  const buckets = new Map<string, { regime: string; weight: number }>();

  for (const seg of segments) {
    const start = new Date(seg.startTs).getTime();
    const end = new Date(seg.endTs).getTime();
    const duration = Math.max(1, end - start);
    const day_key = seg.startTs.slice(0, 10);
    const existing = buckets.get(day_key);
    if (existing === undefined || duration > existing.weight) {
      buckets.set(day_key, { regime: seg.hmmRegime, weight: duration });
    }
  }

  const sorted_days = [...buckets.entries()].sort(([a], [b]) => a.localeCompare(b));
  return sorted_days.slice(-7).map(([day, meta]) => ({ day, regime: meta.regime }));
}

function segment_overlaps_day(seg: RegimeTimelineSegment, day: string): boolean {
  const day_start = new Date(`${day}T00:00:00.000Z`).getTime();
  const day_end = day_start + 86_400_000;
  const seg_start = new Date(seg.startTs).getTime();
  const seg_end = new Date(seg.endTs).getTime();
  return seg_start < day_end && seg_end > day_start;
}

function format_utc_range(start_ts: string, end_ts: string): string {
  return `${start_ts.slice(0, 16).replace("T", " ")} → ${end_ts.slice(0, 16).replace("T", " ")} UTC`;
}

function RegimePageSkeleton(): ReactElement {
  return (
    <>
      <div className="command-card space-y-4 p-4">
        <div className="flex items-center justify-between gap-3">
          <Skeleton className="h-3 w-36" />
          <Skeleton className="h-6 w-24 rounded-md" />
        </div>
        <Skeleton className="h-10 w-56" />
        <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
          <Skeleton className="h-14 rounded-md" />
          <Skeleton className="h-14 rounded-md" />
          <Skeleton className="h-14 rounded-md" />
          <Skeleton className="h-14 rounded-md" />
        </div>
      </div>

      <div className="grid gap-4 xl:grid-cols-12">
        <Skeleton className="h-56 rounded-lg xl:col-span-4" />
        <Skeleton className="h-56 rounded-lg xl:col-span-5" />
        <Skeleton className="h-56 rounded-lg xl:col-span-3" />
      </div>

      <div className="command-card space-y-4 p-4">
        <div className="flex items-center justify-between gap-3">
          <Skeleton className="h-3 w-40" />
          <Skeleton className="h-3 w-28" />
        </div>
        <Skeleton className="h-8 w-full rounded-md" />
        <div className="grid grid-cols-7 gap-1">
          {Array.from({ length: 7 }, (_, index) => (
            <div key={index} className="flex flex-col items-center gap-1">
              <Skeleton className="h-6 w-full rounded-sm" />
              <Skeleton className="h-2 w-8" />
            </div>
          ))}
        </div>
      </div>
    </>
  );
}

function RegimeHeatmapStrip(props: { readonly segments: RegimeTimelineSegment[] }): ReactElement {
  const { segments } = props;
  const [hovered_index, set_hovered_index] = useState<number | null>(null);
  const [hovered_day, set_hovered_day] = useState<string | null>(null);

  if (segments.length === 0) {
    return (
      <EmptyState
        title="No regime history yet"
        description="Run the autonomous engine against BTC — snapshots accrue in Redis for a 7-day strip."
      />
    );
  }

  const start_ms = new Date(segments[0].startTs).getTime();
  const end_ms = new Date(segments[segments.length - 1].endTs).getTime();
  const span = Math.max(1, end_ms - start_ms);
  const daily_blocks = build_daily_heatmap(segments);
  const hovered_segment = hovered_index !== null ? segments[hovered_index] : null;

  function is_segment_highlighted(index: number, seg: RegimeTimelineSegment): boolean {
    if (hovered_index === index) {
      return true;
    }
    if (hovered_day !== null && segment_overlaps_day(seg, hovered_day)) {
      return true;
    }
    return false;
  }

  return (
    <div className="space-y-4">
      <div className="relative">
        <div className="flex h-8 w-full gap-px overflow-hidden rounded-md border border-slate-800/90 bg-slate-950/50 p-px">
          {segments.map((seg, index) => {
            const width_pct =
              ((new Date(seg.endTs).getTime() - new Date(seg.startTs).getTime()) / span) * 100;
            const highlighted = is_segment_highlighted(index, seg);
            return (
              <div
                key={`${seg.startTs}-${seg.hmmRegime}-${index}`}
                className={cn(
                  "relative h-full min-w-[2px] cursor-crosshair transition-all first:rounded-l-[3px] last:rounded-r-[3px]",
                  regime_solid_class(seg.hmmRegime),
                  highlighted && "z-10 ring-2 ring-cyan-400/80 ring-offset-1 ring-offset-slate-950 brightness-125",
                  !highlighted && (hovered_index !== null || hovered_day !== null) && "opacity-45",
                )}
                style={{ width: `${Math.max(0.4, Math.min(100, width_pct))}%` }}
                onMouseEnter={() => {
                  set_hovered_index(index);
                  set_hovered_day(null);
                }}
                onMouseLeave={() => set_hovered_index(null)}
              />
            );
          })}
        </div>

        {hovered_segment !== null ? (
          <div
            className="pointer-events-none absolute left-1/2 top-full z-20 mt-2 -translate-x-1/2 rounded-md border border-slate-700 bg-slate-950/95 px-3 py-2 shadow-xl"
            role="tooltip"
          >
            <p className="font-data text-[11px] font-semibold tabular-nums text-slate-100">
              {hovered_segment.hmmRegime.toUpperCase()}
            </p>
            <p className="mt-0.5 font-data text-[10px] tabular-nums text-slate-400">
              {format_utc_range(hovered_segment.startTs, hovered_segment.endTs)}
            </p>
            {hovered_segment.startPriceUsd ? (
              <p className="mt-0.5 font-data text-[10px] tabular-nums text-cyan-300">
                BTC ${hovered_segment.startPriceUsd}
              </p>
            ) : null}
          </div>
        ) : null}
      </div>

      {daily_blocks.length > 0 ? (
        <div>
          <p className="mb-2 text-[10px] font-medium uppercase tracking-wide text-slate-500">Daily dominant state</p>
          <div className="grid grid-cols-7 gap-1">
            {daily_blocks.map((block) => (
              <div key={block.day} className="flex flex-col items-center gap-1">
                <div
                  className={cn(
                    "h-6 w-full cursor-pointer rounded-sm border border-slate-800/80 transition-all",
                    regime_solid_class(block.regime),
                    hovered_day === block.day && "ring-2 ring-cyan-400/80 brightness-125",
                    hovered_day !== null && hovered_day !== block.day && "opacity-45",
                  )}
                  onMouseEnter={() => {
                    set_hovered_day(block.day);
                    set_hovered_index(null);
                  }}
                  onMouseLeave={() => set_hovered_day(null)}
                />
                <span className="font-data text-[9px] tabular-nums text-slate-500">{block.day.slice(5)}</span>
              </div>
            ))}
          </div>
        </div>
      ) : null}

      <div className="flex flex-wrap gap-3 text-[10px] text-slate-500">
        <span className="inline-flex items-center gap-1.5">
          <span className="h-2 w-2 rounded-sm bg-emerald-600/90" aria-hidden />
          Bull
        </span>
        <span className="inline-flex items-center gap-1.5">
          <span className="h-2 w-2 rounded-sm bg-rose-700/85" aria-hidden />
          Bear
        </span>
        <span className="inline-flex items-center gap-1.5">
          <span className="h-2 w-2 rounded-sm bg-amber-600/75" aria-hidden />
          Volatile
        </span>
      </div>
    </div>
  );
}

export function RegimeControlCenterPage(): ReactElement {
  const query = useQuery({
    queryKey: ["regime-control-center"],
    queryFn: async (): Promise<RegimeControlCenterResponse> => {
      const response = await fetch(apiUrl("/api/dashboard/regime-control-center"), {
        credentials: "include",
      });
      if (!response.ok) {
        throw new Error(`Regime control center HTTP ${response.status}`);
      }
      return response.json() as Promise<RegimeControlCenterResponse>;
    },
    refetchInterval: 30_000,
  });

  const scores_by_asset = useScoresStore((state) => state.scoresByAsset);
  const data = query.data;

  const focus_score = useMemo(() => {
    if (data === undefined) {
      return null;
    }
    return resolve_focus_asset_score(scores_by_asset, data.focusAsset, data.signalAsset);
  }, [data, scores_by_asset]);

  const execution_rows = useMemo(() => {
    if (data === undefined) {
      return [];
    }
    return data.adjustmentRows.filter((row) => {
      const label = row.label.toLowerCase();
      return (
        !label.includes("pillar emphasis") &&
        !label.includes("conviction gate") &&
        !label.includes("position-size modifier")
      );
    });
  }, [data]);

  const policy_rows = useMemo(() => {
    if (data === undefined) {
      return [];
    }
    return data.adjustmentRows.filter((row) => row.label.toLowerCase().includes("pillar emphasis"));
  }, [data]);

  const conviction_row = useMemo(() => {
    if (data === undefined) {
      return undefined;
    }
    return data.adjustmentRows.find((row) => row.label.toLowerCase().includes("conviction gate"));
  }, [data]);

  return (
    <div className="flex min-h-0 flex-col space-y-5">
      <PageHeader
        kicker="Market regime"
        title={
          <span className="inline-flex items-center gap-2">
            Regime Control Center
            <InfoTooltip text={REGIME_INFO_TOOLTIP} />
          </span>
        }
        description={
          <p className="max-w-[65ch] text-sm leading-relaxed text-slate-400">
            HMM regime for BTC, live gates, sizing modifiers, and a seven-day dwell strip.
          </p>
        }
        actions={
          query.isFetching ? (
            <span className="inline-flex items-center gap-2 rounded-md border border-slate-800 bg-slate-900/60 px-3 py-1.5 text-[11px] text-slate-400">
              <Spinner className="h-3 w-3" />
              Refreshing
            </span>
          ) : undefined
        }
      />

      {query.isLoading ? <RegimePageSkeleton /> : null}

      {query.isError ? (
        <p className="text-xs text-[var(--danger)]" role="alert">
          Unable to load regime data. Confirm the API is running and Redis has BTC signals.
        </p>
      ) : null}

      {data ? (
        <>
          <RegimeHeroPanel data={data} />

          <div className="grid gap-4 xl:grid-cols-12">
            <CommandCard
              title="Classification signals"
              subtitle="HMM posteriors and transition risk"
              className="xl:col-span-4"
            >
              {data.classificationRows.length === 0 ? (
                <p className="text-xs text-slate-500">Waiting for regime agent sub-signals in Redis…</p>
              ) : (
                <div className="space-y-3">
                  {data.classificationRows.map((row) => (
                    <ClassificationSignalRow key={`${row.label}-${row.detail.slice(0, 24)}`} row={row} />
                  ))}
                </div>
              )}
            </CommandCard>

            <CommandCard
              title="Execution adjustments"
              subtitle="Gates, sizing, and posterior mix"
              className="xl:col-span-5"
            >
              <div className="space-y-4">
                {conviction_row !== undefined ? (
                  <AdjustmentPolicyRow
                    row={conviction_row}
                    gateScore={focus_score}
                    gateThreshold={data.gateThreshold}
                  />
                ) : (
                  <ConvictionGateSection score={focus_score} gate={data.gateThreshold} />
                )}

                <dl className="grid gap-2 sm:grid-cols-2">
                  <MetricTile
                    label="Position modifier"
                    value={`${data.positionSizeModifier.toFixed(2)}× · ${data.confidenceTier}`}
                  />
                  <MetricTile
                    label="7d switches"
                    value={`${data.regimeSwitches7d} · ${data.historySamples7d} samples`}
                  />
                </dl>

                <div>
                  <p className="text-[10px] font-medium uppercase tracking-wide text-slate-500">Posterior mix</p>
                  <div className="mt-2">
                    <PosteriorMixBars probabilities={data.regimeProbabilities} />
                  </div>
                </div>

                {execution_rows.map((row) => (
                  <AdjustmentPolicyRow
                    key={row.label}
                    row={row}
                    gateScore={focus_score}
                    gateThreshold={data.gateThreshold}
                  />
                ))}
              </div>
            </CommandCard>

            <CommandCard
              title="Regime-specific policy"
              subtitle="Blended pillar emphasis for current HMM"
              className="xl:col-span-3"
            >
              {policy_rows.length === 0 ? (
                <p className="text-xs text-slate-500">No derived policy rows yet.</p>
              ) : (
                <div className="space-y-3">
                  {policy_rows.map((row) => (
                    <AdjustmentPolicyRow
                      key={row.label}
                      row={row}
                      gateScore={focus_score}
                      gateThreshold={data.gateThreshold}
                    />
                  ))}
                </div>
              )}
            </CommandCard>
          </div>

          <CommandCard
            title="7-day regime strip"
            subtitle="BTC marks · dwell segments"
            headerActions={
              <span className="font-data text-[10px] tabular-nums text-slate-500">{data.asOf}</span>
            }
          >
            <RegimeHeatmapStrip segments={data.timeline} />
          </CommandCard>
        </>
      ) : null}
    </div>
  );
}
