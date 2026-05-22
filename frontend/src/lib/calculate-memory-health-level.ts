/** Map upstream health strings to StatusDot-compatible levels. */

export type MemoryHealthLevel = "healthy" | "degraded" | "error" | "offline";

export function calculate_service_health_level(raw: string): MemoryHealthLevel {
  const u = raw.trim().toUpperCase();
  if (u === "HEALTHY" || u === "OK" || u === "UP") {
    return "healthy";
  }
  if (u === "DEGRADED" || u === "WARNING" || u === "WARN") {
    return "degraded";
  }
  if (u === "FAILED" || u === "DOWN" || u === "ERROR") {
    return "error";
  }
  if (u === "OFFLINE" || u === "UNKNOWN" || u.length === 0) {
    return "offline";
  }
  return "offline";
}
