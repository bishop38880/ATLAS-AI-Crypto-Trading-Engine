/**
 * Wire shape for POST /api/v1/providers/.../test and /test-all.
 * Kept in lib for JSON parsing without import cycles with the Zustand store.
 */

export interface ProviderTestResult {
  provider: string;
  status: "healthy" | "degraded" | "error";
  latency_ms: number;
  error: string | null;
  circuit_state: "CLOSED" | "OPEN" | "HALF_OPEN" | "DEGRADED";
  tested_at: string;
}

const _STATUSES = new Set<string>(["healthy", "degraded", "error"]);
const _CIRCUITS = new Set<string>(["CLOSED", "OPEN", "HALF_OPEN", "DEGRADED"]);

export function parse_provider_test_result(raw: unknown): ProviderTestResult | null {
  if (typeof raw !== "object" || raw === null) {
    return null;
  }
  const row = raw as Record<string, unknown>;
  if (typeof row.provider !== "string") {
    return null;
  }
  const status = row.status;
  if (typeof status !== "string" || !_STATUSES.has(status)) {
    return null;
  }
  const latency = row.latency_ms;
  if (typeof latency !== "number" || !Number.isFinite(latency)) {
    return null;
  }
  const testedAt = row.tested_at;
  if (typeof testedAt !== "string") {
    return null;
  }
  const error = row.error;
  if (error !== null && typeof error !== "string") {
    return null;
  }
  const circuit = row.circuit_state;
  if (typeof circuit !== "string" || !_CIRCUITS.has(circuit)) {
    return null;
  }
  return {
    provider: row.provider,
    status: status as ProviderTestResult["status"],
    latency_ms: Math.trunc(latency),
    error,
    circuit_state: circuit as ProviderTestResult["circuit_state"],
    tested_at: testedAt,
  };
}

export function parse_provider_test_result_list(raw: unknown): ProviderTestResult[] {
  if (!Array.isArray(raw)) {
    return [];
  }
  const out: ProviderTestResult[] = [];
  for (const item of raw) {
    const row = parse_provider_test_result(item);
    if (row !== null) {
      out.push(row);
    }
  }
  return out;
}
