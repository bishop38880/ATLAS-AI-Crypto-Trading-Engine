import { useNavigate } from "@tanstack/react-router";
import { useMemo, useState } from "react";

import { DailyRotationModal } from "../AssetUniverse/DailyRotationModal";
import { useDashboardLastCycleHumanLabel } from "../../hooks/useDashboardLastCycle";
import type { DashboardPositionSlot } from "../../lib/dashboard-positions";
import {
  calculate_dashboard_pairs,
  calculate_is_tier_one_pair,
  merge_dashboard_monitored_pairs,
} from "../../lib/dashboard-universe";
import {
  derive_pair_from_rotation_entry,
  calculate_symbol_keys_deduped,
} from "../../lib/dashboard-symbol";
import { derive_signal_route_slug } from "../../lib/dashboard-route-slug";
import { coerce_signal_decision_label, derive_signal_decision_from_total_score } from "../../lib/channel-mappers";
import { calculate_signal_filter_match } from "../../lib/signal-decision-display";
import { cn } from "../../lib/cn";
import type { PricePayload, ScoresPayload, SignalDecision } from "../../store/index";
import { usePricesStore, useScoresStore } from "../../store/index";
import { useAssetUniverseStore } from "../../store/assetUniverseStore";

import { AssetCard } from "./AssetCard";
import { Badge } from "../ui/Badge";

type PortfolioFilterMode = "all" | "tier1" | "position" | "signal";
type PortfolioSortMode = "score" | "change" | "funding" | "alpha";

function compare_pairs_alphabetical(left_pair: string, right_pair: string): number {
  return derive_pair_from_rotation_entry(left_pair).localeCompare(derive_pair_from_rotation_entry(right_pair));
}

function coerce_percent_number_generic(raw?: string): number | null {
  if (raw === undefined) {
    return null;
  }
  const sanitized = Number.parseFloat(raw.replace(/,/g, "").replace(/%/g, ""));
  return Number.isFinite(sanitized) ? sanitized : null;
}

export interface AssetUniversePanelProps {
  universe_pairs?: readonly string[] | undefined;
  slot_snapshots: DashboardPositionSlot[];
  system_regime: string;
}

function resolve_score_decision(snapshot: unknown, total_fallback: number): SignalDecision {
  if (snapshot === undefined || snapshot === null) {
    return "No Position";
  }

  const typed_row = snapshot as { decision?: string; totalScore?: number };
  const total = typeof typed_row.totalScore === "number" ? typed_row.totalScore : total_fallback;

  return typeof typed_row.decision === "string"
    ? coerce_signal_decision_label(typed_row.decision, total)
    : derive_signal_decision_from_total_score(total);
}

