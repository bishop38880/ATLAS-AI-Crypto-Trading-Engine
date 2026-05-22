import { useNavigate } from "@tanstack/react-router";
import type { ReactElement } from "react";
import { memo, useMemo, useState } from "react";

import { derive_signal_route_slug } from "../../lib/dashboard-route-slug";
import { CONFLUENCE_SCORE_CAP } from "../../lib/confluence-score-constants";
import { formatDecisionGlyph } from "../../lib/format-decision-glyph";
import { format_last_compact_from_iso } from "../../lib/format-relative-age";
import { calculate_obti_feed_cell } from "../../lib/obti-feed-display";
import { calculate_signal_feed_csv } from "../../lib/signal-feed-csv";
import type { SignalFeedRow } from "../../lib/signal-feed-mapper";
import { cn } from "../../lib/cn";
import { AssetLogo } from "../AssetLogo";

export interface SignalFeedTableProps {
  rows: readonly SignalFeedRow[];
}

type DecisionFilter = "all" | "buy" | "sell" | "hold" | "none";
type SortMode = "time" | "score" | "asset";

function matches_decision_filter(decision: string, filter: DecisionFilter): boolean {
  if (filter === "all") {
    return true;
  }
  if (filter === "buy") {
    return decision.includes("Buy");
  }
  if (filter === "sell") {
    return decision.includes("Sell");
  }
  if (filter === "hold") {
    return decision === "Hold";
  }
  return decision === "No Position";
}

