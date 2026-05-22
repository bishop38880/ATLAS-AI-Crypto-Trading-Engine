import {
  CONFLUENCE_CYCLE_INTERVAL_SECONDS,
  CONFLUENCE_SCORE_STALE_CYCLE_MULTIPLIER,
} from "./confluence-score-constants";
import { derive_pair_from_rotation_entry } from "./dashboard-symbol";
import { calculate_is_score_cycle_stale } from "./format-relative-age";
import { detects_dashboard_regime_mask } from "./regime-badge-display";
import { resolve_scores_snapshot_for_base } from "./resolve-scores-snapshot";
import { coerce_signal_decision_label, derive_signal_decision_from_total_score } from "./channel-mappers";
import { calculate_signal_filter_match } from "./signal-decision-display";
import type { DashboardPositionSlot } from "./dashboard-positions";
import type { ScoresPayload, SignalDecision, SystemHealth } from "../store/index";

export type DashboardAttentionSeverity = "critical" | "warn" | "info";

export interface DashboardAttentionItem {
  readonly id: string;
  readonly severity: DashboardAttentionSeverity;
  readonly label: string;
  readonly href?: string;
}

export interface DashboardAttentionInput {
  readonly health: SystemHealth | null;
  readonly pairs: readonly string[];
  readonly scores_by_asset: Map<string, ScoresPayload>;
  readonly slot_snapshots: DashboardPositionSlot[];
  readonly scores_connected: boolean;
  readonly prices_connected: boolean;
}

const MAX_PROMETHEUS_SLOTS = 6;

function resolve_decision(snapshot: ScoresPayload | undefined): SignalDecision {
  if (snapshot === undefined) {
    return "No Position";
  }
  return typeof snapshot.decision === "string"
    ? coerce_signal_decision_label(snapshot.decision, snapshot.totalScore)
    : derive_signal_decision_from_total_score(snapshot.totalScore);
}

function count_open_slots(slot_snapshots: DashboardPositionSlot[]): number {
  return slot_snapshots.filter(
    (slot) => typeof slot.asset === "string" && slot.asset.trim().length > 0,
  ).length;
}

function pair_in_slot(canonical_pair: string, slot_snapshots: DashboardPositionSlot[]): boolean {
  const upper = canonical_pair.toUpperCase();
  return slot_snapshots.some((slot) => {
    if (typeof slot.asset !== "string" || slot.asset.trim().length === 0) {
      return false;
    }
    return derive_pair_from_rotation_entry(slot.asset).toUpperCase() === upper;
  });
}

/** Derive operator-facing attention rows for the live horizon dashboard. */
export function calculate_dashboard_attention_items(input: DashboardAttentionInput): DashboardAttentionItem[] {
  const items: DashboardAttentionItem[] = [];

  if (input.health?.overallStatus === "HALTED") {
    items.push({
      id: "platform-halted",
      severity: "critical",
      label: "Platform halted — operator action required",
      href: "/monitoring",
    });
  } else if (input.health?.overallStatus === "DEGRADED") {
    items.push({
      id: "platform-degraded",
      severity: "warn",
      label: "Platform degraded — review providers and agents",
      href: "/providers",
    });
  }

  if (detects_dashboard_regime_mask(input.health)) {
    items.push({
      id: "regime-volatile-mask",
      severity: "warn",
      label: "HMM reads VOLATILE while bucket shows RANGING",
      href: "/regime",
    });
  }

  if (!input.scores_connected) {
    items.push({
      id: "scores-socket-down",
      severity: "warn",
      label: "Scores socket disconnected",
      href: "/monitoring",
    });
  }

  if (!input.prices_connected) {
    items.push({
      id: "prices-socket-down",
      severity: "warn",
      label: "Prices socket disconnected",
      href: "/monitoring",
    });
  }

  const veto_assets: string[] = [];
  const stale_assets: string[] = [];
  const ready_unslotted: string[] = [];

  for (const pair of input.pairs) {
    const canonical = derive_pair_from_rotation_entry(pair);
    const snapshot = resolve_scores_snapshot_for_base(input.scores_by_asset, canonical);
    if (snapshot === undefined) {
      continue;
    }

    if (snapshot.vetoActive) {
      veto_assets.push(canonical);
    }

    if (
      calculate_is_score_cycle_stale(
        snapshot.cycleTs,
        CONFLUENCE_CYCLE_INTERVAL_SECONDS,
        CONFLUENCE_SCORE_STALE_CYCLE_MULTIPLIER,
      )
    ) {
      stale_assets.push(canonical);
    }

    const decision = resolve_decision(snapshot);
    if (calculate_signal_filter_match(decision) && !pair_in_slot(canonical, input.slot_snapshots)) {
      ready_unslotted.push(canonical);
    }
  }

  if (veto_assets.length > 0) {
    const preview = veto_assets.slice(0, 3).join(", ");
    const suffix = veto_assets.length > 3 ? ` +${veto_assets.length - 3}` : "";
    items.push({
      id: "veto-active",
      severity: "warn",
      label: `Veto active · ${preview}${suffix}`,
    });
  }

  if (stale_assets.length > 0) {
    const preview = stale_assets.slice(0, 3).join(", ");
    const suffix = stale_assets.length > 3 ? ` +${stale_assets.length - 3}` : "";
    items.push({
      id: "stale-scores",
      severity: "warn",
      label: `Stale confluence · ${preview}${suffix}`,
    });
  }

  const open_slots = count_open_slots(input.slot_snapshots);
  if (ready_unslotted.length > 0 && open_slots < MAX_PROMETHEUS_SLOTS) {
    items.push({
      id: "signals-ready",
      severity: "info",
      label: `${ready_unslotted.length} signal${ready_unslotted.length === 1 ? "" : "s"} ready · ${MAX_PROMETHEUS_SLOTS - open_slots} slot${MAX_PROMETHEUS_SLOTS - open_slots === 1 ? "" : "s"} open`,
    });
  }

  return items.slice(0, 6);
}
