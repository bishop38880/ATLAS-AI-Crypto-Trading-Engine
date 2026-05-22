export type FundingFlipSignal = "COMPRESS_FROM_POSITIVE" | "COMPRESS_FROM_NEGATIVE" | "NEUTRAL";

export interface FundingHistoryPoint {
  ts: string;
  fundingTimeMs: number;
  rate: string;
}

export interface FundingCommandRow {
  asset: string;
  compactSymbol: string;
  fundingRate8h: string;
  annualizedSimplePct: string;
  zscore: number;
  flipSignal: FundingFlipSignal;
  flipStrength: number;
  flipExplanation: string;
  effectiveLongCarryPct: string;
  effectiveShortCarryPct: string;
  history: FundingHistoryPoint[];
  cacheHit: boolean;
}

export interface FundingCommandCenterPayload {
  generatedAt: string;
  rows: FundingCommandRow[];
  dashboardPairsUsed: string[];
}
