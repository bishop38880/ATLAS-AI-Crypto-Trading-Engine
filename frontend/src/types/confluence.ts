export interface SubSignal {
  value: string | number;
  flag: string;
}

export interface AgentResult {
  score: number;
  weight: number;
  sub_signals: Record<string, SubSignal>;
}

export interface ConfluenceStreamData {
  asset: string;
  timestamp: number;
  total_score: number;
  agents: {
    DerivativesAgent: AgentResult;
    WhaleWatcherAgent: AgentResult;
    TechnicalAgent: AgentResult;
    SocialAgent: AgentResult;
    MacroAgent: AgentResult;
  };
}

export type AgentName = keyof ConfluenceStreamData["agents"];

export type ConnectionStatus =
  | "connecting"
  | "open"
  | "closed"
  | "error";

export const CONFLUENCE_TOTAL_WEIGHT = 220;

export const AGENT_LABELS: Record<AgentName, string> = {
  DerivativesAgent: "Derivatives",
  WhaleWatcherAgent: "Whale Watcher",
  TechnicalAgent: "Technical",
  SocialAgent: "Social",
  MacroAgent: "Macro",
};
