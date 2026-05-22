/** JSON shapes for `/api/memory/*` (Pydantic `to_camel` on the wire). */

export interface LanceDBStatsWire {
  syncState: string;
  lagSeconds: number;
  vectorCount: number;
  lastSyncIso: string;
}

export interface RAGStatsWire {
  collectionName: string;
  totalDocuments: number;
  archivedCount: number;
  archivedPercent: number;
  activeDocuments: number;
  lancedb: LanceDBStatsWire;
  embeddingModel: string;
  embeddingDims: number;
  qdrantHealth: string;
  lancedbHealth: string;
  lastUpdatedIso: string;
  qdrantCollectionName?: string;
  qdrantPointsCount?: number | null;
  patternMemoryRows?: number;
}

export interface EscoreBandWire {
  label: string;
  rangeMin: number;
  rangeMax: number;
  count: number;
  percent: number;
  status: string;
}

export interface AgentZeroLastRunResultsWire {
  documentsScored?: number;
  archived?: number;
  retained?: number;
  errors?: number;
}

export interface AgentZeroLifecycleWire {
  lastRunIso: string | null;
  lastRunRelative: string;
  nextRunIso: string;
  nextRunRelative: string;
  lastRunResults: AgentZeroLastRunResultsWire | null;
  escoreDistribution: EscoreBandWire[];
  avgEscore: number;
  threshold: number;
  collectionHealthLabel: string;
}

export interface MemoryActivityEntryWire {
  id: string;
  timestamp: string;
  operation: string;
  asset: string;
  documentCount: number;
  latencyMs: number;
  result: string;
  detail?: string | null;
}

export interface ContradictionDetailWire {
  asset: string;
  statementA: string;
  statementAAge: string;
  statementB: string;
  statementBAge: string;
  resolution: string;
}

export interface VerifierStatsWire {
  documentsVerified: number;
  passed: number;
  passedPercent: number;
  repaired: number;
  repairedPercent: number;
  repairedDetail: string | null;
  flagged: number;
  flaggedPercent: number;
  flaggedDetail: string | null;
  rejected: number;
  rejectedPercent: number;
  rejectedDetail: string | null;
  contradictions: ContradictionDetailWire[];
}

export interface RetrievalHitWire {
  documentId: string;
  similarityScore: number;
  finalScore: number;
  asset: string;
  signalDecision: string;
  preview: string;
  userTags: string[];
}

export interface RetrievalEventWire {
  id: string;
  timestamp: string;
  asset: string;
  queryText: string;
  retrievalDepth: string;
  hitCount: number;
  hits: RetrievalHitWire[];
  error?: string | null;
}

export interface StorePreviewSignalWire {
  signalId: string;
  asset: string;
  decision: string;
  score: number;
  createdAtIso: string;
  reasoningPreview: string;
  userTags: string[];
}

export interface StorePreviewPatternWire {
  id: string;
  title: string;
  category: string;
  createdAtIso: string;
  contentPreview: string;
  userTags: string[];
}

export interface StorePreviewWire {
  signals: StorePreviewSignalWire[];
  patterns: StorePreviewPatternWire[];
}
