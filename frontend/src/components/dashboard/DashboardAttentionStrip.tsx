import { Link } from "@tanstack/react-router";
import { useMemo, type ReactElement } from "react";

import { calculate_dashboard_attention_items } from "../../lib/calculate-dashboard-attention";
import {
  calculate_dashboard_pairs,
  merge_dashboard_monitored_pairs,
} from "../../lib/dashboard-universe";
import type { DashboardPositionSlot } from "../../lib/dashboard-positions";
import { cn } from "../../lib/cn";
import { usePricesStore, useScoresStore, useSystemStore } from "../../store/index";
import { useAssetUniverseStore } from "../../store/assetUniverseStore";

export interface DashboardAttentionStripProps {
  readonly universe_pairs?: readonly string[] | undefined;
  readonly slot_snapshots: DashboardPositionSlot[];
}

function severity_class(severity: "critical" | "warn" | "info"): string {
  if (severity === "critical") {
    return "border-rose-500/40 bg-rose-950/30 text-rose-200";
  }
  if (severity === "warn") {
    return "border-amber-500/35 bg-amber-950/25 text-amber-100";
  }
  return "border-cyan-500/25 bg-cyan-950/20 text-cyan-100";
}

/** Compact actionable rows for veto, stale scores, socket posture, and unslotted signals. */
export function DashboardAttentionStrip(props: DashboardAttentionStripProps): ReactElement | null {
  const health = useSystemStore((state) => state.health);
  const scores_by_asset = useScoresStore((state) => state.scoresByAsset);
  const scores_connected = useScoresStore((state) => state.isConnected);
  const prices_connected = usePricesStore((state) => state.isConnected);
  const dashboard_assets = useAssetUniverseStore((state) => state.dashboardAssets);

  const pairs = useMemo(() => {
    const base_pairs = props.universe_pairs ?? calculate_dashboard_pairs(null);
    return merge_dashboard_monitored_pairs(base_pairs, dashboard_assets);
  }, [dashboard_assets, props.universe_pairs]);

  const items = useMemo(
    () =>
      calculate_dashboard_attention_items({
        health,
        pairs,
        scores_by_asset,
        slot_snapshots: props.slot_snapshots,
        scores_connected,
        prices_connected,
      }),
    [health, pairs, prices_connected, props.slot_snapshots, scores_by_asset, scores_connected],
  );

  if (items.length === 0) {
    return null;
  }

  return (
    <section
      aria-label="Attention required"
      className="flex flex-wrap items-center gap-2 rounded-lg border border-slate-800/90 bg-slate-950/40 px-3 py-2"
    >
      <span className="text-[10px] font-semibold uppercase tracking-[0.14em] text-slate-500">Attention</span>
      {items.map((item) => {
        const chip_class = cn(
          "inline-flex max-w-[min(22rem,42vw)] items-center gap-1 rounded-md border px-2 py-0.5 text-[10px] leading-snug",
          severity_class(item.severity),
        );

        if (item.href !== undefined) {
          return (
            <Link key={item.id} to={item.href} className={cn(chip_class, "transition-opacity hover:opacity-90")}>
              {item.severity === "critical" ? <span aria-hidden>✕</span> : null}
              <span className="truncate">{item.label}</span>
            </Link>
          );
        }

        return (
          <span key={item.id} className={chip_class}>
            <span className="truncate">{item.label}</span>
          </span>
        );
      })}
    </section>
  );
}
