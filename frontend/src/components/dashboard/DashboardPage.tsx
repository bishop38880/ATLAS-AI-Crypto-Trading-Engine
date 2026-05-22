import { useEffect, useState } from "react";

import { useDashboardPinsSync } from "../../hooks/useDashboardPinsSync";
import { useDashboardAssetPairsQuery, useDashboardPositionSlotsQuery } from "../../hooks/useDashboardQueries";
import { calculate_dashboard_position_slots_from_payload } from "../../lib/dashboard-positions";
import { PageHeader } from "../ui/PageHeader";
import { DashboardAttentionStrip } from "./DashboardAttentionStrip";
import { DashboardBriefPanel } from "./DashboardBriefPanel";
import { DashboardCyclePanel } from "./DashboardCyclePanel";
import { DashboardGrid } from "./DashboardGrid";
import { DashboardHorizonSkeleton } from "./DashboardHorizonSkeleton";
import { DashboardTabBar, type DashboardTab } from "./DashboardTabBar";
import { DashboardTodayStrip } from "./DashboardTodayStrip";
import { PositionSlotsPanel } from "./PositionSlotsPanel";
import { useSystemStore } from "../../store/index";

const TAB_STORAGE_KEY = "polaris.dashboard.tab";

function read_stored_tab(): DashboardTab {
  if (typeof window === "undefined") {
    return "horizon";
  }
  const stored = window.localStorage.getItem(TAB_STORAGE_KEY);
  if (stored === "horizon" || stored === "cycle" || stored === "brief") {
    return stored;
  }
  return "horizon";
}

export function DashboardPage() {
  useDashboardPinsSync();

  const [active_tab, set_active_tab] = useState<DashboardTab>(() => read_stored_tab());

  const rotation_query = useDashboardAssetPairsQuery();
  const rotation_pairs = rotation_query.data;
  const position_query = useDashboardPositionSlotsQuery();
  const position_slots_snapshot =
    position_query.data ?? calculate_dashboard_position_slots_from_payload([]);

  const live_regime = useSystemStore((channel) => channel.health?.currentRegime ?? "UNKNOWN");

  const horizon_loading =
    position_query.isPending || (!rotation_query.isFetched && rotation_query.isFetching);

  useEffect(() => {
    window.localStorage.setItem(TAB_STORAGE_KEY, active_tab);
  }, [active_tab]);

  const tab_description =
    active_tab === "horizon"
      ? "Asset grid, position slots, and a one-line today summary — pins first, then the rotation ladder."
      : active_tab === "cycle"
        ? "Engine controls, stack launch, cycle countdown, and full status strip."
        : "AI terminal, live regime context from the socket stream, and the last critical alert.";

  return (
    <div className="space-y-6">
      <PageHeader
        kicker="Live horizon"
        title="8-Asset Live Horizon"
        description={tab_description}
        actions={<DashboardTabBar active={active_tab} onChange={set_active_tab} />}
      />

      {active_tab === "horizon" ? (
        horizon_loading ? (
          <DashboardHorizonSkeleton />
        ) : (
          <>
            <DashboardTodayStrip position_slots={position_slots_snapshot} />
            <DashboardAttentionStrip
              universe_pairs={rotation_pairs}
              slot_snapshots={position_slots_snapshot}
            />
            <PositionSlotsPanel slots={position_slots_snapshot} />
            <DashboardGrid
              universe_pairs={rotation_pairs}
              slot_snapshots={position_slots_snapshot}
              system_regime={live_regime}
            />
          </>
        )
      ) : null}

      {active_tab === "cycle" ? <DashboardCyclePanel position_slots={position_slots_snapshot} /> : null}

      {active_tab === "brief" ? <DashboardBriefPanel /> : null}
    </div>
  );
}
