/** REST `/api/dashboard/correlation-matrix` — camelCase wire format. */

export type PositionCorrelationLevel = "ok" | "warn" | "critical";

export interface PositionCorrelationRiskWire {
  readonly level: PositionCorrelationLevel;
  readonly message: string;
  readonly averagePairwiseCorrelation: number | null;
  readonly monitoredPositions: readonly string[];
}

export interface CorrelationClusterWire {
  readonly bases: readonly string[];
  readonly size: number;
  readonly maxAbsCorrelation: number;
}

export interface ExtremeCorrelationPairWire {
  readonly baseA: string;
  readonly baseB: string;
  readonly correlation: number;
}

export interface AssetCorrelationMatrixWire {
  readonly generatedAt: string;
  readonly pairs: readonly string[];
  readonly bases: readonly string[];
  readonly matrix14d: readonly (readonly (number | null)[])[];
  readonly matrix30d: readonly (readonly (number | null)[])[];
  readonly clusters30d: readonly CorrelationClusterWire[];
  readonly extremePairs30d: readonly ExtremeCorrelationPairWire[];
  readonly fetchErrors: Readonly<Record<string, string>>;
  readonly cacheTtlSeconds: number;
  readonly positionRisk14d: PositionCorrelationRiskWire;
  readonly positionRisk30d: PositionCorrelationRiskWire;
}

export function is_asset_correlation_matrix_wire(input: unknown): input is AssetCorrelationMatrixWire {
  if (typeof input !== "object" || input === null) {
    return false;
  }
  const row = input as Record<string, unknown>;
  return (
    typeof row.generatedAt === "string" &&
    Array.isArray(row.bases) &&
    Array.isArray(row.matrix14d) &&
    Array.isArray(row.matrix30d)
  );
}
