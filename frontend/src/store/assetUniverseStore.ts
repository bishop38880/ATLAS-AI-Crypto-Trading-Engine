import { create } from "zustand";

import { try_append_daily_rotation_staged } from "../lib/daily-rotation-staged";
import { MAX_DASHBOARD_MONITORED_ASSETS } from "../lib/dashboard-universe";
import { derive_pair_from_rotation_entry } from "../lib/dashboard-symbol";

function canonical_dashboard_pair(raw: string): string {
  return derive_pair_from_rotation_entry(raw.trim());
}

const DASHBOARD_ASSETS_STORAGE_KEY = "atlas.dashboard.assets.v1";

const LADDER_FOCUS_STORAGE_KEY = "atlas.dashboard.ladder_focus_slots.v1";

export type ExecutionLadderSlotNumber = 1 | 2 | 3 | 4 | 5 | 6;

export const EXECUTION_LADDER_SLOT_KEYS: ExecutionLadderSlotNumber[] = [1, 2, 3, 4, 5, 6];

function is_record(input: unknown): input is Record<string, unknown> {
  return typeof input === "object" && input !== null && !Array.isArray(input);
}

function coerce_ladder_slot_number(raw: unknown): ExecutionLadderSlotNumber | null {
  const numeric =
    typeof raw === "string" ? Number.parseInt(raw, 10) : typeof raw === "number" ? raw : NaN;
  if (!Number.isFinite(numeric) || !Number.isInteger(numeric)) {
    return null;
  }
  if (numeric < 1 || numeric > 6) {
    return null;
  }
  return numeric as ExecutionLadderSlotNumber;
}

function strip_other_slots_for_same_pair(
  next_map: Partial<Record<ExecutionLadderSlotNumber, string>>,
  keep_slot_index: ExecutionLadderSlotNumber,
  canonical_keep: string,
): void {
  const keep_upper = canonical_keep.toUpperCase();
  for (const slot_candidate of EXECUTION_LADDER_SLOT_KEYS) {
    if (slot_candidate === keep_slot_index) {
      continue;
    }
    const assigned = next_map[slot_candidate];
    if (assigned === undefined) {
      continue;
    }
    if (canonical_dashboard_pair(assigned).toUpperCase() === keep_upper) {
      delete next_map[slot_candidate];
    }
  }
}

function read_ladder_focus_from_storage(): Partial<Record<ExecutionLadderSlotNumber, string>> {
  if (typeof window === "undefined") {
    return {};
  }
  try {
    const raw = window.localStorage.getItem(LADDER_FOCUS_STORAGE_KEY);
    if (raw === null) {
      return {};
    }
    const parsed: unknown = JSON.parse(raw);
    if (!is_record(parsed)) {
      return {};
    }
    const out: Partial<Record<ExecutionLadderSlotNumber, string>> = {};
    for (const [slot_raw, pair_raw] of Object.entries(parsed)) {
      const slot_index = coerce_ladder_slot_number(slot_raw);
      if (slot_index === null) {
        continue;
      }
      if (typeof pair_raw !== "string" || pair_raw.trim().length === 0) {
        continue;
      }
      out[slot_index] = canonical_dashboard_pair(pair_raw);
    }
    return out;
  } catch {
    return {};
  }
}

function persist_ladder_focus_map(
  map_value: Partial<Record<ExecutionLadderSlotNumber, string>>,
): void {
  if (typeof window === "undefined") {
    return;
  }
  try {
    window.localStorage.setItem(LADDER_FOCUS_STORAGE_KEY, JSON.stringify(map_value));
  } catch {
    /* Hardened storage modes */
  }
}

