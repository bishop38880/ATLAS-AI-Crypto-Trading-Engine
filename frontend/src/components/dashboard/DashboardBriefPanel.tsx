import { Link } from "@tanstack/react-router";
import type { ReactElement } from "react";

import { find_last_critical_entry } from "../../lib/telemetry-critical";
import { useSystemStore } from "../../store/index";
import { useTelemetryRailStore } from "../../stores/telemetryRailStore";
import { AITerminal } from "../executive/AITerminal";
import { CommandCard } from "../ui/CommandCard";

export function DashboardBriefPanel(): ReactElement {
  const health = useSystemStore((state) => state.health);
  const telemetry_entries = useTelemetryRailStore((state) => state.entries);
  const last_critical = find_last_critical_entry(telemetry_entries);

  const runner_up = health?.regimeContextRunnerUp?.trim();
  const transition_hint = health?.regimeContextTransitionHint?.trim();
  const explanation = health?.regimeContextExplanation?.trim();

  return (
    <div className="space-y-4">
      <CommandCard title="Live regime context" subtitle="From /ws/system — read-only">
        <div className="space-y-2 text-[11px] leading-snug text-slate-400">
          {runner_up !== undefined && runner_up.length > 0 ? (
            <p className="font-data tabular-nums text-slate-300">{runner_up}</p>
          ) : (
            <p className="text-slate-600">Waiting for runner-up from regime agent…</p>
          )}
          {transition_hint !== undefined && transition_hint.length > 0 ? (
            <p className="text-cyan-300/90">{transition_hint}</p>
          ) : null}
          {explanation !== undefined && explanation.length > 0 ? (
            <p className="text-slate-500">{explanation}</p>
          ) : null}
          <Link to="/regime" className="inline-block text-[10px] font-medium text-cyan-400 hover:text-cyan-300">
            Open Regime Center →
          </Link>
        </div>
      </CommandCard>

      {last_critical !== null ? (
        <CommandCard title="Last critical alert" subtitle="Pinned from live telemetry rail">
          <p
            className={
              last_critical.level === "error" ? "text-[11px] text-rose-300" : "text-[11px] text-amber-300"
            }
          >
            {last_critical.message}
          </p>
          <p className="mt-1 font-data text-[10px] tabular-nums text-slate-500">
            {last_critical.source} · {last_critical.isoTime.slice(11, 19)} UTC
          </p>
          <Link to="/monitoring" className="mt-2 inline-block text-[10px] font-medium text-cyan-400 hover:text-cyan-300">
            View monitoring →
          </Link>
        </CommandCard>
      ) : null}

      <AITerminal />
    </div>
  );
}
