import { useMemo, useState } from "react";

import type { DashboardPositionSlot } from "../lib/dashboard-positions";
import { calculate_dashboard_pairs, calculate_is_tier_one_pair } from "../lib/dashboard-universe";
import {
  calculate_symbol_keys_deduped,
  derive_pair_from_rotation_entry,
} from "../lib/dashboard-symbol";
import { resolve_scores_snapshot_for_base } from "../lib/resolve-scores-snapshot";
import { coerce_signal_decision_label, derive_signal_decision_from_total_score } from "../lib/channel-mappers";
import { calculate_signal_filter_match } from "../lib/signal-decision-display";
import type { PricePayload, ScoresPayload, SignalDecision } from "../store/index";
import { usePricesStore, useScoresStore } from "../store/index";

export type PortfolioFilterMode = "all" | "tier1" | "position" | "signal";
export type PortfolioSortMode = "score" | "change" | "funding" | "alpha";

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

export interface UseDashboardPreparedPairsArgs {
  universe_pairs?: readonly string[] | undefined;
  slot_snapshots: DashboardPositionSlot[];
}

export interface UseDashboardPreparedPairsResult {
  portfolio_filter: PortfolioFilterMode;
  set_portfolio_filter: (value: PortfolioFilterMode) => void;
  portfolio_sort: PortfolioSortMode;
  set_portfolio_sort: (value: PortfolioSortMode) => void;
  prepared_pairs: string[];
}

export function useDashboardPreparedPairs({
  universe_pairs,
  slot_snapshots,
}: UseDashboardPreparedPairsArgs): UseDashboardPreparedPairsResult {
  const [portfolio_filter, set_portfolio_filter] = useState<PortfolioFilterMode>("all");
  const [portfolio_sort, set_portfolio_sort] = useState<PortfolioSortMode>("score");

  const scores_bundle = useScoresStore((channel) => channel.scoresByAsset);
  const price_bundle = usePricesStore((channel) => channel.prices);

  const active_pairs_base = universe_pairs ?? calculate_dashboard_pairs(null);

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

  return {
    portfolio_filter,
    set_portfolio_filter,
    portfolio_sort,
    set_portfolio_sort,
    prepared_pairs,
  };
}
