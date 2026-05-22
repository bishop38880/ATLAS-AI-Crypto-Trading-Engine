export type MonitoringStartupStatus = "ALL_PASSED" | "HAS_DEGRADED" | "HAS_FAILURES";

export type MonitoringStepStatus = "PASSED" | "DEGRADED" | "FAILED" | "SKIPPED";

export interface StartupSequenceStep {
  stepNumber: number;
  label: string;
  durationSeconds: number;
  status: MonitoringStepStatus | string;
  detail?: string;
}

export interface StartupSequenceData {
  lastRunIso: string;
  totalSeconds: number;
  overallStatus: MonitoringStartupStatus | string;
  steps: StartupSequenceStep[];
}

export interface PipelineLatencyCurrent {
  p50Ms: number;
  p95Ms: number;
  p99Ms: number;
  maxMs: number;
}

export interface PipelineLatencyHistoryEntry {
  timestamp?: string;
  cycleTs?: string;
  cycleId?: string;
  asset?: string;
  latencyMs?: number;
  durationMs?: number;
  totalMs?: number;
  p95Ms?: number;
  value?: number;
  status?: string;
}

export interface PipelineLatencyData {
  targetMs: number;
  breachCount24h: number;
  breachPercent24h: number;
  current: PipelineLatencyCurrent;
  history: PipelineLatencyHistoryEntry[];
}

export interface AgentZeroLastRun {
  timestamp?: string;
  status?: string;
  durationSeconds?: number;
  error?: string;
}

export interface AgentZeroScheduleData {
  scheduleCron: string;
  scheduleDescription: string;
  nextRunIso: string;
  nextRunRelative: string;
  lastRun: AgentZeroLastRun | null;
}

export type AlertLevel = "INFO" | "WARN" | "WARNING" | "ERROR" | "CRITICAL" | string;

export interface AlertEntry {
  id?: string;
  timestamp: string;
  level: AlertLevel;
  message: string;
  source?: string;
}

export interface CostBreakdownEntry {
  provider: string;
  costUsd: string;
  calls: number;
}

export interface KeyMetricsData {
  uptimePercent: string;
  cycleCount: number;
  signalsEmitted: number;
  avgScore: number;
  testFloor: number;
  apiCostToday: string;
  apiCostCap: string;
  costPercent: string;
  burnRateVs7DayAvg: string;
  costBreakdown: CostBreakdownEntry[];
}

export interface TestFloorHistoryEntry {
  timestamp?: string;
  currentFloor?: number;
  floor?: number;
  passed?: number;
  value?: number;
}

export interface TestFloorData {
  currentFloor: number;
  allTimeHigh: number;
  whenAchievedRelative: string;
  targetFloor: number;
  breached: boolean;
  history: TestFloorHistoryEntry[];
}
