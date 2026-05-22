import type { ReactElement, ReactNode } from "react";

import { useMonitoringQueries } from "../../hooks/useMonitoringQueries";
import { calculate_sparkline_polyline_points } from "../../lib/calculate_sparkline_polyline";
import { cn } from "../../lib/cn";
import {
  alert_level_status,
  alert_level_variant,
  calculate_ratio_percent,
  format_monitoring_timestamp,
  latency_history_values,
  parse_monitoring_number,
  startup_status_level,
  step_status_level,
  test_floor_history_values,
} from "../../lib/monitoring-dashboard";
import type {
  AgentZeroScheduleData,
  AlertEntry,
  KeyMetricsData,
  PipelineLatencyData,
  StartupSequenceData,
  TestFloorData,
} from "../../types/monitoring";
import { Badge } from "../ui/Badge";
import { Card } from "../ui/Card";
import { DataValue } from "../ui/DataValue";
import { EmptyState } from "../ui/EmptyState";
import { ScoreBar } from "../ui/ScoreBar";
import { Skeleton } from "../ui/Skeleton";
import { Spinner } from "../ui/Spinner";
import { StatusDot } from "../ui/StatusDot";

export function MonitoringPage(): ReactElement {
  const queries = useMonitoringQueries();

  return (
    <div className="flex min-h-0 flex-col space-y-4">
      <header className="shrink-0">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0 flex-1">
            <h1 className="font-mono text-lg font-semibold tracking-tight text-[var(--text-primary)]">
              Monitoring
            </h1>
            <p className="mt-1 max-w-3xl text-xs leading-relaxed text-[var(--text-secondary)]">
              Startup checks, pipeline latency, API spend, Agent Zero cadence, and recent
              operational alerts for the ATLAS intelligence plane.
            </p>
          </div>
          {queries.isRefreshing ? (
            <span
              className="inline-flex items-center gap-2 rounded-md border border-[var(--border)] bg-[var(--bg-elevated)] px-3 py-1.5 text-[11px] text-[var(--text-secondary)]"
              aria-live="polite"
            >
              <Spinner className="h-3 w-3" />
              Refreshing
            </span>
          ) : null}
        </div>
      </header>

      {queries.isError ? (
        <p className="text-xs text-[var(--sell)]" role="status">
          One or more monitoring feeds failed to load. The dashboard will continue retrying.
        </p>
      ) : null}

      {queries.isLoading ? <MonitoringSkeleton /> : null}

      {!queries.isLoading ? (
        <>
          <MetricsSummary
            metrics={queries.metrics.data}
            testFloor={queries.testFloor.data}
            metricsFetchFailed={queries.metrics.isError}
          />
          <div className="grid gap-4 xl:grid-cols-[1.1fr_0.9fr]">
            <StartupSequencePanel startup={queries.startup.data} />
            <LatencyPanel latency={queries.latency.data} />
          </div>
          <div className="grid gap-4 xl:grid-cols-[0.9fr_1.1fr]">
            <TestFloorPanel testFloor={queries.testFloor.data} />
            <AgentZeroPanel agentZero={queries.agentZero.data} />
          </div>
          <AlertsPanel alerts={queries.alerts.data ?? []} />
        </>
      ) : null}
    </div>
  );
}

function MonitoringSkeleton(): ReactElement {
  return (
    <div className="space-y-4" aria-busy="true">
      <div className="grid gap-3 md:grid-cols-3 xl:grid-cols-6">
        {Array.from({ length: 6 }).map((_, index) => (
          <Skeleton key={index} height={96} rounded={false} className="rounded-[var(--radius-md)]" />
        ))}
      </div>
      <div className="grid gap-4 xl:grid-cols-2">
        <Skeleton height={280} rounded={false} className="rounded-[var(--radius-md)]" />
        <Skeleton height={280} rounded={false} className="rounded-[var(--radius-md)]" />
      </div>
    </div>
  );
}