/** Dashboard asset horizon with selection toolbar and daily rotation staging (frontend-only until POST rotation-override). */
export function AssetUniversePanel({ universe_pairs, slot_snapshots, system_regime }: AssetUniversePanelProps) {
  const [portfolio_filter, set_portfolio_filter] = useState<PortfolioFilterMode>("all");
  const [portfolio_sort, set_portfolio_sort] = useState<PortfolioSortMode>("score");

  const scores_bundle = useScoresStore((channel) => channel.scoresByAsset);
  const price_bundle = usePricesStore((channel) => channel.prices);
  const dashboard_assets = useAssetUniverseStore((state) => state.dashboardAssets);

  const selected_universe_live = useAssetUniverseStore((s) => s.selectedUniverseAsset);
  const daily_rotation_staged = useAssetUniverseStore((s) => s.dailyRotationStaged);
  const set_rotation_modal_open = useAssetUniverseStore((s) => s.setRotationModalOpen);
  const set_selected_universe_asset = useAssetUniverseStore((s) => s.setSelectedUniverseAsset);

  const navigate = useNavigate();

  const last_cycle_snapshot = useDashboardLastCycleHumanLabel();

  const active_pairs_base = useMemo(() => {
    const base_pairs = universe_pairs ?? calculate_dashboard_pairs(null);
    return merge_dashboard_monitored_pairs(base_pairs, dashboard_assets);
  }, [dashboard_assets, universe_pairs]);

  const prepared_pairs = useMemo(() => {
    const rows = active_pairs_base.map((pair) => {
      const canonical_pair = derive_pair_from_rotation_entry(pair);
      const canonical_upper = canonical_pair.toUpperCase();

      let score_snapshot: ScoresPayload | undefined;

      const score_keys = calculate_symbol_keys_deduped(canonical_pair);
      for (const lookup_key of score_keys) {
        const hit_row = scores_bundle.get(lookup_key);
        if (hit_row !== undefined && hit_row.asset !== "__aggregate__") {
          score_snapshot = hit_row;
          break;
        }
      }

      let price_snapshot: PricePayload | undefined;

      for (const lookup_key of score_keys) {
        const px = price_bundle.get(lookup_key);
        if (px) {
          price_snapshot = px;
          break;
        }
      }

      if (price_snapshot === undefined) {
        const alias_keys = score_keys.map((token) => token.toUpperCase());
        for (const [market_symbol, payload] of price_bundle.entries()) {
          if (alias_keys.includes(market_symbol.toUpperCase())) {
            price_snapshot = payload;
            break;
          }
        }
      }

      const score_total = typeof score_snapshot === "undefined" ? 0 : score_snapshot.totalScore;
      const verdict = resolve_score_decision(score_snapshot, score_total);

      const change_component = coerce_percent_number_generic(price_snapshot?.change24h);
      const funding_component = coerce_percent_number_generic(price_snapshot?.fundingRate);

      const has_open_slot_row = slot_snapshots.some((slot_row) => {
        if (typeof slot_row.asset !== "string" || slot_row.asset.trim().length === 0) {
          return false;
        }
        const slot_pair_upper = derive_pair_from_rotation_entry(slot_row.asset).toUpperCase();
        return slot_pair_upper === canonical_upper;
      });

      const passes_signal_gate = calculate_signal_filter_match(verdict);

      return {
        canonical_pair,
        score_total,
        change_component,
        funding_component,
        has_open_slot_row,
        passes_signal_gate,
      };
    });

    const narrowed_rows = rows.filter((row_pack) => {
      switch (portfolio_filter) {
        case "tier1":
          return calculate_is_tier_one_pair(row_pack.canonical_pair);
        case "position":
          return row_pack.has_open_slot_row;
        case "signal":
          return row_pack.passes_signal_gate;
        default:
          return true;
      }
    });

    const ordered_rows = [...narrowed_rows].sort((alpha_row, beta_row) => {
      if (portfolio_sort === "score") {
        if (alpha_row.score_total === beta_row.score_total) {
          return compare_pairs_alphabetical(alpha_row.canonical_pair, beta_row.canonical_pair);
        }
        return beta_row.score_total - alpha_row.score_total;
      }

      if (portfolio_sort === "change") {
        const left_change = alpha_row.change_component ?? Number.NEGATIVE_INFINITY;
        const right_change = beta_row.change_component ?? Number.NEGATIVE_INFINITY;
        if (left_change === right_change) {
          return compare_pairs_alphabetical(alpha_row.canonical_pair, beta_row.canonical_pair);
        }
        return right_change - left_change;
      }

      if (portfolio_sort === "funding") {
        const left_funding = alpha_row.funding_component ?? Number.NEGATIVE_INFINITY;
        const right_funding = beta_row.funding_component ?? Number.NEGATIVE_INFINITY;
        if (left_funding === right_funding) {
          return compare_pairs_alphabetical(alpha_row.canonical_pair, beta_row.canonical_pair);
        }
        return right_funding - left_funding;
      }

      return compare_pairs_alphabetical(alpha_row.canonical_pair, beta_row.canonical_pair);
    });

    return ordered_rows.map((segment) => segment.canonical_pair);
  }, [
    active_pairs_base,
    portfolio_filter,
    portfolio_sort,
    price_bundle,
    scores_bundle,
    slot_snapshots,
  ]);

  const regime_tone = system_regime === "BULL" ? "bull" : system_regime === "BEAR" ? "bear" : "ranging";
  const regime_text =
    system_regime === "BULL" ? "BULL ▲" : system_regime === "BEAR" ? "BEAR ▼" : "RANGING →";

  const select_focus_ring =
    "rounded-[var(--radius-sm)] border border-[var(--border)] bg-[var(--bg-base)] px-2 py-1 text-[var(--text-primary)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent-cyan)]";

  const add_rotation_disabled =
    selected_universe_live === null ||
    daily_rotation_staged.length >= 4 ||
    selected_universe_live === undefined;
  const signals_disabled = selected_universe_live === null || selected_universe_live === undefined;

  const rotation_full = daily_rotation_staged.length >= 4;

  const handle_view_signals = () => {
    if (!selected_universe_live) {
      return;
    }
    const slug = derive_signal_route_slug(selected_universe_live);
    navigate({ to: "/signals/$asset", params: { asset: slug } });
  };

  return (
    <div className="relative pb-28">
      <span aria-live="polite" className="sr-only">
        {selected_universe_live
          ? `${selected_universe_live} selected`
          : "No asset selected"}
      </span>

      <section
        aria-label="Dashboard filters"
        className="flex flex-col gap-3 rounded-2xl border border-white/5 bg-slate-950/25 p-4 md:flex-row md:flex-wrap md:items-center md:justify-between"
      >
        <div className="flex flex-wrap gap-3 text-[12px]" aria-live="polite">
          <label className="panel-title flex flex-col gap-1">
            All assets
            <select
              value={portfolio_filter}
              className={cn(select_focus_ring)}
              aria-label="Filter asset universe"
              onChange={(change_event) => set_portfolio_filter(change_event.target.value as PortfolioFilterMode)}
            >
              <option value="all">All assets</option>
              <option value="tier1">Tier 1 only</option>
              <option value="position">Has position</option>
              <option value="signal">Has signal</option>
            </select>
          </label>

          <label className="panel-title flex flex-col gap-1">
            Sort
            <select
              value={portfolio_sort}
              className={cn(select_focus_ring)}
              aria-label="Sort dashboard cards"
              onChange={(change_event) => set_portfolio_sort(change_event.target.value as PortfolioSortMode)}
            >
              <option value="score">Score</option>
              <option value="change">Price change</option>
              <option value="funding">Funding rate</option>
              <option value="alpha">Alphabetical</option>
            </select>
          </label>
        </div>

        <div className="flex flex-wrap items-center gap-3 md:justify-end">
          <Badge variant={regime_tone}>{regime_text}</Badge>
          <span className="font-data text-[12px] text-[var(--text-secondary)]">{`Last cycle: ${last_cycle_snapshot}`}</span>
        </div>
      </section>

      <div className="mt-4 grid min-w-0 w-full grid-cols-1 gap-2.5 md:grid-cols-[repeat(2,minmax(0,1fr))] xl:grid-cols-[repeat(3,minmax(0,1fr))]">
        {prepared_pairs.map((symbol_pair) => (
          <AssetCard
            key={symbol_pair}
            pair={symbol_pair}
            system_regime={system_regime}
            slot_lookup={slot_snapshots}
            interactionMode="select"
            enableExecutionLadderDrag
            isSelected={
              selected_universe_live !== null &&
              derive_pair_from_rotation_entry(selected_universe_live).toUpperCase() === symbol_pair.toUpperCase()
            }
            onSelectCanonicalPair={(canonical: string) => {
              set_selected_universe_asset(canonical);
            }}
          />
        ))}
      </div>

      <div
        className="sticky bottom-0 z-20 mt-6 flex gap-2 border-t border-[var(--border)] bg-[var(--bg-surface)]/95 p-3 backdrop-blur-sm"
        aria-label="Asset actions"
      >
        <button
          type="button"
          title={
            rotation_full && selected_universe_live
              ? "Daily rotation is full (4/4). Remove an asset first."
              : undefined
          }
          onClick={() => set_rotation_modal_open(true)}
          disabled={add_rotation_disabled}
          aria-disabled={add_rotation_disabled}
          aria-label={
            selected_universe_live
              ? rotation_full && !daily_rotation_staged.includes(selected_universe_live)
                ? "Daily rotation is full (4/4). Remove an asset first."
                : `Add ${selected_universe_live} to daily rotation`
              : "Select an asset to add to rotation"
          }
          className={cn(
            "inline-flex shrink-0 items-center justify-center rounded-[var(--radius-md)] px-4 py-2 text-sm font-semibold transition-[colors,opacity]",
            add_rotation_disabled
              ? "cursor-not-allowed bg-[var(--bg-elevated)] text-[var(--text-tertiary)]"
              : "bg-[var(--bg-elevated)] text-[var(--text-primary)] hover:border-[var(--border-accent)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent-cyan)]",
          )}
        >
          + Daily Rotation
          {daily_rotation_staged.length > 0 ? (
            <span className="ml-1 text-xs text-[var(--text-tertiary)]">{`(${String(daily_rotation_staged.length)}/4)`}</span>
          ) : null}
        </button>

        <button
          type="button"
          onClick={handle_view_signals}
          disabled={signals_disabled}
          aria-disabled={signals_disabled}
          aria-label={
            selected_universe_live
              ? `View signals for ${selected_universe_live}`
              : "Select an asset to view signals"
          }
          className={cn(
            "inline-flex flex-1 items-center justify-center rounded-[var(--radius-md)] border border-[var(--border)] px-4 py-2 text-sm font-semibold transition-[colors,opacity]",
            signals_disabled
              ? "cursor-not-allowed text-[var(--text-tertiary)]"
              : "text-[var(--accent-cyan)] hover:border-[var(--border-accent)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent-cyan)]",
          )}
        >
          Signals →
        </button>
      </div>

      <DailyRotationModal />
    </div>
  );
}