function filter_ladder_focus_for_removed_dashboard_asset(
  map_value: Partial<Record<ExecutionLadderSlotNumber, string>>,
  removed_upper: string,
): Partial<Record<ExecutionLadderSlotNumber, string>> {
  const next_map: Partial<Record<ExecutionLadderSlotNumber, string>> = {};
  let mutated = false;

  for (const slot_index of EXECUTION_LADDER_SLOT_KEYS) {
    const assigned = map_value[slot_index];
    if (assigned === undefined) {
      continue;
    }

    if (canonical_dashboard_pair(assigned).toUpperCase() === removed_upper) {
      mutated = true;
      continue;
    }

    next_map[slot_index] = assigned;
  }

  return mutated ? next_map : map_value;
}

function read_persisted_dashboard_assets(): string[] {
  if (typeof window === "undefined") {
    return [];
  }
  try {
    const raw = window.localStorage.getItem(DASHBOARD_ASSETS_STORAGE_KEY);
    if (raw === null) {
      return [];
    }
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) {
      return [];
    }
    const pairs = parsed
      .filter((row): row is string => typeof row === "string")
      .map((row) => canonical_dashboard_pair(row));
    const seen = new Set<string>();
    const deduped: string[] = [];
    for (const pair of pairs) {
      const key = pair.toUpperCase();
      if (seen.has(key)) {
        continue;
      }
      seen.add(key);
      deduped.push(pair);
    }
    return deduped.slice(0, MAX_DASHBOARD_MONITORED_ASSETS);
  } catch {
    return [];
  }
}

function persist_dashboard_assets(assets: string[]): void {
  if (typeof window === "undefined") {
    return;
  }
  try {
    window.localStorage.setItem(DASHBOARD_ASSETS_STORAGE_KEY, JSON.stringify(assets));
  } catch {
    // Local storage can be unavailable in hardened browser modes.
  }
}

export interface AssetUniverseInteractionSlice {
  /** Canonical pair highlighted in Asset Universe panel (matches dashboard pair strings). */
  selectedUniverseAsset: string | null;

  /** Operator-selected assets appended to the dashboard beyond active rotation. */
  dashboardAssets: string[];

  pendingDashboardAsset: string | null;

  /**
   * When a PROMETHEUS ladder slot has no live position locally, remembers which extra pin
   * should surface as execution intent for that numbered slot — UI only, persisted client-side.
   */
  ladderFocusBySlot: Partial<Record<ExecutionLadderSlotNumber, string>>;

  /** Local staging only until `POST /api/executive/rotation-override`; not persisted server-side. */
  dailyRotationStaged: string[];

  isRotationModalOpen: boolean;

  setSelectedUniverseAsset: (asset: string | null) => void;
  openDashboardAssetPrompt: (asset: string) => void;
  confirmDashboardAsset: () => void;
  dismissDashboardAssetPrompt: () => void;
  removeDashboardAsset: (asset: string) => void;
  assignLadderFocusFromPinDrop: (
    slot_index: ExecutionLadderSlotNumber,
    dropped_raw_pair: string,
  ) => boolean;
  clearLadderFocusAssignment: (slot_index: ExecutionLadderSlotNumber) => void;

  addToDailyRotationStaged: (asset: string) => void;
  removeFromDailyRotationStaged: (asset: string) => void;
  clearDailyRotationStaged: () => void;
  setRotationModalOpen: (isOpen: boolean) => void;
}

