/** OmniBox REST + SSE payload shapes (camelCase from API). */

export type OmniBoxRouteKey = "RAG_ONLY" | "MCP_ONLY" | "HYBRID" | "DIRECT";

export interface OmniBoxBudgetPayload {
  sessionQueriesUsed: number;
  sessionQueryLimit: number;
  sessionTokensUsed: number;
  sessionTokenLimit: number;
  estimatedCostUsd: string;
  dailyCostCapUsd: string;
  queriesThisMinute: number;
  queriesPerMinuteLimit: number;
  dailyCapReached: boolean;
  costCapResetHours: number | null;
  operatorOverridePath: string | null;
}

export interface QueryClassificationPayload {
  route: OmniBoxRouteKey;
  confidence: number;
  rationale?: string;
}

export type SourceOutcomeLabel = "RECORDED" | "OPEN";

export interface SourceCitationPayload {
  id: string;
  title: string;
  snippet?: string;
  asset?: string;
  timestampUtc?: string;
  score?: number;
  scoreMax?: number;
  decision?: string;
  outcomePct?: string;
  outcomeLabel?: SourceOutcomeLabel;
  url?: string;
}

export interface OmniBoxDonePayload {
  latencyMs: number;
  llmTier: string;
  liveDataSummary?: string | null;
}

export type OmniBoxSseEvent =
  | { type: "classification"; data: QueryClassificationPayload }
  | { type: "token"; data: string }
  | { type: "sources"; data: SourceCitationPayload[] }
  | { type: "done"; data: OmniBoxDonePayload };
