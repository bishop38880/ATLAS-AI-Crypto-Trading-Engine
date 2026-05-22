import { useQuery } from "@tanstack/react-query";

import { apiUrl } from "../lib/url";

import type { FundingCommandCenterPayload } from "../types/funding-command-center";

function coerce_command_center(raw: unknown): FundingCommandCenterPayload {
  const record =
    typeof raw === "object" && raw !== null ? (raw as Record<string, unknown>) : {};

  const generated_at_raw = record.generatedAt ?? record.generated_at;
  const pairs_raw = record.dashboardPairsUsed ?? record.dashboard_pairs_used;
  const rows_raw = record.rows ?? [];

  const generated_at =
    typeof generated_at_raw === "string" ? generated_at_raw : new Date(0).toISOString();

  const dashboard_pairs: string[] =
    Array.isArray(pairs_raw)
      ? pairs_raw.filter((symbol): symbol is string => typeof symbol === "string")
      : [];

  const rows: FundingCommandCenterPayload["rows"] = [];

  if (Array.isArray(rows_raw)) {
    for (const entry of rows_raw) {
      if (typeof entry !== "object" || entry === null) {
        continue;
      }

      const row = entry as Record<string, unknown>;
      const history_raw = row.history;
      const history_pairs: FundingCommandCenterPayload["rows"][number]["history"] = [];

      if (Array.isArray(history_raw)) {
        for (const point of history_raw) {
          if (typeof point !== "object" || point === null) {
            continue;
          }
          const p = point as Record<string, unknown>;
          const ts = typeof p.ts === "string" ? p.ts : "";
          const funding_ts = p.fundingTimeMs ?? p.funding_time_ms;
          const rate = typeof p.rate === "string" ? p.rate : "0";

          history_pairs.push({
            ts,
            fundingTimeMs:
              typeof funding_ts === "number" && Number.isFinite(funding_ts)
                ? Math.round(funding_ts)
                : 0,
            rate,
          });
        }
      }

      const flip = row.flipSignal ?? row.flip_signal;
      const flip_signal_normalized =
        flip === "COMPRESS_FROM_POSITIVE" ||
        flip === "COMPRESS_FROM_NEGATIVE" ||
        flip === "NEUTRAL"
          ? flip
          : "NEUTRAL";

      const cache_hit_cell = row.cacheHit ?? row.cache_hit;

      rows.push({
        asset: typeof row.asset === "string" ? row.asset : "",
        compactSymbol:
          typeof row.compactSymbol === "string"
            ? row.compactSymbol
            : typeof row.compact_symbol === "string"
              ? row.compact_symbol
              : "",
        fundingRate8h:
          typeof row.fundingRate8h === "string"
            ? row.fundingRate8h
            : typeof row.funding_rate_8h === "string"
              ? row.funding_rate_8h
              : "0",
        annualizedSimplePct:
          typeof row.annualizedSimplePct === "string"
            ? row.annualizedSimplePct
            : typeof row.annualized_simple_pct === "string"
              ? row.annualized_simple_pct
              : "0",
        zscore:
          typeof row.zscore === "number" && Number.isFinite(row.zscore) ? row.zscore : 0,
        flipSignal: flip_signal_normalized,
        flipStrength:
          typeof row.flipStrength === "number" && Number.isFinite(row.flipStrength)
            ? Math.round(row.flipStrength)
            : typeof row.flip_strength === "number" && Number.isFinite(row.flip_strength)
              ? Math.round(row.flip_strength)
              : 0,
        flipExplanation:
          typeof row.flipExplanation === "string"
            ? row.flipExplanation
            : typeof row.flip_explanation === "string"
              ? row.flip_explanation
              : "",
        effectiveLongCarryPct:
          typeof row.effectiveLongCarryPct === "string"
            ? row.effectiveLongCarryPct
            : typeof row.effective_long_carry_pct === "string"
              ? row.effective_long_carry_pct
              : "0",
        effectiveShortCarryPct:
          typeof row.effectiveShortCarryPct === "string"
            ? row.effectiveShortCarryPct
            : typeof row.effective_short_carry_pct === "string"
              ? row.effective_short_carry_pct
              : "0",
        history: history_pairs,
        cacheHit: typeof cache_hit_cell === "boolean" ? cache_hit_cell : false,
      });
    }
  }

  return {
    generatedAt: generated_at,
    rows,
    dashboardPairsUsed: dashboard_pairs,
  };
}

async function funding_command_center_fetch(): Promise<FundingCommandCenterPayload> {
  const response = await fetch(apiUrl("/api/funding/command-center"));
  if (!response.ok) {
    throw new Error("funding_center_fetch_failed");
  }
  const body: unknown = await response.json().catch(() => ({}));

  return coerce_command_center(body);
}

export function useFundingCommandCenterQuery() {
  return useQuery({
    queryKey: ["funding", "command_center_v1"],
    queryFn: funding_command_center_fetch,
    staleTime: 20_000,
    refetchInterval: 45_000,
  });
}
