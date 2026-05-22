import { useQuery } from "@tanstack/react-query";

import { apiUrl } from "../lib/url";

export interface MarlShadowMetricRow {
  label: string;
  value: string;
  hint: string;
}

export interface MarlDeliberationViewModel {
  agentLabel: string;
  round1Points: number;
  round2Points: number;
  revisionRawPct: number;
  revisionClampedPct: number;
  checkpointName: string;
  shadowMetrics: MarlShadowMetricRow[];
  netScoreBefore: number;
  netScoreAfter: number;
  verdict: string;
}

export interface MarlLatestPayload {
  asset: string;
  enabled: boolean;
  shadowMode: boolean;
  deliberation: MarlDeliberationViewModel | null;
}

function read_string(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

function read_number(value: unknown, fallback = 0): number {
  if (typeof value === "number" && Number.isFinite(value)) {
    return value;
  }
  if (typeof value === "string" && value.trim().length > 0) {
    const n = Number(value);
    return Number.isFinite(n) ? n : fallback;
  }
  return fallback;
}

function is_record(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function parse_shadow_metrics(raw: unknown): MarlShadowMetricRow[] {
  if (!Array.isArray(raw)) {
    return [];
  }
  const rows: MarlShadowMetricRow[] = [];
  for (const cell of raw) {
    if (!is_record(cell)) {
      continue;
    }
    rows.push({
      label: read_string(cell.label ?? cell.name, "—"),
      value: read_string(cell.value ?? cell.display, "—"),
      hint: read_string(cell.hint ?? cell.note ?? cell.detail, ""),
    });
  }
  return rows;
}

export function parse_marl_latest_payload(raw: unknown, fallbackAsset: string): MarlLatestPayload {
  const record = is_record(raw) ? raw : {};
  const asset = read_string(record.asset, fallbackAsset);
  const enabled = typeof record.enabled === "boolean" ? record.enabled : false;
  const shadow_mode =
    typeof record.shadowMode === "boolean"
      ? record.shadowMode
      : typeof record.shadow_mode === "boolean"
        ? record.shadow_mode
        : true;

  const deliberation_raw = record.deliberation ?? record.payload;
  let deliberation: MarlDeliberationViewModel | null = null;

  if (is_record(deliberation_raw)) {
    deliberation = {
      agentLabel: read_string(deliberation_raw.agentLabel ?? deliberation_raw.agent_label ?? "Technical Agent"),
      round1Points: Math.round(read_number(deliberation_raw.round1Points ?? deliberation_raw.round_1_points)),
      round2Points: Math.round(read_number(deliberation_raw.round2Points ?? deliberation_raw.round_2_points)),
      revisionRawPct: read_number(deliberation_raw.revisionRawPct ?? deliberation_raw.revision_raw_pct),
      revisionClampedPct: read_number(deliberation_raw.revisionClampedPct ?? deliberation_raw.revision_clamped_pct),
      checkpointName: read_string(deliberation_raw.checkpointName ?? deliberation_raw.checkpoint_name ?? "—"),
      shadowMetrics: parse_shadow_metrics(deliberation_raw.shadowMetrics ?? deliberation_raw.shadow_metrics),
      netScoreBefore: Math.round(read_number(deliberation_raw.netScoreBefore ?? deliberation_raw.net_score_before)),
      netScoreAfter: Math.round(read_number(deliberation_raw.netScoreAfter ?? deliberation_raw.net_score_after)),
      verdict: read_string(deliberation_raw.verdict ?? deliberation_raw.verdictLabel ?? "UNCHANGED"),
    };
  }

  return {
    asset,
    enabled,
    shadowMode: shadow_mode,
    deliberation,
  };
}

async function fetch_marl_latest(asset: string): Promise<MarlLatestPayload> {
  const response = await fetch(apiUrl(`/api/marl/latest?asset=${encodeURIComponent(asset)}`));
  if (!response.ok) {
    throw new Error(`marl_latest_http_${response.status}`);
  }
  const raw: unknown = await response.json();
  return parse_marl_latest_payload(raw, asset);
}

export function useMarlLatestQuery(asset: string) {
  const trimmed = asset.trim().length > 0 ? asset.trim() : "BTC";
  return useQuery({
    queryKey: ["marl", "latest_v1", trimmed.toUpperCase()],
    queryFn: () => fetch_marl_latest(trimmed.toUpperCase()),
    staleTime: 10_000,
    refetchInterval: 15_000,
  });
}