function SignalFeedTableInner({ rows: rowsProp }: SignalFeedTableProps): ReactElement {
  const rows = Array.isArray(rowsProp) ? rowsProp : [];
  const navigate = useNavigate();
  const [assetFilter, setAssetFilter] = useState<string>("all");
  const [decisionFilter, setDecisionFilter] = useState<DecisionFilter>("all");
  const [sortMode, setSortMode] = useState<SortMode>("time");

  const assetChoices = useMemo(() => {
    const uniq = new Set(rows.map((row) => row.asset));
    return Array.from(uniq).sort((a, b) => a.localeCompare(b));
  }, [rows]);

  const processed = useMemo(() => {
    let next = rows.filter((row) => (assetFilter === "all" ? true : row.asset === assetFilter));
    next = next.filter((row) => matches_decision_filter(row.decision, decisionFilter));

    const sorted = [...next];
    sorted.sort((a, b) => {
      if (sortMode === "asset") {
        return a.asset.localeCompare(b.asset);
      }
      if (sortMode === "score") {
        return b.totalScore - a.totalScore;
      }
      return Date.parse(b.timestamp) - Date.parse(a.timestamp);
    });
    return sorted;
  }, [rows, assetFilter, decisionFilter, sortMode]);

  const download_csv = (): void => {
    const csv = calculate_signal_feed_csv(processed);
    const blob = new Blob([csv], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `polaris-signal-feed-${new Date().toISOString().slice(0, 10)}.csv`;
    anchor.click();
    URL.revokeObjectURL(url);
  };

  const open_detail = (asset: string): void => {
    const slug = derive_signal_route_slug(asset);
    void navigate({ to: "/signals/$asset", params: { asset: slug } });
  };

  return (
    <section className="space-y-3" aria-live="polite">
      <div className="flex flex-wrap items-center gap-2">
        <label className="sr-only" htmlFor="signal-feed-asset-filter">
          Asset filter
        </label>
        <select
          id="signal-feed-asset-filter"
          value={assetFilter}
          onChange={(event) => setAssetFilter(event.target.value)}
          className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[var(--bg-elevated)] px-2 py-1 text-xs text-[var(--text-primary)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--accent-cyan)]"
        >
          <option value="all">All assets</option>
          {assetChoices.map((symbol) => (
            <option key={symbol} value={symbol}>
              {symbol}
            </option>
          ))}
        </select>

        <label className="sr-only" htmlFor="signal-feed-decision-filter">
          Decision filter
        </label>
        <select
          id="signal-feed-decision-filter"
          value={decisionFilter}
          onChange={(event) => setDecisionFilter(event.target.value as DecisionFilter)}
          className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[var(--bg-elevated)] px-2 py-1 text-xs text-[var(--text-primary)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--accent-cyan)]"
        >
          <option value="all">Decision: All</option>
          <option value="buy">Buy stack</option>
          <option value="sell">Sell stack</option>
          <option value="hold">Hold</option>
          <option value="none">No Position</option>
        </select>

        <label className="sr-only" htmlFor="signal-feed-sort">
          Sort mode
        </label>
        <select
          id="signal-feed-sort"
          value={sortMode}
          onChange={(event) => setSortMode(event.target.value as SortMode)}
          className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[var(--bg-elevated)] px-2 py-1 text-xs text-[var(--text-primary)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--accent-cyan)]"
        >
          <option value="time">Sort: Time</option>
          <option value="score">Sort: Score</option>
          <option value="asset">Sort: Asset</option>
        </select>

        <button
          type="button"
          onClick={download_csv}
          aria-label="Export filtered signal feed as CSV"
          className="ml-auto rounded-[var(--radius-sm)] border border-[var(--border-hover)] bg-[var(--bg-overlay)] px-3 py-1 text-[11px] font-bold uppercase tracking-wide text-[var(--text-primary)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--accent-cyan)]"
        >
          CSV export
        </button>
      </div>

      <div className="overflow-x-auto rounded-[var(--radius-md)] border border-[var(--border)]">
        <table className="min-w-full divide-y divide-[var(--border)] text-left text-sm">
          <thead className="bg-[var(--bg-surface)] text-[10px] font-bold uppercase tracking-wide text-[var(--text-tertiary)]">
            <tr>
              <th className="whitespace-nowrap px-3 py-2" scope="col">
                Asset
              </th>
              <th className="whitespace-nowrap px-3 py-2" scope="col">
                Time
              </th>
              <th className="whitespace-nowrap px-3 py-2" scope="col">
                Decision
              </th>
              <th className="whitespace-nowrap px-3 py-2" scope="col">
                Score
              </th>
              <th className="whitespace-nowrap px-3 py-2" scope="col">
                Conf
              </th>
              <th className="whitespace-nowrap px-3 py-2" scope="col">
                OBTI
              </th>
              <th className="whitespace-nowrap px-3 py-2" scope="col">
                Gate
              </th>
            </tr>
          </thead>
          <tbody className="divide-y divide-[var(--border)] bg-[var(--bg-elevated)]/40">
            {processed.map((row) => {
              const glyph = formatDecisionGlyph(row.decision);
              const obti = calculate_obti_feed_cell(row.obtiSummary, row.obtiSide);
              const passes = row.passesGate;
              const gate_glyph = passes ? "✓" : "✗";
              const gate_tone = passes ? "text-[var(--success)]" : "text-[var(--danger)]";
              return (
                <tr
                  key={`${row.asset}-${row.timestamp}-${String(row.cycleNumber ?? "")}`}
                  role="button"
                  tabIndex={0}
                  aria-label={`Open signal detail for ${row.asset}`}
                  className="cursor-pointer hover:bg-[rgba(20,27,38,0.55)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-[var(--accent-cyan)]"
                  onClick={() => open_detail(row.asset)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter" || event.key === " ") {
                      event.preventDefault();
                      open_detail(row.asset);
                    }
                  }}
                >
                  <td className="whitespace-nowrap px-3 py-2 font-semibold text-[var(--text-primary)]">
                    <span className="flex items-center gap-2">
                      <AssetLogo symbol={row.asset} size="sm" />
                      <span>{row.asset}</span>
                    </span>
                  </td>
                  <td className="whitespace-nowrap px-3 py-2 font-mono text-xs text-[var(--text-secondary)] tabular-nums">
                    {format_last_compact_from_iso(row.timestamp)}
                  </td>
                  <td className="px-3 py-2 text-[var(--text-primary)]">
                    <span aria-hidden className="mr-1 font-mono">
                      {glyph}
                    </span>
                    {row.decision}
                  </td>
                  <td className="whitespace-nowrap px-3 py-2 font-mono tabular-nums text-[var(--accent-cyan)]">
                    {row.totalScore}/{CONFLUENCE_SCORE_CAP}
                  </td>
                  <td className="whitespace-nowrap px-3 py-2 font-mono tabular-nums text-[var(--text-secondary)]">
                    {(row.confidence).toFixed(2)}
                  </td>
                  <td className="whitespace-nowrap px-3 py-2 font-mono text-xs text-[var(--text-secondary)]">
                    <span {...(obti.ariaLabel ? { "aria-label": obti.ariaLabel } : {})}>{obti.visual}</span>
                  </td>
                  <td className={cn("whitespace-nowrap px-3 py-2 font-mono text-xs", gate_tone)}>
                    <span aria-hidden>{gate_glyph}</span>
                    <span className="sr-only">{passes ? "Passes gate" : "Below gate"}</span>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}

export const SignalFeedTable = memo(SignalFeedTableInner);
