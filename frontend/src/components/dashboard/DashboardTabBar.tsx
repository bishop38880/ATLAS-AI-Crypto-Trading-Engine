import type { ReactElement } from "react";

import { cn } from "../../lib/cn";

export type DashboardTab = "horizon" | "cycle" | "brief";

const TAB_LABELS: Record<DashboardTab, string> = {
  horizon: "Horizon",
  cycle: "Cycle",
  brief: "Brief",
};

export interface DashboardTabBarProps {
  readonly active: DashboardTab;
  readonly onChange: (tab: DashboardTab) => void;
}

export function DashboardTabBar(props: DashboardTabBarProps): ReactElement {
  const tabs: DashboardTab[] = ["horizon", "cycle", "brief"];

  return (
    <div
      className="inline-flex rounded-lg border border-slate-800/90 bg-slate-950/50 p-0.5"
      role="tablist"
      aria-label="Dashboard views"
    >
      {tabs.map((tab) => (
        <button
          key={tab}
          type="button"
          role="tab"
          aria-selected={props.active === tab}
          className={cn(
            "rounded-md px-3 py-1.5 text-[11px] font-semibold uppercase tracking-wide transition-colors",
            props.active === tab
              ? "bg-cyan-500/20 text-cyan-200 ring-1 ring-cyan-500/35"
              : "text-slate-500 hover:text-slate-300",
          )}
          onClick={() => props.onChange(tab)}
        >
          {TAB_LABELS[tab]}
        </button>
      ))}
    </div>
  );
}
