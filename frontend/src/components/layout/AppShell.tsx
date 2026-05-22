import { Link, useRouterState } from "@tanstack/react-router";
import { useEffect, useState, type ReactNode } from "react";

import { usePolarisRealtimeBootstrap } from "../../hooks/usePolarisRealtimeBootstrap";
import { cn } from "../../lib/cn";
import { AtlasLogo } from "../branding/AtlasLogo";
import { Badge } from "../ui/Badge";
import { Button } from "../ui/Button";
import { GlassPanel } from "../ui/GlassPanel";
import { StatusPill } from "../ui/StatusPill";
import { LiveTelemetryRail } from "./LiveTelemetryRail";
import {
  IconActivity,
  IconBarChart,
  IconCpu,
  IconDashboard,
  IconDatabase,
  IconGlobe,
  IconHeatmap,
  IconLineChart,
  IconMessage,
  IconNetwork,
  IconPipeline,
  IconSettings,
  IconShield,
  IconZap,
} from "./NavIcons";
import { CONFLUENCE_CYCLE_INTERVAL_SECONDS } from "../../lib/confluence-score-constants";

const CYCLE_START_SECONDS = CONFLUENCE_CYCLE_INTERVAL_SECONDS;

export interface AppShellProps {
  children: ReactNode;
}

interface NavEntry {
  to: string;
  label: string;
  icon: ReactNode;
  end?: boolean;
}

interface NavSection {
  label: string;
  items: NavEntry[];
}

const navSections: NavSection[] = [
  {
    label: "Command",
    items: [
      { to: "/", label: "Dashboard", icon: <IconDashboard />, end: true },
      { to: "/regime", label: "Regime Center", icon: <IconLineChart /> },
      { to: "/pipeline", label: "Pipeline", icon: <IconPipeline /> },
      { to: "/risk", label: "Risk Governor", icon: <IconShield /> },
      { to: "/signals", label: "Signal Feed", icon: <IconZap /> },
    ],
  },
  {
    label: "Trading",
    items: [
      { to: "/paper-trade", label: "Paper Trading", icon: <IconLineChart /> },
      { to: "/backtest", label: "Backtest", icon: <IconBarChart /> },
      { to: "/journal", label: "Trade Journal", icon: <IconBarChart /> },
      { to: "/assets", label: "Asset Universe", icon: <IconGlobe /> },
      { to: "/agents", label: "Agents", icon: <IconCpu /> },
    ],
  },
  {
    label: "Intelligence",
    items: [
      { to: "/providers", label: "Provider Health", icon: <IconActivity /> },
      { to: "/gnn", label: "GNN Intelligence", icon: <IconNetwork /> },
      { to: "/correlation", label: "Correlation", icon: <IconHeatmap /> },
      { to: "/hydra", label: "HYDRA", icon: <IconActivity /> },
      { to: "/memory", label: "Memory / RAG", icon: <IconDatabase /> },
      { to: "/omnibox", label: "OmniBox", icon: <IconMessage /> },
    ],
  },
  {
    label: "Ops",
    items: [
      { to: "/monitoring", label: "Monitoring", icon: <IconBarChart /> },
      { to: "/funding", label: "Funding Center", icon: <IconActivity /> },
    ],
  },
];

