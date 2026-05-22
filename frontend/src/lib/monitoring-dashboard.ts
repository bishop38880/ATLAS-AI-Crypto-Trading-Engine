import type { BadgeVariant } from "../components/ui/Badge";
import type { StatusLevel } from "../components/ui/StatusDot";
import type {
  AlertEntry,
  MonitoringStepStatus,
  MonitoringStartupStatus,
  PipelineLatencyHistoryEntry,
  TestFloorHistoryEntry,
} from "../types/monitoring";

export function parse_monitoring_number(value: number | string | null | undefined): number {
  if (typeof value === "number") {
    return Number.isFinite(value) ? value : 0;
  }
  if (typeof value !== "string") {
    return 0;
  }
  const parsed = Number(value.replace(/,/g, ""));
  return Number.isFinite(parsed) ? parsed : 0;
}

export function calculate_ratio_percent(value: number | string, max: number | string): number {
  const parsedValue = parse_monitoring_number(value);
  const parsedMax = parse_monitoring_number(max);
  if (parsedMax <= 0) {
    return 0;
  }
  return Math.min(100, Math.max(0, (parsedValue / parsedMax) * 100));
}

export function startup_status_level(status: MonitoringStartupStatus | string): StatusLevel {
  if (status === "ALL_PASSED") {
    return "healthy";
  }
  if (status === "HAS_FAILURES") {
    return "error";
  }
  return "degraded";
}

export function step_status_level(status: MonitoringStepStatus | string): StatusLevel {
  if (status === "PASSED" || status === "READY" || status === "GREEN") {
    return "healthy";
  }
  if (status === "FAILED" || status === "RED" || status === "ERROR") {
    return "error";
  }
  if (status === "SKIPPED") {
    return "offline";
  }
  return "degraded";
}

export function alert_level_variant(alert: Pick<AlertEntry, "level">): BadgeVariant {
  const level = alert.level.toUpperCase();
  if (level === "ERROR" || level === "CRITICAL") {
    return "sell";
  }
  if (level === "WARN" || level === "WARNING") {
    return "degraded";
  }
  return "open";
}

export function alert_level_status(alert: Pick<AlertEntry, "level">): StatusLevel {
  const level = alert.level.toUpperCase();
  if (level === "ERROR" || level === "CRITICAL") {
    return "error";
  }
  if (level === "WARN" || level === "WARNING") {
    return "degraded";
  }
  return "healthy";
}

export function latency_history_values(
  history: readonly PipelineLatencyHistoryEntry[],
): number[] {
  const values: number[] = [];
  for (const entry of history) {
    const value =
      entry.latencyMs ??
      entry.durationMs ??
      entry.totalMs ??
      entry.p95Ms ??
      entry.value;
    if (typeof value === "number" && Number.isFinite(value)) {
      values.push(value);
    }
  }
  return values.reverse();
}

export function test_floor_history_values(
  history: readonly TestFloorHistoryEntry[],
): number[] {
  const values: number[] = [];
  for (const entry of history) {
    const value =
      entry.currentFloor ??
      entry.floor ??
      entry.passed ??
      entry.value;
    if (typeof value === "number" && Number.isFinite(value)) {
      values.push(value);
    }
  }
  return values.reverse();
}

export function format_monitoring_timestamp(value: string | undefined): string {
  if (value === undefined || value.trim() === "") {
    return "unknown";
  }
  const parsed = Date.parse(value);
  if (!Number.isFinite(parsed)) {
    return value;
  }
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(parsed));
}
