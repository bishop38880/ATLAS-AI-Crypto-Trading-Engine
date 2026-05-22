import { Link } from "@tanstack/react-router";
import { Fragment, memo, useMemo, type DragEvent } from "react";

import { usePriceSnapshotForDashboardPair, useScoresSnapshotForDashboardPair } from "../../hooks/useDashboardAssetSnapshots";
import type { DashboardPositionSlot } from "../../lib/dashboard-positions";
import { derive_pair_from_rotation_entry } from "../../lib/dashboard-symbol";
import { derive_signal_route_slug } from "../../lib/dashboard-route-slug";
import { calculate_confluence_card_tier_border_classes } from "../../lib/calculate-confluence-card-score-tier";
import { calculate_confluence_action_badge_presentation } from "../../lib/calculate-confluence-action-badge";
import {
  CONFLUENCE_CYCLE_INTERVAL_SECONDS,
  CONFLUENCE_GATE_THRESHOLD_DEFAULT,
  CONFLUENCE_SCORE_CAP,
  CONFLUENCE_SCORE_STALE_CYCLE_MULTIPLIER,
} from "../../lib/confluence-score-constants";
import { calculate_is_score_cycle_stale, format_last_compact_from_iso } from "../../lib/format-relative-age";
import { format_usd_compact_display } from "../../lib/format-usd-compact";
import { cn } from "../../lib/cn";
import {
  DASHBOARD_PIN_DRAG_MIME_TYPE,
  serialize_dashboard_pin_for_drag,
} from "../../lib/dashboard-pin-dnd";
import { AssetLogo } from "../AssetLogo";
import { Badge } from "../ui/Badge";
import { ConfluenceThresholdScoreBar } from "../ui/ConfluenceThresholdScoreBar";
import { CategoryScoreMiniBars } from "./CategoryScoreMiniBars";

import type { ObtiSummaryLevel } from "../../store/index";

function calculate_regime_echo_label(regime: string): string {
  switch (regime) {
    case "BULL":
      return "BULL ▲";
    case "BEAR":
      return "BEAR ▼";
    case "RANGING":
      return "RANGE →";
    default:
      return "Regime →";
  }
}

function ObtiGlyph({ summary }: { summary: ObtiSummaryLevel | null }) {
  if (summary === null || summary === "low") {
    return null;
  }

  if (summary === "moderate") {
    return (
      <span className="text-[var(--warning)]" aria-hidden>
        ◐
      </span>
    );
  }

  return (
    <span className="text-[var(--danger)]" aria-label="Extreme order-book toxicity">
      ●
    </span>
  );
}

function coerce_percent_glyph_display(raw: string | undefined): { glyph: string; text: string; trend_class: string } {
  const neutral_glyph = "→";
  if (raw === undefined || raw.trim().length === 0) {
    return { glyph: neutral_glyph, text: "—", trend_class: "text-[var(--text-secondary)]" };
  }

  const sanitized = Number.parseFloat(raw.replace(/,/g, "").replace(/%/g, ""));
  if (!Number.isFinite(sanitized)) {
    return { glyph: neutral_glyph, text: raw, trend_class: "text-[var(--text-secondary)]" };
  }

  const glyph = sanitized > 0 ? "▲" : sanitized < 0 ? "▼" : neutral_glyph;
  const text = `${sanitized >= 0 ? "+" : ""}${sanitized.toFixed(1)}%`;
  const trend_class =
    sanitized > 0 ? "text-[var(--success)]" : sanitized < 0 ? "text-[var(--danger)]" : "text-[var(--text-secondary)]";

  return { glyph, text, trend_class };
}

export interface AssetCardProps {
  pair: string;
  system_regime: string;
  slot_lookup: DashboardPositionSlot[];
  /** When `select`, the card toggles selection via `onSelectCanonicalPair` instead of navigating. */
  interactionMode?: "link" | "select";
  onSelectCanonicalPair?: (canonicalPair: string) => void;
  isSelected?: boolean;
  /** With `interactionMode="select"`, allows dragging the card onto vacant PROMETHEUS ladder slots. */
  enableExecutionLadderDrag?: boolean;
}