function formatCountdown(totalSeconds: number): string {
  const m = Math.floor(totalSeconds / 60);
  const s = totalSeconds % 60;
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

export function AppShell({ children }: AppShellProps) {
  usePolarisRealtimeBootstrap();

  const [collapsed, setCollapsed] = useState(false);
  const [remaining, setRemaining] = useState(CYCLE_START_SECONDS);
  const [flash, setFlash] = useState(false);

  const pathname = useRouterState({ select: (s) => s.location.pathname });

  useEffect(() => {
    const id = window.setInterval(() => {
      setRemaining((prev) => {
        if (prev <= 1) {
          setFlash(true);
          window.setTimeout(() => setFlash(false), 450);
          return CYCLE_START_SECONDS;
        }
        return prev - 1;
      });
    }, 1000);
    return () => window.clearInterval(id);
  }, []);

  return (
    <div className="flex h-screen min-h-0 flex-col gap-3 bg-[radial-gradient(ellipse_90%_70%_at_100%_-20%,rgba(34,211,238,0.06),transparent_55%),radial-gradient(ellipse_80%_60%_at_-10%_110%,rgba(15,23,42,0.95),transparent_50%),linear-gradient(to_bottom_right,#020617,#0f172a,#020617)] p-3 font-[family-name:var(--font-body)] text-slate-200">
      <GlassPanel glow className="flex shrink-0 items-center gap-3 px-4 py-2">
        <div className="flex min-w-0 flex-1 flex-wrap items-center gap-x-4 gap-y-2">
          <Link
            to="/"
            aria-label="Home"
            className="flex shrink-0 items-center rounded-[var(--radius-sm)] outline-offset-2 focus-visible:outline focus-visible:outline-2 focus-visible:outline-[var(--accent-cyan)]"
          >
            <AtlasLogo className="h-auto w-auto max-h-[168px] max-w-[838px]" alt="" aria-hidden />
          </Link>
          <div className="flex shrink-0 items-baseline gap-0.5 text-lg font-semibold tracking-tight text-slate-100">
            <span>P</span>
            <span className="text-cyan-400">O</span>
            <span>L</span>
            <span>A</span>
            <span>R</span>
            <span>I</span>
            <span>S</span>
          </div>

          <div className="hidden sm:flex">
            <StatusPill level="healthy" label="HEALTHY" />
          </div>

          <Badge variant="bull" className="hidden sm:inline-flex">
            BULL ▲
          </Badge>

          <div
            className={cn(
              "hidden rounded-md border border-slate-800 bg-slate-900/60 px-2.5 py-1 font-data text-xs tabular-nums text-slate-400 md:block",
              flash && "cycle-timer-flash border-cyan-500/30 text-cyan-200",
            )}
          >
            Next cycle {formatCountdown(remaining)}
          </div>
        </div>
      </GlassPanel>

      <div className="flex min-h-0 min-w-0 flex-1 gap-3">
        <GlassPanel
          className={cn(
            "flex min-h-0 shrink-0 flex-col overflow-hidden transition-[width] duration-[var(--transition-base)]",
            collapsed ? "w-[60px]" : "w-[220px]",
          )}
        >
          <nav className="flex min-h-0 flex-1 flex-col overflow-y-auto p-2" aria-label="Primary">
            {navSections.map((section) => (
              <div key={section.label} className="mb-1">
                {!collapsed ? <p className="nav-section-label">{section.label}</p> : null}
                <div className="flex flex-col gap-0.5">
                  {section.items.map((item) => {
                    const active =
                      item.end === true
                        ? pathname === item.to
                        : pathname === item.to || pathname.startsWith(`${item.to}/`);
                    return (
                      <Link
                        key={item.to}
                        to={item.to}
                        title={collapsed ? item.label : undefined}
                        className={cn("nav-item", active && "nav-item-active")}
                      >
                        {item.icon}
                        {!collapsed ? <span className="truncate">{item.label}</span> : null}
                      </Link>
                    );
                  })}
                </div>
              </div>
            ))}
          </nav>

          <div className="border-t border-[var(--border)] p-2">
            <Link
              to="/settings"
              className={cn("nav-item", pathname.startsWith("/settings") && "nav-item-active")}
            >
              <IconSettings />
              {!collapsed && <span>Settings</span>}
            </Link>
            <Button
              variant="pill-ghost"
              size="xs"
              className="mt-2 w-full font-data"
              onClick={() => setCollapsed((c) => !c)}
            >
              {collapsed ? "→" : "← Collapse"}
            </Button>
          </div>
        </GlassPanel>

        <GlassPanel glow className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden">
          <main className="min-h-0 flex-1 overflow-auto p-4 md:p-6">{children}</main>
        </GlassPanel>

        <LiveTelemetryRail />
      </div>
    </div>
  );
}