function MetricsSummary(props: {
  metrics: KeyMetricsData | undefined;
  testFloor: TestFloorData | undefined;
  metricsFetchFailed: boolean;
}): ReactElement {
  const { metrics, testFloor, metricsFetchFailed } = props;
  if (metrics === undefined) {
    return (
      <EmptyState
        title={metricsFetchFailed ? "Could not load monitoring metrics" : "No monitoring metrics yet"}
        description={
          metricsFetchFailed
            ? "The monitoring request failed. Start the FastAPI app on the port your Vite dev server proxies /api to (defaults: uvicorn backend.main:app bound to host 127.0.0.1, port 8787). Inspect /api/monitoring/metrics in the browser Network panel."
            : "The metrics endpoint responded without a usable payload."
        }
      />
    );
  }

  const apiCost = parse_monitoring_number(metrics.apiCostToday);
  const apiCostCap = parse_monitoring_number(metrics.apiCostCap);
  const costPercent = calculate_ratio_percent(metrics.apiCostToday, metrics.apiCostCap);

  return (
    <div className="grid gap-3 md:grid-cols-3 xl:grid-cols-6">
      <MetricTile label="Uptime" value={metrics.uptimePercent} suffix="%" />
      <MetricTile label="Cycles" value={metrics.cycleCount} />
      <MetricTile label="Signals" value={metrics.signalsEmitted} />
      <MetricTile label="Avg Score" value={metrics.avgScore} />
      <MetricTile label="Test Floor" value={testFloor?.currentFloor ?? metrics.testFloor} />
      <Card className="min-h-[96px]" accent={costPercent >= 90 ? "amber" : "cyan"}>
        <div className="flex flex-col gap-3">
          <div>
            <p className="text-[11px] uppercase tracking-wide text-[var(--text-tertiary)]">
              API Cost
            </p>
            <DataValue value={apiCost.toFixed(2)} prefix="$" size="lg" />
            <p className="text-[11px] text-[var(--text-tertiary)]">
              cap ${apiCostCap.toFixed(2)}
            </p>
          </div>
          <ScoreBar value={costPercent} max={100} label="Cost cap used" showValue={false} size="sm" />
        </div>
      </Card>
    </div>
  );
}

function MetricTile(props: {
  label: string;
  value: number | string;
  suffix?: string;
}): ReactElement {
  return (
    <Card className="min-h-[96px]">
      <p className="text-[11px] uppercase tracking-wide text-[var(--text-tertiary)]">
        {props.label}
      </p>
      <DataValue value={props.value} suffix={props.suffix} size="lg" />
    </Card>
  );
}

function StartupSequencePanel(props: {
  startup: StartupSequenceData | undefined;
}): ReactElement {
  const { startup } = props;
  if (startup === undefined) {
    return <Card title="Startup Sequence"><PanelEmpty label="No startup data" /></Card>;
  }

  return (
    <Card
      title="Startup Sequence"
      subtitle={`Last run ${format_monitoring_timestamp(startup.lastRunIso)} in ${startup.totalSeconds.toFixed(2)}s`}
      accent={startup.overallStatus === "ALL_PASSED" ? "teal" : "amber"}
      headerActions={
        <StatusDot
          level={startup_status_level(startup.overallStatus)}
          label={startup.overallStatus.replace(/_/g, " ")}
        />
      }
    >
      <div className="space-y-3">
        {startup.steps.map((step) => (
          <div
            key={`${step.stepNumber}-${step.label}`}
            className="flex items-start justify-between gap-3 rounded-[var(--radius-sm)] border border-[var(--border)] bg-[var(--bg-elevated)] px-3 py-2"
          >
            <div className="min-w-0">
              <div className="flex items-center gap-2">
                <span className="font-mono text-[11px] text-[var(--text-tertiary)]">
                  {step.stepNumber.toString().padStart(2, "0")}
                </span>
                <p className="truncate text-sm font-medium text-[var(--text-primary)]">
                  {step.label}
                </p>
              </div>
              {step.detail !== undefined ? (
                <p className="mt-1 text-xs text-[var(--text-secondary)]">{step.detail}</p>
              ) : null}
            </div>
            <div className="flex shrink-0 items-center gap-3">
              <span className="font-data text-xs text-[var(--text-secondary)]">
                {step.durationSeconds.toFixed(2)}s
              </span>
              <StatusDot level={step_status_level(step.status)} label={step.status} />
            </div>
          </div>
        ))}
      </div>
    </Card>
  );
}

