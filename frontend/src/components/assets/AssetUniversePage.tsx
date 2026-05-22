import { useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useState, type ReactElement } from "react";

import {
  calculate_asset_universe_view_model,
  type AssetUniverseCoverage,
  type AssetUniverseRotationPayload,
  type AssetUniverseRow,
} from "../../lib/asset-universe";
import { MAX_DASHBOARD_MONITORED_ASSETS } from "../../lib/dashboard-universe";
import { cn } from "../../lib/cn";
import { apiUrl } from "../../lib/url";
import { useAssetUniverseStore } from "../../store/assetUniverseStore";
import { AssetLogo } from "../AssetLogo";
import { Badge, type BadgeVariant } from "../ui/Badge";
import { EmptyState } from "../ui/EmptyState";
import { Skeleton } from "../ui/Skeleton";

type CoverageFilter = "all" | AssetUniverseCoverage | "tier_one";

const coverage_label: Record<AssetUniverseCoverage, string> = {
  daily_8: "Daily 8",
  active_33: "Active 33",
  universe: "Universe",
  fallback: "Fallback",
};

const coverage_badge: Record<AssetUniverseCoverage, BadgeVariant> = {
  daily_8: "live",
  active_33: "open",
  universe: "shadow",
  fallback: "degraded",
};

const EMPTY_ASSET_ROWS: readonly AssetUniverseRow[] = [];

async function fetch_rotation_state_json(): Promise<AssetUniverseRotationPayload> {
  try {
    const response = await fetch(apiUrl("/api/rotation/state"), {
      headers: { Accept: "application/json" },
    });

    if (!response.ok) {
      return {};
    }

    const body: unknown = await response.json().catch(() => ({}));
    if (body === null || typeof body !== "object" || Array.isArray(body)) {
      return {};
    }
    return body as AssetUniverseRotationPayload;
  } catch {
    return {};
  }
}

function UniverseLoadingGrid(): ReactElement {
  return (
    <section className="space-y-4" aria-busy="true" aria-label="Asset universe loading">
      <Skeleton className="block h-10 w-72" height={40} />
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 md:grid-cols-4 xl:grid-cols-6">
        {Array.from({ length: 36 }).map((_, index) => (
          <Skeleton key={index} height={76} className="w-full" />
        ))}
      </div>
    </section>
  );
}

function AssetUniverseCard({
  row,
  is_on_dashboard,
  on_select,
}: {
  row: AssetUniverseRow;
  is_on_dashboard: boolean;
  on_select: (row: AssetUniverseRow) => void;
}): ReactElement {
  return (
    <button
      type="button"
      onClick={() => on_select(row)}
      className="rounded-[var(--radius-md)] border border-[var(--border)] bg-[var(--bg-surface)] p-3 text-left transition-[border-color,transform] hover:-translate-y-0.5 hover:border-[var(--accent-cyan)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent-cyan)]"
      aria-label={`Select ${row.pair} for dashboard`}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="flex min-w-0 flex-1 items-start gap-2">
          <AssetLogo symbol={row.pair} size="md" className="size-9 shrink-0" />
          <div className="min-w-0">
            <p className="font-data text-lg font-semibold text-[var(--text-primary)]">{row.base}</p>
            <p className="font-data text-[11px] uppercase tracking-wide text-[var(--text-tertiary)]">
              {row.pair}
            </p>
          </div>
        </div>
        <div className="flex shrink-0 flex-col items-end gap-2">
          <Badge variant={coverage_badge[row.coverage]}>{coverage_label[row.coverage]}</Badge>
          {is_on_dashboard ? <Badge variant="healthy">Dashboard</Badge> : null}
        </div>
      </div>

      <div className="mt-4 flex items-center justify-between gap-2 text-[11px] uppercase tracking-wide">
        <span className="text-[var(--text-tertiary)]">{row.symbol}</span>
        {row.is_tier_one ? (
          <span className="text-[var(--accent-cyan)]">Tier 1</span>
        ) : (
          <span className="text-[var(--text-tertiary)]">Rotation</span>
        )}
      </div>
    </button>
  );
}

