import { Link } from "@tanstack/react-router";
import type { ReactElement } from "react";

import { useNextCycleCountdown } from "../../hooks/useNextCycleCountdown";
import type { DashboardPositionSlot } from "../../lib/dashboard-positions";
import { find_last_critical_entry } from "../../lib/telemetry-critical";
import { cn } from "../../lib/cn";
import { useSystemStore } from "../../store/index";
import { useTelemetryRailStore } from "../../stores/telemetryRailStore";

import { RegimeStatusBadge } from "./RegimeStatusBadge";

const MAX_PROMETHEUS_SLOTS = 6;

export interface DashboardTodayStripProps {
  readonly position_slots: DashboardPositionSlot[];
}

export function DashboardTodayStrip(props: DashboardTodayStripProps): ReactElement {
  const health = useSystemStore((state) => state.health);
  const telemetry_entries = useTelemetryRailStore((state) => state.entries);
  const countdown = useNextCycleCountdown();

  const filled_slots = props.position_slots.filter(
    (slot) => typeof slot.asset === "string" && slot.asset.trim().length > 0,
  ).length;

  const open_positions = Math.max(filled_slots, health?.openPositions ?? 0);
  const last_critical = find_last_critical_entry(telemetry_entries);

  return (
    <div
      className="flex flex-wrap items-center gap-x-4 gap-y-2 rounded-lg border border-slate-800/90 bg-slate-950/45 px-3 py-2.5"
      aria-label="Today at a glance"
    >
      <span className="text-[10px] font-semibold uppercase tracking-[0.14em] text-slate-500">Today</span>

      <RegimeStatusBadge health={health ?? null} badgeClassName="text-[10px]" />

      <span className="font-data text-[11px] tabular-nums text-slate-300">
        <span className="text-slate-500">Positions </span>
        {open_positions}/{MAX_PROMETHEUS_SLOTS}
      </span>

      <span className="font-data text-[11px] tabular-nums text-cyan-300">
        <span className="text-slate-500">Next cycle </span>
        {countdown}
      </span>

      {last_critical !== null ? (
        <Link
          to="/monitoring"
          className={cn(
            "inline-flex max-w-[min(28rem,50vw)] items-center gap-1.5 truncate rounded-md border px-2 py-0.5 text-[10px] transition-colors hover:border-rose-500/50",
            last_critical.level === "error" ? "border-rose-500/35 text-rose-300" : "border-amber-500/35 text-amber-300",
          )}
          title={last_critical.message}
        >
          <span aria-hidden>{last_critical.level === "error" ? "✕" : "▲"}</span>
          <span className="truncate">{last_critical.message}</span>
        </Link>
      ) : (
        <span className="text-[10px] text-slate-600">No critical alerts</span>
      )}
    </div>
  );
}
