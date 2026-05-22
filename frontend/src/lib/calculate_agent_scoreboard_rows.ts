import type { AgentScoreboardCatalogEntry } from "./agent-scoreboard-catalog";
import { AGENT_SCOREBOARD_CATALOG, normalize_agent_match_token } from "./agent-scoreboard-catalog";
import type { AgentStatus } from "../store/index";

export interface AgentScoreboardDisplayRow {
  catalog: AgentScoreboardCatalogEntry;
  liveName: string | null;
  score: number;
  max: number;
  status: AgentStatus["status"];
  vetoBlocking: boolean;
  explanation?: string;
}

function take_matching_agent(
  agents: AgentStatus[],
  consumed: Set<number>,
  matchTokens: readonly string[],
): AgentStatus | null {
  const targets = matchTokens.map((t) => normalize_agent_match_token(t));
  for (let i = 0; i < agents.length; i += 1) {
    if (consumed.has(i)) {
      continue;
    }
    const token = normalize_agent_match_token(agents[i].name);
    const hit = targets.some((t) => token === t);
    if (hit) {
      consumed.add(i);
      return agents[i];
    }
  }
  return null;
}

/** Merges live `/ws/agents` pulses with the fixed FE-5 catalog (one Redis row consumes at most one slot). */
export function calculate_agent_scoreboard_rows(agents: AgentStatus[]): AgentScoreboardDisplayRow[] {
  const consumed = new Set<number>();
  const rows: AgentScoreboardDisplayRow[] = [];

  for (const catalog of AGENT_SCOREBOARD_CATALOG) {
    const live = take_matching_agent(agents, consumed, catalog.matchTokens);

    if (catalog.role === "VETO") {
      rows.push({
        catalog,
        liveName: live?.name ?? null,
        score: 0,
        max: catalog.defaultMax,
        status: live?.status ?? "OFFLINE",
        vetoBlocking: Boolean(live?.veto),
        explanation: live?.explanation,
      });
      continue;
    }

    const max = live !== null && live.maxPoints > 0 ? live.maxPoints : catalog.defaultMax;

    rows.push({
      catalog,
      liveName: live?.name ?? null,
      score: live?.lastScore ?? 0,
      max,
      status: live?.status ?? "OFFLINE",
      vetoBlocking: false,
      explanation: live?.explanation,
    });
  }

  return rows;
}

/** Sum CONFLUENCE agent scores only — denominator for operators is always {@link CONFLUENCE_SCORE_CAP}. */
export function calculate_confluence_subtotal_score(rows: AgentScoreboardDisplayRow[]): number {
  let score = 0;
  for (const row of rows) {
    if (row.catalog.role === "CONFLUENCE") {
      score += row.score;
    }
  }
  return score;
}
