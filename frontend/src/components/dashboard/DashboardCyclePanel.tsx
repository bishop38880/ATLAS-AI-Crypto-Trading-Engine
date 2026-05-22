import { Link } from "@tanstack/react-router";
import type { ReactElement } from "react";

import { useDashboardLastCycleHumanLabel } from "../../hooks/useDashboardLastCycle";
import { useSystemStore } from "../../store/index";
import { CommandCard } from "../ui/CommandCard";
import { DashboardAttentionStrip } from "./DashboardAttentionStrip";
import { EngineStartPanel } from "./EngineStartPanel";
import { FullStackLaunchCard } from "./FullStackLaunchCard";
import { SystemStatusBar } from "./SystemStatusBar";
import type { DashboardPositionSlot } from "../../lib/dashboard-positions";

export interface DashboardCyclePanelProps {
  readonly position_slots: DashboardPositionSlot[];
}

export function DashboardCyclePanel(props: DashboardCyclePanelProps): ReactElement {
  const last_cycle_label = useDashboardLastCycleHumanLabel();
  const cycle_count = useSystemStore((state) => state.health?.cycleCount);

  const cycle_count_label =
    typeof cycle_count === "number" && Number.isFinite(cycle_count)
      ? cycle_count.toLocaleString()
      : "—";

  return (
    <div className="space-y-4">
      <CommandCard title="Cycle posture" subtitle="Engine state, countdown, and last completed wave">
        <dl className="grid gap-3 sm:grid-cols-3">
          <div>
            <dt className="text-[10px] font-medium uppercase tracking-wide text-slate-500">Last cycle</dt>
            <dd className="mt-0.5 font-data text-sm tabular-nums text-slate-100">{last_cycle_label}</dd>
          </div>
          <div>
            <dt className="text-[10px] font-medium uppercase tracking-wide text-slate-500">Total cycles</dt>
            <dd className="mt-0.5 font-data text-sm tabular-nums text-slate-100">{cycle_count_label}</dd>
          </div>
          <div className="flex items-end">
            <Link
              to="/pipeline"
              className="btn-pill btn-pill-primary inline-flex font-data text-[11px]"
            >
              Open live pipeline trace →
            </Link>
          </div>
        </dl>
      </CommandCard>

      <FullStackLaunchCard />
      <EngineStartPanel />
      <SystemStatusBar position_slots={props.position_slots} />
      <DashboardAttentionStrip slot_snapshots={props.position_slots} />
    </div>
  );
}
