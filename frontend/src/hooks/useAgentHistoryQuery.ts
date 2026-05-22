import { useQuery } from "@tanstack/react-query";

import { apiUrl } from "../lib/url";

export interface AgentHistoryCycle {
  totalScore: number;
  timestamp?: string;
  signalId?: string;
  finalDecision?: string;
  agentDirection?: string | null;
  agentScore?: number;
  agentMaxScore?: number;
  outcomeLabel?: string | null;
  correct?: boolean | null;
  featureScores?: Record<string, number>;
}

function is_record(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
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

function read_string(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

function read_bool_or_null(value: unknown): boolean | null | undefined {
  if (value === null || value === undefined) {
    return value === null ? null : undefined;
  }
  if (typeof value === "boolean") {
    return value;
  }
  return undefined;
}

function normalize_cycle(raw: Record<string, unknown>): AgentHistoryCycle {
  const feature_blob = raw.featureScores ?? raw.feature_scores;
  let featureScores: Record<string, number> | undefined;
  if (is_record(feature_blob)) {
    featureScores = {};
    for (const [key, cell] of Object.entries(feature_blob)) {
      featureScores[key] = Math.round(read_number(cell));
    }
  }

  const agent_score_raw = raw.agentScore ?? raw.agent_score;
  const agent_score =
    agent_score_raw !== undefined ? Math.round(read_number(agent_score_raw)) : Math.round(read_number(raw.totalScore ?? raw.total_score));

  const correct_raw = raw.correct;
  const correct_parsed = read_bool_or_null(correct_raw);

  return {
    totalScore: agent_score,
    timestamp: read_string(raw.timestamp ?? raw.ts ?? raw.cycleTs ?? raw.cycle_ts),
    signalId: read_string(raw.signalId ?? raw.signal_id),
    finalDecision: read_string(raw.finalDecision ?? raw.final_decision),
    agentDirection: (() => {
      const d = raw.agentDirection ?? raw.agent_direction;
      if (d === null) {
        return null;
      }
      const s = read_string(d);
      return s.length > 0 ? s : null;
    })(),
    agentScore: agent_score_raw !== undefined ? Math.round(read_number(agent_score_raw)) : undefined,
    agentMaxScore:
      raw.agentMaxScore !== undefined || raw.agent_max_score !== undefined
        ? Math.round(read_number(raw.agentMaxScore ?? raw.agent_max_score))
        : undefined,
    outcomeLabel: (() => {
      const o = raw.outcomeLabel ?? raw.outcome_label;
      if (o === null) {
        return null;
      }
      const s = read_string(o);
      return s.length > 0 ? s : null;
    })(),
    correct: correct_parsed === undefined ? null : correct_parsed,
    featureScores,
  };
}

export function parse_agent_history_payload(raw: unknown): AgentHistoryCycle[] {
  if (Array.isArray(raw)) {
    return raw.filter(is_record).map(normalize_cycle);
  }
  if (is_record(raw) && Array.isArray(raw.cycles)) {
    return raw.cycles.filter(is_record).map(normalize_cycle);
  }
  return [];
}

async function fetch_agent_history(agentName: string, asset: string): Promise<AgentHistoryCycle[]> {
  const path = `/api/agents/${encodeURIComponent(agentName)}/history?asset=${encodeURIComponent(asset)}`;
  const response = await fetch(apiUrl(path));
  if (!response.ok) {
    return [];
  }
  const raw: unknown = await response.json();
  return parse_agent_history_payload(raw);
}

export interface UseAgentHistoryQueryArgs {
  agentWsName: string | null;
  asset: string;
  enabled: boolean;
}

export function useAgentHistoryQuery({ agentWsName, asset, enabled }: UseAgentHistoryQueryArgs) {
  const safe_asset = asset.trim().length > 0 ? asset.trim().toUpperCase() : "BTC";
  const safe_agent = agentWsName?.trim() ?? "";

  return useQuery({
    queryKey: ["agents", "history_v2", safe_agent, safe_asset],
    queryFn: () => fetch_agent_history(safe_agent, safe_asset),
    enabled: enabled && safe_agent.length > 0,
    staleTime: 30_000,
  });
}