function LatencyPanel(props: {
  latency: PipelineLatencyData | undefined;
}): ReactElement {
  const { latency } = props;
  if (latency === undefined) {
    return <Card title="Pipeline Latency"><PanelEmpty label="No latency data" /></Card>;
  }

  const values = latency_history_values(latency.history);
  return (
    <Card
      title="Pipeline Latency"
      subtitle={`${latency.breachCount24h} breaches in 24h (${latency.breachPercent24h.toFixed(1)}%)`}
      accent={latency.breachCount24h > 0 ? "amber" : "cyan"}
      headerActions={<DataValue value={latency.targetMs} suffix="ms target" size="xs" />}
    >
      <div className="grid gap-3 sm:grid-cols-4">
        <LatencyValue label="P50" value={latency.current.p50Ms} />
        <LatencyValue label="P95" value={latency.current.p95Ms} />
        <LatencyValue label="P99" value={latency.current.p99Ms} />
        <LatencyValue label="Max" value={latency.current.maxMs} />
      </div>
      <Sparkline values={values} className="mt-5" />
    </Card>
  );
}

function LatencyValue(props: { label: string; value: number }): ReactElement {
  return (
    <div className="rounded-[var(--radius-sm)] bg-[var(--bg-elevated)] px-3 py-2">
      <p className="text-[11px] uppercase tracking-wide text-[var(--text-tertiary)]">
        {props.label}
      </p>
      <DataValue value={props.value} suffix="ms" size="sm" />
    </div>
  );
}

function TestFloorPanel(props: {
  testFloor: TestFloorData | undefined;
}): ReactElement {
  const { testFloor } = props;
  if (testFloor === undefined) {
    return <Card title="Test Floor"><PanelEmpty label="No test floor data" /></Card>;
  }

  const values = test_floor_history_values(testFloor.history);
  return (
    <Card
      title="Test Floor"
      subtitle={`ATH ${testFloor.allTimeHigh} - target ${testFloor.targetFloor} - ${testFloor.whenAchievedRelative}`}
      accent={testFloor.breached ? "amber" : "teal"}
      headerActions={
        <Badge variant={testFloor.breached ? "degraded" : "healthy"}>
          {testFloor.breached ? "breached" : "holding"}
        </Badge>
      }
    >
      <div className="grid gap-3 sm:grid-cols-3">
        <MetricTile label="Current" value={testFloor.currentFloor} />
        <MetricTile label="All-Time High" value={testFloor.allTimeHigh} />
        <MetricTile label="Target" value={testFloor.targetFloor} />
      </div>
      <Sparkline values={values} className="mt-5" />
    </Card>
  );
}

function AgentZeroPanel(props: {
  agentZero: AgentZeroScheduleData | undefined;
}): ReactElement {
  const { agentZero } = props;
  if (agentZero === undefined) {
    return <Card title="Agent Zero"><PanelEmpty label="No Agent Zero schedule" /></Card>;
  }

  return (
    <Card
      title="Agent Zero"
      subtitle={agentZero.scheduleDescription}
      accent="violet"
      headerActions={<Badge variant="shadow">{agentZero.scheduleCron}</Badge>}
    >
      <div className="grid gap-3 sm:grid-cols-2">
        <InfoRow label="Next run" value={agentZero.nextRunRelative || "unknown"} />
        <InfoRow label="Next run ISO" value={agentZero.nextRunIso || "unknown"} />
        <InfoRow label="Last status" value={agentZero.lastRun?.status ?? "not recorded"} />
        <InfoRow
          label="Last run"
          value={format_monitoring_timestamp(agentZero.lastRun?.timestamp)}
        />
      </div>
      {agentZero.lastRun?.error !== undefined ? (
        <p className="mt-3 rounded-[var(--radius-sm)] border border-[rgba(239,83,80,0.3)] bg-[rgba(239,83,80,0.08)] px-3 py-2 text-xs text-[var(--sell)]">
          {agentZero.lastRun.error}
        </p>
      ) : null}
    </Card>
  );
}