export const useAssetUniverseStore = create<AssetUniverseInteractionSlice>((set) => ({
  selectedUniverseAsset: null,
  dashboardAssets: read_persisted_dashboard_assets(),
  ladderFocusBySlot: read_ladder_focus_from_storage(),
  pendingDashboardAsset: null,
  dailyRotationStaged: [],
  isRotationModalOpen: false,

  setSelectedUniverseAsset: (asset) =>
    set((state) => {
      if (asset === null) {
        return { selectedUniverseAsset: null };
      }
      if (state.selectedUniverseAsset === asset) {
        return { selectedUniverseAsset: null };
      }
      return { selectedUniverseAsset: asset };
    }),

  openDashboardAssetPrompt: (asset) => set({ pendingDashboardAsset: asset }),

  confirmDashboardAsset: () =>
    set((state) => {
      if (state.pendingDashboardAsset === null) {
        return state;
      }
      const canonical = canonical_dashboard_pair(state.pendingDashboardAsset);
      const already = state.dashboardAssets.some(
        (row) => canonical_dashboard_pair(row).toUpperCase() === canonical.toUpperCase(),
      );
      if (already) {
        return { pendingDashboardAsset: null };
      }
      if (state.dashboardAssets.length >= MAX_DASHBOARD_MONITORED_ASSETS) {
        return { pendingDashboardAsset: null };
      }
      const next_assets = [...state.dashboardAssets, canonical];
      persist_dashboard_assets(next_assets);
      return {
        dashboardAssets: next_assets,
        pendingDashboardAsset: null,
      };
    }),

  dismissDashboardAssetPrompt: () => set({ pendingDashboardAsset: null }),

  removeDashboardAsset: (asset) =>
    set((state) => {
      const target_upper = canonical_dashboard_pair(asset).toUpperCase();
      const next_assets = state.dashboardAssets.filter(
        (row) => canonical_dashboard_pair(row).toUpperCase() !== target_upper,
      );
      persist_dashboard_assets(next_assets);
      const next_focus_map = filter_ladder_focus_for_removed_dashboard_asset(state.ladderFocusBySlot, target_upper);
      if (next_focus_map !== state.ladderFocusBySlot) {
        persist_ladder_focus_map(next_focus_map);
      }
      return { dashboardAssets: next_assets, ladderFocusBySlot: next_focus_map };
    }),

  assignLadderFocusFromPinDrop: (
    slot_index: ExecutionLadderSlotNumber,
    dropped_raw_pair: string,
  ): boolean => {
    const canonical_drop = canonical_dashboard_pair(dropped_raw_pair);
    if (canonical_drop.trim().length === 0) {
      return false;
    }

    let accepted_flag = false;
    set((state) => {
      const stored_extra_pin_match = state.dashboardAssets.find((row_pinned) => {
        return canonical_dashboard_pair(row_pinned).toUpperCase() === canonical_drop.toUpperCase();
      });

      if (stored_extra_pin_match === undefined) {
        return state;
      }

      const canonical_value = canonical_dashboard_pair(stored_extra_pin_match);
      const next_map: Partial<Record<ExecutionLadderSlotNumber, string>> = {
        ...state.ladderFocusBySlot,
      };
      strip_other_slots_for_same_pair(next_map, slot_index, canonical_value);
      next_map[slot_index] = canonical_value;
      persist_ladder_focus_map(next_map);
      accepted_flag = true;

      return { ladderFocusBySlot: next_map };
    });

    return accepted_flag;
  },

  clearLadderFocusAssignment: (slot_index: ExecutionLadderSlotNumber) =>
    set((state) => {
      if (state.ladderFocusBySlot[slot_index] === undefined) {
        return state;
      }
      const next_map: Partial<Record<ExecutionLadderSlotNumber, string>> = { ...state.ladderFocusBySlot };
      delete next_map[slot_index];
      persist_ladder_focus_map(next_map);
      return { ladderFocusBySlot: next_map };
    }),

  addToDailyRotationStaged: (asset) =>
    set((state) => {
      const appended = try_append_daily_rotation_staged(state.dailyRotationStaged, asset);
      if (appended === null) {
        return state;
      }
      return { dailyRotationStaged: appended };
    }),

  removeFromDailyRotationStaged: (asset) =>
    set((state) => ({
      dailyRotationStaged: state.dailyRotationStaged.filter((row) => row !== asset),
    })),

  clearDailyRotationStaged: () => set({ dailyRotationStaged: [] }),

  setRotationModalOpen: (isOpen) => set({ isRotationModalOpen: isOpen }),
}));