export const AssetCard = memo(function AssetCard({
  pair,
  system_regime,
  slot_lookup,
  interactionMode = "link",
  onSelectCanonicalPair,
  isSelected = false,
  enableExecutionLadderDrag = false,
}: AssetCardProps) {
  const canonical_pair = useMemo(() => derive_pair_from_rotation_entry(pair), [pair]);
  const slug = derive_signal_route_slug(canonical_pair);

  const handle_execution_ladder_drag_start = (drag_event: DragEvent<HTMLButtonElement>): void => {
    const payload = serialize_dashboard_pin_for_drag(canonical_pair);
    drag_event.dataTransfer.setData(DASHBOARD_PIN_DRAG_MIME_TYPE, payload);
    drag_event.dataTransfer.setData("text/plain", payload);
    drag_event.dataTransfer.effectAllowed = "copy";
  };

  const score_row = useScoresSnapshotForDashboardPair(canonical_pair);
  const price_row = usePriceSnapshotForDashboardPair(canonical_pair);

  const slot_assignment =
    slot_lookup.find((slot) => {
      if (typeof slot.asset !== "string" || slot.asset.trim().length === 0) {
        return false;
      }

      const candidate = derive_pair_from_rotation_entry(slot.asset);
      return candidate.toUpperCase() === canonical_pair.toUpperCase();
    })?.slot_index ?? null;

  const action_badge = useMemo(() => {
    if (typeof score_row === "undefined") {
      return calculate_confluence_action_badge_presentation({
        totalScoreCapped: 0,
        gateThreshold: CONFLUENCE_GATE_THRESHOLD_DEFAULT,
        vetoActive: false,
        decision: "No Position",
      });
    }
    return calculate_confluence_action_badge_presentation({
      totalScoreCapped: Math.min(score_row.totalScore, CONFLUENCE_SCORE_CAP),
      gateThreshold: score_row.gateThreshold,
      vetoActive: score_row.vetoActive,
      decision: score_row.decision,
    });
  }, [score_row]);

  const price_display = typeof price_row === "undefined" ? "—" : `$${price_row.price}`;
  const change_visual = coerce_percent_glyph_display(price_row?.change24h);
  const open_interest_label = format_usd_compact_display(price_row?.openInterest ?? "—");
  const funding_rate = typeof price_row === "undefined" ? "—" : price_row.fundingRate;

  const last_cycle_label =
    typeof score_row === "undefined" ? "—" : `Last: ${format_last_compact_from_iso(score_row.cycleTs)}`;

  const stale_card = calculate_is_score_cycle_stale(
    score_row?.cycleTs,
    CONFLUENCE_CYCLE_INTERVAL_SECONDS,
    CONFLUENCE_SCORE_STALE_CYCLE_MULTIPLIER,
  );

  const confluence_total =
    typeof score_row === "undefined" ? 0 : Math.min(score_row.totalScore, CONFLUENCE_SCORE_CAP);

  const obti_summary = typeof score_row === "undefined" ? null : score_row.obtiSummary;

  const veto_active = typeof score_row === "undefined" ? false : score_row.vetoActive;

  const tier_edge_class = calculate_confluence_card_tier_border_classes({
    total_score_capped: confluence_total,
    veto_active,
  });

  const surface_class = cn(
    "asset-card",
    tier_edge_class,
    stale_card && "opacity-[0.65] saturate-[0.85]",
    slot_assignment !== null &&
      "ring-2 ring-cyan-400/60 ring-offset-2 ring-offset-slate-950 shadow-[0_8px_32px_rgba(34,211,238,0.1)]",
    interactionMode === "select" && isSelected && "ring-2 ring-cyan-400 ring-offset-2 ring-offset-slate-950",
    interactionMode === "select" &&
      enableExecutionLadderDrag &&
      "cursor-grab touch-manipulation select-none active:cursor-grabbing",
  );

  const body = (
    <Fragment>
      <div className="flex items-start justify-between gap-3">
        <div className="flex min-w-0 flex-1 items-center gap-3">
          <AssetLogo symbol={canonical_pair} size="lg" className="size-11 shrink-0" />
          <p className="truncate text-sm font-semibold tracking-tight text-slate-100">
            {canonical_pair.toUpperCase()}
          </p>
        </div>
        <div className="flex flex-col items-end gap-2">
          {stale_card ? (
            <Badge variant="stale" className="px-2.5 py-1 text-[12px] tracking-wider" aria-label="Score data is stale">
              STALE
            </Badge>
          ) : null}
          {slot_assignment !== null ? (
            <Badge variant="open" aria-label={`Position slot ${slot_assignment}`}>{`● SLOT ${slot_assignment}`}</Badge>
          ) : null}
          <Badge variant={action_badge.variant} aria-label={action_badge.ariaLabel}>
            {action_badge.label}
          </Badge>
        </div>
      </div>

      <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-2 font-data text-[12px]">
        <span className="tabular-nums text-[var(--text-primary)]">{price_display}</span>
        <span className={cn("flex items-center gap-1", change_visual.trend_class)}>
          <span aria-hidden>{change_visual.glyph}</span>
          <span>{change_visual.text}</span>
        </span>
        <span className="tabular-nums text-[var(--text-secondary)]">{`OI: ${open_interest_label}`}</span>
        <span className="tabular-nums text-[var(--text-secondary)]">{`FR: ${funding_rate}`}</span>
      </div>

      <div className="mt-3 flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
        <div className="min-w-0 flex-1">
          <ConfluenceThresholdScoreBar value={confluence_total} label="Score" size="sm" animated />
        </div>
        <Badge variant="shadow" className="h-fit shrink-0 whitespace-nowrap text-[10px] uppercase">
          {calculate_regime_echo_label(system_regime)}
        </Badge>
      </div>

      <div className="mt-3 flex flex-col gap-2 text-[11px] text-[var(--text-secondary)] sm:flex-row sm:items-center sm:gap-4">
        <CategoryScoreMiniBars
          categoryScores={score_row?.categoryScores}
          className="min-w-0 flex-1"
        />
        <span className="shrink-0 tabular-nums sm:text-right">{last_cycle_label}</span>
      </div>

      <div className="pointer-events-none absolute bottom-2 right-3 text-base leading-none">
        <ObtiGlyph summary={obti_summary} />
      </div>

    </Fragment>
  );

  if (interactionMode === "select") {
    const selection_aria =
      enableExecutionLadderDrag === true
        ? `Select ${canonical_pair} for universe actions. Drag onto a vacant PROMETHEUS ladder slot to reserve execution intent.`
        : `Select ${canonical_pair} for universe actions`;

    return (
      <button
        type="button"
        className={surface_class}
        draggable={enableExecutionLadderDrag}
        aria-pressed={isSelected}
        aria-label={selection_aria}
        onDragStart={enableExecutionLadderDrag ? handle_execution_ladder_drag_start : undefined}
        onClick={() => onSelectCanonicalPair?.(canonical_pair)}
      >
        {body}
      </button>
    );
  }

  return (
    <Link to="/signals/$asset" params={{ asset: slug }} className={surface_class}>
      {body}
    </Link>
  );
});