function AlertsPanel(props: { alerts: AlertEntry[] }): ReactElement {
  const { alerts } = props;
  return (
    <Card
      title="Recent Alerts"
      subtitle="Redis alert buffer with agent-health fallback"
      accent={alerts.length > 0 ? "amber" : "teal"}
      headerActions={<Badge variant={alerts.length > 0 ? "degraded" : "healthy"}>{alerts.length}</Badge>}
    >
      {alerts.length === 0 ? (
        <EmptyState
          title="No active alerts"
          description="No alert entries are present in the monitoring buffer."
          className="py-8"
        />
      ) : (
        <div className="space-y-2">
          {alerts.map((alert, index) => (
            <div
              key={alert.id ?? `${alert.timestamp}-${index}`}
              className="flex items-start gap-3 rounded-[var(--radius-sm)] border border-[var(--border)] bg-[var(--bg-elevated)] px-3 py-2"
            >
              <StatusDot level={alert_level_status(alert)} className="mt-1" />
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2">
                  <Badge variant={alert_level_variant(alert)}>{alert.level}</Badge>
                  {alert.source !== undefined ? (
                    <span className="text-[11px] text-[var(--text-tertiary)]">
                      {alert.source}
                    </span>
                  ) : null}
                  <span className="text-[11px] text-[var(--text-tertiary)]">
                    {format_monitoring_timestamp(alert.timestamp)}
                  </span>
                </div>
                <p className="mt-1 text-sm text-[var(--text-primary)]">{alert.message}</p>
              </div>
            </div>
          ))}
        </div>
      )}
    </Card>
  );
}

function Sparkline(props: { values: number[]; className?: string }): ReactElement {
  const { values, className } = props;
  if (values.length < 2) {
    return <PanelEmpty label="Not enough history for trend" className={className} />;
  }

  const sparkline = calculate_sparkline_polyline_points(values);
  return (
    <div className={cn("rounded-[var(--radius-sm)] bg-[var(--bg-elevated)] p-3", className)}>
      <div className="mb-2 flex items-center justify-between text-[11px] text-[var(--text-tertiary)]">
        <span>Trend</span>
        <span>
          {sparkline.min.toFixed(0)} - {sparkline.max.toFixed(0)}
        </span>
      </div>
      <svg viewBox="0 0 100 32" className="h-12 w-full overflow-visible" role="img" aria-label="Trend sparkline">
        <polyline
          points={sparkline.pointsAttr}
          fill="none"
          stroke="var(--accent-cyan)"
          strokeWidth="2"
          vectorEffect="non-scaling-stroke"
        />
      </svg>
    </div>
  );
}

function InfoRow(props: { label: string; value: ReactNode }): ReactElement {
  return (
    <div className="rounded-[var(--radius-sm)] bg-[var(--bg-elevated)] px-3 py-2">
      <p className="text-[11px] uppercase tracking-wide text-[var(--text-tertiary)]">
        {props.label}
      </p>
      <p className="mt-1 break-words font-mono text-xs text-[var(--text-primary)]">
        {props.value}
      </p>
    </div>
  );
}

function PanelEmpty(props: { label: string; className?: string }): ReactElement {
  return (
    <div
      className={cn(
        "rounded-[var(--radius-sm)] border border-dashed border-[var(--border)] px-4 py-8 text-center text-xs text-[var(--text-secondary)]",
        props.className,
      )}
    >
      {props.label}
    </div>
  );
}