function AddToDashboardDialog({
  asset,
  is_on_dashboard,
  cannot_add_more_pins,
  on_confirm,
  on_remove,
  on_cancel,
}: {
  asset: string;
  is_on_dashboard: boolean;
  /** True when the extra-pin list already holds ``MAX_DASHBOARD_MONITORED_ASSETS`` entries. */
  cannot_add_more_pins: boolean;
  on_confirm: () => void;
  on_remove: () => void;
  on_cancel: () => void;
}): ReactElement {
  useEffect(() => {
    const handle_escape = (event: KeyboardEvent): void => {
      if (event.key === "Escape") {
        on_cancel();
      }
    };
    window.addEventListener("keydown", handle_escape);
    return () => window.removeEventListener("keydown", handle_escape);
  }, [on_cancel]);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 px-4"
      role="presentation"
      onClick={on_cancel}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="add-dashboard-asset-title"
        className="w-full max-w-md rounded-2xl border border-[var(--border-accent)] bg-[var(--bg-surface)] p-5 shadow-[0_24px_80px_rgba(0,0,0,0.55)]"
        onClick={(event) => event.stopPropagation()}
      >
        <Badge variant={is_on_dashboard ? "healthy" : cannot_add_more_pins ? "degraded" : "live"}>
          {is_on_dashboard ? "Pinned" : cannot_add_more_pins ? "Dashboard full" : "Add asset"}
        </Badge>
        <h2 id="add-dashboard-asset-title" className="mt-3 text-lg font-semibold text-[var(--text-primary)]">
          {is_on_dashboard
            ? `Manage ${asset} on dashboard`
            : cannot_add_more_pins
              ? `Cannot add ${asset}`
              : `Add ${asset} to the dashboard?`}
        </h2>
        <p className="mt-2 text-sm leading-6 text-[var(--text-secondary)]">
          {is_on_dashboard
            ? "This pair is already pinned to your dashboard. You can remove it from the extra cards list, or close this dialog."
            : cannot_add_more_pins
              ? `The dashboard allows at most ${String(MAX_DASHBOARD_MONITORED_ASSETS)} pinned extra assets. Remove one from the dashboard before adding another.`
              : "This will pin the asset to your dashboard cards. Once the analysis engine publishes its next snapshot, the card will show that asset's confluence score."}
        </p>
        <div className="mt-5 flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
          <button
            type="button"
            onClick={on_cancel}
            className="rounded-[var(--radius-sm)] border border-[var(--border)] px-4 py-2 text-sm font-semibold text-[var(--text-secondary)] hover:border-[var(--border-hover)] hover:text-[var(--text-primary)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent-cyan)]"
          >
            Close
          </button>
          {is_on_dashboard ? (
            <button
              type="button"
              onClick={on_remove}
              className="rounded-[var(--radius-sm)] border border-[var(--danger)]/45 bg-[var(--danger)]/12 px-4 py-2 text-sm font-bold uppercase tracking-wide text-[var(--danger)] hover:bg-[var(--danger)]/20 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--danger)]"
            >
              Remove from dashboard
            </button>
          ) : (
            <button
              type="button"
              onClick={on_confirm}
              disabled={cannot_add_more_pins}
              aria-disabled={cannot_add_more_pins}
              title={
                cannot_add_more_pins
                  ? `Maximum ${String(MAX_DASHBOARD_MONITORED_ASSETS)} dashboard pins reached`
                  : undefined
              }
              className={
                cannot_add_more_pins
                  ? "cursor-not-allowed rounded-[var(--radius-sm)] border border-[var(--border)] bg-[var(--bg-elevated)] px-4 py-2 text-sm font-bold uppercase tracking-wide text-[var(--text-tertiary)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent-cyan)]"
                  : "rounded-[var(--radius-sm)] border border-[var(--accent-cyan)]/50 bg-[var(--accent-cyan)]/12 px-4 py-2 text-sm font-bold uppercase tracking-wide text-[var(--accent-cyan)] hover:bg-[var(--accent-cyan)]/20 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent-cyan)]"
              }
            >
              Add to dashboard
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

export function AssetUniversePage(): ReactElement {
  const [coverage_filter, set_coverage_filter] = useState<CoverageFilter>("all");
  const [query_text, set_query_text] = useState("");
  const dashboard_assets = useAssetUniverseStore((state) => state.dashboardAssets);
  const pending_dashboard_asset = useAssetUniverseStore((state) => state.pendingDashboardAsset);
  const open_dashboard_asset_prompt = useAssetUniverseStore((state) => state.openDashboardAssetPrompt);
  const confirm_dashboard_asset = useAssetUniverseStore((state) => state.confirmDashboardAsset);
  const dismiss_dashboard_asset_prompt = useAssetUniverseStore((state) => state.dismissDashboardAssetPrompt);
  const remove_dashboard_asset = useAssetUniverseStore((state) => state.removeDashboardAsset);

  const rotation_query = useQuery({
    queryKey: ["asset-universe", "rotation-state"],
    queryFn: fetch_rotation_state_json,
    staleTime: 10_000,
    refetchInterval: 30_000,
    select: calculate_asset_universe_view_model,
  });

  const view_model = rotation_query.data;
  const rows = view_model?.rows ?? EMPTY_ASSET_ROWS;

  const visible_rows = useMemo(() => {
    const normalized_query = query_text.trim().toUpperCase();

    return rows.filter((row) => {
      const matches_filter =
        coverage_filter === "all"
          ? true
          : coverage_filter === "tier_one"
            ? row.is_tier_one
            : row.coverage === coverage_filter;

      const matches_query =
        normalized_query.length === 0 ||
        row.base.includes(normalized_query) ||
        row.pair.includes(normalized_query) ||
        row.symbol.includes(normalized_query);

      return matches_filter && matches_query;
    });
  }, [coverage_filter, query_text, rows]);

  const dashboard_asset_lookup = useMemo(
    () => new Set(dashboard_assets.map((asset) => asset.toUpperCase())),
    [dashboard_assets],
  );

  if (rotation_query.isLoading) {
    return <UniverseLoadingGrid />;
  }

  return (
    <div className="space-y-6" aria-live="polite">
      {pending_dashboard_asset !== null ? (
        <AddToDashboardDialog
          asset={pending_dashboard_asset}
          is_on_dashboard={dashboard_asset_lookup.has(pending_dashboard_asset.toUpperCase())}
          cannot_add_more_pins={
            dashboard_assets.length >= MAX_DASHBOARD_MONITORED_ASSETS &&
            !dashboard_asset_lookup.has(pending_dashboard_asset.toUpperCase())
          }
          on_confirm={confirm_dashboard_asset}
          on_remove={() => {
            remove_dashboard_asset(pending_dashboard_asset);
            dismiss_dashboard_asset_prompt();
          }}
          on_cancel={dismiss_dashboard_asset_prompt}
        />
      ) : null}

      <header className="flex flex-col gap-3 lg:flex-row lg:items-end lg:justify-between">
        <div>
          <h1 className="text-2xl font-semibold text-[var(--text-primary)]">Asset Universe</h1>
          <p className="mt-2 max-w-3xl text-sm text-[var(--text-secondary)]">
            Redis-backed rotation snapshot across the full monitored universe, active 33 ladder, and daily 8 picks.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Badge variant={view_model?.is_stale ? "degraded" : "healthy"}>
            {view_model?.is_stale ? "Stale source" : "Live source"}
          </Badge>
          {rotation_query.isFetching ? (
            <span className="text-xs text-[var(--text-tertiary)]">Refreshing...</span>
          ) : null}
        </div>
      </header>

      <section className="grid gap-3 md:grid-cols-4">
        <div className="rounded-[var(--radius-md)] border border-[var(--border)] bg-[var(--bg-surface)] p-4">
          <p className="text-[11px] uppercase tracking-wide text-[var(--text-tertiary)]">Loaded</p>
          <p className="mt-2 font-data text-2xl font-semibold text-[var(--text-primary)]">{rows.length}</p>
        </div>
        <div className="rounded-[var(--radius-md)] border border-[var(--border)] bg-[var(--bg-surface)] p-4">
          <p className="text-[11px] uppercase tracking-wide text-[var(--text-tertiary)]">Active 33</p>
          <p className="mt-2 font-data text-2xl font-semibold text-[var(--accent-cyan)]">
            {view_model?.active_count ?? 0}
          </p>
        </div>
        <div className="rounded-[var(--radius-md)] border border-[var(--border)] bg-[var(--bg-surface)] p-4">
          <p className="text-[11px] uppercase tracking-wide text-[var(--text-tertiary)]">Daily 8</p>
          <p className="mt-2 font-data text-2xl font-semibold text-[var(--accent-teal)]">
            {view_model?.daily_count ?? 0}
          </p>
        </div>
        <div className="rounded-[var(--radius-md)] border border-[var(--border)] bg-[var(--bg-surface)] p-4">
          <p className="text-[11px] uppercase tracking-wide text-[var(--text-tertiary)]">Fetched</p>
          <p className="mt-2 truncate font-data text-sm text-[var(--text-secondary)]">
            {view_model?.fetched_at ?? "Fallback ladder"}
          </p>
        </div>
      </section>

      <section className="flex flex-col gap-3 rounded-2xl border border-white/5 bg-slate-950/25 p-4 md:flex-row md:items-center md:justify-between">
        <label className="panel-title flex flex-col gap-1">
          Search assets
          <input
            value={query_text}
            className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[var(--bg-base)] px-3 py-2 text-sm text-[var(--text-primary)] placeholder:text-[var(--text-tertiary)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent-cyan)]"
            placeholder="BTC, SOL, ETHUSDT..."
            aria-label="Search asset universe"
            onChange={(event) => set_query_text(event.target.value)}
          />
        </label>

        <label className="panel-title flex flex-col gap-1">
          Coverage
          <select
            value={coverage_filter}
            className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[var(--bg-base)] px-2 py-2 text-sm text-[var(--text-primary)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent-cyan)]"
            aria-label="Filter asset coverage"
            onChange={(event) => set_coverage_filter(event.target.value as CoverageFilter)}
          >
            <option value="all">All assets</option>
            <option value="daily_8">Daily 8</option>
            <option value="active_33">Active 33</option>
            <option value="universe">Full universe</option>
            <option value="tier_one">Tier 1 only</option>
            <option value="fallback">Fallback</option>
          </select>
        </label>
      </section>

      {visible_rows.length === 0 ? (
        <EmptyState
          title="No assets match this filter"
          description="Adjust the search text or coverage filter to inspect the loaded universe."
        />
      ) : (
        <div
          className={cn("grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3 2xl:grid-cols-4")}
          aria-label="Loaded asset universe"
        >
          {visible_rows.map((row) => (
            <AssetUniverseCard
              key={row.pair}
              row={row}
              is_on_dashboard={dashboard_asset_lookup.has(row.pair.toUpperCase())}
              on_select={(selected_row) => open_dashboard_asset_prompt(selected_row.pair)}
            />
          ))}
        </div>
      )}
    </div>
  );
}
