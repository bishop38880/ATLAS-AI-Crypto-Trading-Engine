import { Link, useRouterState } from "@tanstack/react-router";
import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactElement,
  type UIEvent,
} from "react";

import { useTelemetryRailIngest } from "../../hooks/useTelemetryRailIngest";
import { cn } from "../../lib/cn";
import {
  group_telemetry_entries_by_time,
  matches_telemetry_filter,
  type TelemetryFilter,
} from "../../lib/telemetry-rail-display";
import type { TelemetryRailEntry } from "../../stores/telemetryRailStore";
import { useTelemetryRailStore } from "../../stores/telemetryRailStore";
import { Badge } from "../ui/Badge";
import { Button } from "../ui/Button";
import { GlassPanel } from "../ui/GlassPanel";

const STORAGE_COLLAPSED_KEY = "polaris.telemetryRail.collapsed";

function format_entry_time(iso: string): string {
  try {
    return new Date(iso).toLocaleTimeString(undefined, {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hour12: false,
    });
  } catch {
    return iso.slice(11, 19);
  }
}

function resolve_message_tone(row: TelemetryRailEntry): string {
  const upper = row.message.toUpperCase();
  if (upper.includes("CRITICAL") || row.level === "error") {
    return "text-rose-300";
  }
  if (
    upper.includes("DEGRADED") ||
    upper.includes("WARNING") ||
    upper.includes("[WARN]") ||
    row.level === "warn"
  ) {
    return "text-amber-300";
  }
  return "text-slate-400";
}

function resolve_source_tone(row: TelemetryRailEntry): string {
  if (row.level === "error") {
    return "text-rose-400/90";
  }
  if (row.level === "warn") {
    return "text-amber-400/90";
  }
  return "text-slate-500";
}

function TelemetryLine({ row }: { row: TelemetryRailEntry }): ReactElement {
  const message_tone = resolve_message_tone(row);
  const source_tone = resolve_source_tone(row);

  return (
    <li className="rounded-md border border-slate-800/90 bg-slate-950/70 px-2 py-1.5 shadow-[inset_0_1px_0_rgba(255,255,255,0.03)]">
      <div className="flex items-baseline gap-1.5 text-[10px] leading-snug">
        <span className="shrink-0 font-data tabular-nums text-slate-500">{format_entry_time(row.isoTime)}</span>
        <span className={cn("shrink-0 font-data uppercase tracking-wide text-[9px]", source_tone)}>{row.source}</span>
      </div>
      <p className={cn("mt-1 text-[10px] leading-snug tracking-normal text-balance", message_tone)}>{row.message}</p>
    </li>
  );
}

