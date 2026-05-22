/** Row semantics for `/agents` scoreboard — distinct from veto/shadow overlays (session FE-5). */

export type AgentScoreboardRole = "CONFLUENCE" | "OVERLAY" | "VETO" | "SHADOW";

export interface AgentScoreboardCatalogEntry {
  id: string;
  label: string;
  role: AgentScoreboardRole;
  defaultMax: number;
  /** Exact normalised tokens matched against agent.name (see {@link normalize_agent_match_token}). */
  matchTokens: readonly string[];
}

export function normalize_agent_match_token(raw: string): string {
  return raw.toLowerCase().replace(/[^a-z0-9]/g, "");
}

/**
 * Canonical operator-facing ladder. Live Redis names win via {@link calculate_agent_scoreboard_rows}.
 * Maxima match FE-5 examples; websocket `maxPoints` overrides defaults when present.
 */
export const AGENT_SCOREBOARD_CATALOG: readonly AgentScoreboardCatalogEntry[] = [
  {
    id: "derivatives",
    label: "Derivatives Agent",
    role: "CONFLUENCE",
    defaultMax: 75,
    matchTokens: ["derivativesagent"],
  },
  {
    id: "technical",
    label: "Technical Agent",
    role: "CONFLUENCE",
    defaultMax: 45,
    matchTokens: ["technicalagent"],
  },
  {
    id: "onchain",
    label: "On-Chain Agent",
    role: "CONFLUENCE",
    defaultMax: 50,
    matchTokens: ["whalewatcheragent"],
  },
  {
    id: "sentiment",
    label: "Sentiment Agent",
    role: "CONFLUENCE",
    defaultMax: 30,
    matchTokens: ["socialagent"],
  },
  {
    id: "regime",
    label: "Regime Agent",
    role: "CONFLUENCE",
    defaultMax: 20,
    matchTokens: ["macroagent"],
  },
  {
    id: "liquidation",
    label: "Liquidation Agent",
    role: "OVERLAY",
    defaultMax: 15,
    matchTokens: ["liquidationagent"],
  },
  {
    id: "whale_overlay",
    label: "Whale Watcher",
    role: "OVERLAY",
    defaultMax: 10,
    matchTokens: ["whaleoverlayagent"],
  },
  {
    id: "funding",
    label: "Funding Rate Monitor",
    role: "OVERLAY",
    defaultMax: 12,
    matchTokens: ["fundingratemonitoragent", "fundingratemonitor"],
  },
  {
    id: "correlation",
    label: "Correlation Monitor",
    role: "OVERLAY",
    defaultMax: 8,
    matchTokens: ["correlationmonitor", "correlationagent"],
  },
  {
    id: "risk",
    label: "Risk Agent",
    role: "VETO",
    defaultMax: 0,
    matchTokens: ["risk"],
  },
  {
    id: "news_macro",
    label: "News & Macro",
    role: "SHADOW",
    defaultMax: 8,
    matchTokens: ["newsmacroagent", "macroresearchagent"],
  },
];
