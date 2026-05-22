import { useEffect, useMemo, useState, type ReactElement } from "react";

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
import { resolve_scores_snapshot_for_base } from "../../lib/resolve-scores-snapshot";
import { coerce_signal_decision_label, derive_signal_decision_from_total_score } from "../../lib/channel-mappers";
import { calculate_signal_filter_match } from "../../lib/signal-decision-display";
import { cn } from "../../lib/cn";
import type { PricePayload, SignalDecision } from "../../store/index";
import { usePricesStore, useScoresStore } from "../../store/index";
import { useAssetUniverseStore } from "../../store/assetUniverseStore";

import { AssetCard } from "./AssetCard";
import { Badge } from "../ui/Badge";

type PortfolioFilterMode = "all" | "tier1" | "position" | "signal";
type PortfolioSortMode = "score" | "change" | "funding" | "alpha";

const FILTER_STORAGE_KEY = "polaris.dashboard.grid.filter";
const SORT_STORAGE_KEY = "polaris.dashboard.grid.sort";

function read_stored_filter(): PortfolioFilterMode {
  if (typeof window === "undefined") {
    return "all";
  }
  const stored = window.localStorage.getItem(FILTER_STORAGE_KEY);
  if (stored === "all" || stored === "tier1" || stored === "position" || stored === "signal") {
    return stored;
  }
  return "all";
}

function read_stored_sort(): PortfolioSortMode {
  if (typeof window === "undefined") {
    return "score";
  }
  const stored = window.localStorage.getItem(SORT_STORAGE_KEY);
  if (stored === "score" || stored === "change" || stored === "funding" || stored === "alpha") {
    return stored;
  }
  return "score";
}

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

function FilterChip(props: {
  readonly label: string;
  readonly active: boolean;
  readonly onClick: () => void;
}): ReactElement {
  return (
    <button
      type="button"
      className={cn(
        "rounded px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide transition-colors",
        props.active
          ? "bg-cyan-500/20 text-cyan-200 ring-1 ring-cyan-500/35"
          : "bg-slate-900/60 text-slate-500 hover:text-slate-300",
      )}
      aria-pressed={props.active}
      onClick={props.onClick}
    >
      {props.label}
    </button>
  );
}

export interface DashboardGridProps {
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

export function DashboardGrid({ universe_pairs, slot_snapshots, system_regime }: DashboardGridProps) {
  const [portfolio_filter, set_portfolio_filter] = useState<PortfolioFilterMode>(() => read_stored_filter());
  const [portfolio_sort, set_portfolio_sort] = useState<PortfolioSortMode>(() => read_stored_sort());

  const scores_bundle = useScoresStore((channel) => channel.scoresByAsset);
  const price_bundle = usePricesStore((channel) => channel.prices);
  const dashboard_assets = useAssetUniverseStore((state) => state.dashboardAssets);

  const last_cycle_snapshot = useDashboardLastCycleHumanLabel();

  useEffect(() => {
    window.localStorage.setItem(FILTER_STORAGE_KEY, portfolio_filter);
  }, [portfolio_filter]);

  useEffect(() => {
    window.localStorage.setItem(SORT_STORAGE_KEY, portfolio_sort);
  }, [portfolio_sort]);

  const active_pairs_base = useMemo(() => {
    const base_pairs = universe_pairs ?? calculate_dashboard_pairs(null);
    return merge_dashboard_monitored_pairs(base_pairs, dashboard_assets);
  }, [dashboard_assets, universe_pairs]);

  const prepared_pairs = useMemo(() => {
    const rows = active_pairs_base.map((pair) => {
      const canonical_pair = derive_pair_from_rotation_entry(pair);
      const canonical_upper = canonical_pair.toUpperCase();

      const score_snapshot = resolve_scores_snapshot_for_base(scores_bundle, canonical_pair);

      const score_keys = calculate_symbol_keys_deduped(canonical_pair);

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

  return (
    <div className="space-y-4">
      <section
        aria-label="Dashboard filters"
        className="command-card command-card-accent-cyan flex flex-col gap-3 p-4 md:flex-row md:flex-wrap md:items-center md:justify-between"
      >
        <div className="space-y-2" aria-live="polite">
          <p className="text-[10px] font-semibold uppercase tracking-[0.14em] text-slate-500">Filter</p>
          <div className="flex flex-wrap gap-1">
            <FilterChip label="All" active={portfolio_filter === "all"} onClick={() => set_portfolio_filter("all")} />
            <FilterChip
              label="Tier 1"
              active={portfolio_filter === "tier1"}
              onClick={() => set_portfolio_filter("tier1")}
            />
            <FilterChip
              label="In slot"
              active={portfolio_filter === "position"}
              onClick={() => set_portfolio_filter("position")}
            />
            <FilterChip
              label="Signal"
              active={portfolio_filter === "signal"}
              onClick={() => set_portfolio_filter("signal")}
            />
          </div>
        </div>

        <div className="space-y-2">
          <p className="text-[10px] font-semibold uppercase tracking-[0.14em] text-slate-500">Sort</p>
          <div className="flex flex-wrap gap-1">
            <FilterChip label="Score" active={portfolio_sort === "score"} onClick={() => set_portfolio_sort("score")} />
            <FilterChip
              label="Change"
              active={portfolio_sort === "change"}
              onClick={() => set_portfolio_sort("change")}
            />
            <FilterChip
              label="Funding"
              active={portfolio_sort === "funding"}
              onClick={() => set_portfolio_sort("funding")}
            />
            <FilterChip
              label="A–Z"
              active={portfolio_sort === "alpha"}
              onClick={() => set_portfolio_sort("alpha")}
            />
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-3 md:ml-auto md:justify-end">
          <Badge variant={regime_tone}>{regime_text}</Badge>
          <span className="font-data text-[12px] text-[var(--text-secondary)]">{`Last cycle: ${last_cycle_snapshot}`}</span>
        </div>
      </section>

      <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
        {prepared_pairs.map((symbol_pair) => (
          <AssetCard
            key={symbol_pair}
            pair={symbol_pair}
            system_regime={system_regime}
            slot_lookup={slot_snapshots}
          />
        ))}
      </div>
    </div>
  );
}