function TimeBucketHeader(props: { readonly label: string }): ReactElement {
  return (
    <li
      aria-hidden={false}
      className="sticky top-0 z-[1] -mx-2 border-y border-slate-800/70 bg-slate-950/95 px-2 py-1 backdrop-blur-sm"
    >
      <p className="text-[9px] font-semibold uppercase tracking-[0.18em] text-slate-500">{props.label}</p>
    </li>
  );
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
        "rounded px-2 py-0.5 text-[9px] font-semibold uppercase tracking-wide transition-colors",
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

/** Collapsible telemetry column for WebSocket posture, agent deltas, provider breakers, and monitoring alerts. */
export function LiveTelemetryRail(): ReactElement {
  useTelemetryRailIngest();

  const pathname = useRouterState({ select: (state) => state.location.pathname });
  const on_regime_page = pathname === "/regime" || pathname.startsWith("/regime/");

  const entries = useTelemetryRailStore((s) => s.entries);
  const clear_feed = useTelemetryRailStore((s) => s.clear);

  const [collapsed, set_collapsed] = useState(() => {
    if (typeof window === "undefined") {
      return false;
    }
    return window.localStorage.getItem(STORAGE_COLLAPSED_KEY) === "1";
  });

  const [filter, set_filter] = useState<TelemetryFilter>("all");
  const [, set_time_tick] = useState(0);

  const list_ref = useRef<HTMLOListElement | null>(null);
  const pin_bottom_ref = useRef(true);

  useEffect(() => {
    if (!on_regime_page && filter === "regime") {
      set_filter("all");
    }
  }, [filter, on_regime_page]);

  useEffect(() => {
    const id = window.setInterval(() => set_time_tick((value) => value + 1), 5_000);
    return () => window.clearInterval(id);
  }, []);

  const visible_entries = useMemo(
    () => entries.filter((row) => matches_telemetry_filter(row, filter)),
    [entries, filter],
  );

  const time_buckets = useMemo(
    () => group_telemetry_entries_by_time(visible_entries),
    [visible_entries],
  );

  useEffect(() => {
    window.localStorage.setItem(STORAGE_COLLAPSED_KEY, collapsed ? "1" : "0");
  }, [collapsed]);

  useEffect(() => {
    const list_el = list_ref.current;
    if (list_el === null || !pin_bottom_ref.current) {
      return;
    }
    list_el.scrollTop = list_el.scrollHeight;
  }, [visible_entries.length, collapsed, time_buckets.length]);

  function on_list_scroll(ev: UIEvent<HTMLOListElement>): void {
    const t = ev.currentTarget;
    const gap = t.scrollHeight - t.scrollTop - t.clientHeight;
    pin_bottom_ref.current = gap < 56;
  }

  return (
    <GlassPanel
      glow
      className={cn(
        "relative flex min-h-0 shrink-0 flex-col overflow-hidden border-l border-cyan-500/15 transition-[width] duration-[var(--transition-base)]",
        collapsed ? "w-11" : "w-[min(22rem,max(17rem,16vw))]",
      )}
      aria-label="Live alerts and telemetry stream"
    >
      <div className="flex shrink-0 items-center gap-2 border-b border-[var(--border)] px-2 py-2">
        {collapsed ? null : (
          <div className="min-w-0 flex-1">
            <p className="command-card-title truncate text-[11px] uppercase tracking-[0.14em] text-slate-300">
              Live alerts
            </p>
            <p className="truncate text-[9px] leading-tight text-slate-500">
              Telemetry · agents · sockets
              {entries.length > 0 ? (
                <span className="ml-1 font-data text-slate-400">
                  · {visible_entries.length}/{entries.length}
                </span>
              ) : null}
            </p>
          </div>
        )}
        <div className={cn("flex items-center gap-1", collapsed && "mx-auto flex-col")}>
          {collapsed ? null : <Badge variant="live" className="text-[8px]">LIVE</Badge>}
          <Button
            variant="pill-ghost"
            size="xs"
            aria-expanded={!collapsed}
            aria-controls="telemetry-rail-log"
            className="font-data px-1.5"
            onClick={() => set_collapsed((c) => !c)}
          >
            {collapsed ? "◀" : "▶"}
          </Button>
        </div>
      </div>

      {collapsed ? (
        <button
          type="button"
          className="group flex flex-1 flex-col items-center justify-center gap-2 px-1 py-3 text-[9px] font-semibold uppercase tracking-[0.3em] text-slate-500 hover:text-cyan-200"
          aria-label="Expand live telemetry stream"
          onClick={() => set_collapsed(false)}
        >
          <span className="text-[var(--accent-cyan)] opacity-70 group-hover:opacity-100">●</span>
          <span className="text-[10px] tracking-[0.25em]" style={{ writingMode: "vertical-rl" }}>
            Telemetry
          </span>
        </button>
      ) : (
        <>
          <div className="flex shrink-0 flex-wrap gap-1 border-b border-slate-800/80 px-2 py-1.5">
            <FilterChip label="All" active={filter === "all"} onClick={() => set_filter("all")} />
            <FilterChip label="Scores" active={filter === "scores"} onClick={() => set_filter("scores")} />
            <FilterChip label="System" active={filter === "system"} onClick={() => set_filter("system")} />
            {on_regime_page ? (
              <FilterChip label="Regime" active={filter === "regime"} onClick={() => set_filter("regime")} />
            ) : null}
          </div>

          <ol
            id="telemetry-rail-log"
            ref={list_ref}
            onScroll={on_list_scroll}
            className="telemetry-log-fade min-h-0 flex-1 list-none space-y-1 overflow-y-auto overflow-x-hidden px-2 py-2"
          >
            {visible_entries.length === 0 ? (
              <li className="rounded-md border border-dashed border-slate-700 bg-slate-950/50 px-2 py-3 text-center text-[10px] text-slate-500">
                {entries.length === 0
                  ? "Waiting for WebSocket chatter, agent deltas, or monitoring alerts…"
                  : "No entries match this filter."}
              </li>
            ) : (
              time_buckets.flatMap((bucket) => {
                const header =
                  time_buckets.length > 1 ? (
                    <TimeBucketHeader key={`${bucket.label}-header`} label={bucket.label} />
                  ) : null;
                const lines = bucket.entries.map((row) => <TelemetryLine key={row.id} row={row} />);
                return header !== null ? [header, ...lines] : lines;
              })
            )}
          </ol>

          <div className="shrink-0 space-y-1.5 border-t border-[var(--border)] px-2 py-2">
            <div className="flex gap-2">
              <Button variant="pill" size="xs" className="flex-1 font-data" onClick={() => clear_feed()}>
                Clear
              </Button>
              <Link to="/monitoring" className="btn-pill btn-pill-primary flex-1 text-center font-data text-[10px]">
                Monitoring →
              </Link>
              <Link to="/providers" className="btn-pill flex-1 text-center font-data text-[10px]">
                Providers
              </Link>
            </div>
            <p className="text-[9px] leading-snug text-slate-500">
              Mirrors socket lifecycle, breaker moves, veto flips, and{" "}
              <code className="font-data text-slate-400">/api/monitoring/alerts</code>.
            </p>
          </div>
        </>
      )}
    </GlassPanel>
  );
}
