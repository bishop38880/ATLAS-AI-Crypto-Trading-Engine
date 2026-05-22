import { memo, useEffect, useMemo, useState, type ReactElement } from "react";

import { ProviderBrandLogo } from "./ProviderBrandLogo";
import { cn } from "../../lib/cn";
import { format_breaker_state_label, latency_color_class } from "../../lib/provider-health";
import { useProviderStore, type ProviderHealth } from "../../store/index";
import { Badge } from "../ui/Badge";
import { CircuitBreakerTimeline } from "./CircuitBreakerTimeline";
import { LatencySparkline } from "./LatencySparkline";
import { Spinner } from "../ui/Spinner";

function useSecondTick(): number {
  const [nowMs, setNowMs] = useState(() => Date.now());
  useEffect(() => {
    const id = window.setInterval(() => {
      setNowMs(Date.now());
    }, 1000);
    return () => window.clearInterval(id);
  }, []);
  return nowMs;
}

function calculate_health_bar_color(ratio: number): string {
  const t = Math.min(1, Math.max(0, ratio));
  const rLow = { r: 239, g: 83, b: 80 };
  const rMid = { r: 255, g: 167, b: 38 };
  const rHigh = { r: 0, g: 230, b: 118 };
  const lerp = (
    a: { r: number; g: number; b: number },
    b: { r: number; g: number; b: number },
    x: number,
  ) => ({
    r: Math.round(a.r + (b.r - a.r) * x),
    g: Math.round(a.g + (b.g - a.g) * x),
    b: Math.round(a.b + (b.b - a.b) * x),
  });
  let rgb: { r: number; g: number; b: number };
  if (t < 0.5) {
    rgb = lerp(rLow, rMid, t * 2);
  } else {
    rgb = lerp(rMid, rHigh, (t - 0.5) * 2);
  }
  return `rgb(${rgb.r} ${rgb.g} ${rgb.b})`;
}

export interface ProviderCardProps {
  provider: ProviderHealth;
  p99History: number[];
}

export const ProviderCard = memo(function ProviderCard({
  provider,
  p99History,
}: ProviderCardProps): ReactElement {
  const nowMs = useSecondTick();

  const isTestingAll = useProviderStore((s) => s.isTestingAll);
  const isTestingSlice = useProviderStore((s) => s.isTesting);
  const lastTestSlice = useProviderStore((s) => s.lastTestResults);
  const testProvider = useProviderStore((s) => s.testProvider);

  const anyTesting = useMemo(() => {
    return isTestingAll || Object.values(isTestingSlice).some(Boolean);
  }, [isTestingAll, isTestingSlice]);

  const tierBusy = Boolean(isTestingSlice[provider.name]);
  const lastTest = lastTestSlice[provider.name];

  const palette = useMemo(() => {
    switch (provider.state) {
      case "CLOSED":
        return { accent: "var(--success)", pulse: true };
      case "DEGRADED":
        return { accent: "var(--degraded)", pulse: false };
      case "OPEN":
        return { accent: "var(--danger)", pulse: false };
      case "HALF_OPEN":
        return { accent: "var(--warning)", pulse: true };
      default:
        return { accent: "var(--text-tertiary)", pulse: false };
    }
  }, [provider.state]);

  const healthRatio = useMemo(() => {
    return Math.min(1, Math.max(0, provider.healthScore));
  }, [provider.healthScore]);
  const barColor = useMemo(() => calculate_health_bar_color(healthRatio), [healthRatio]);

  const lastFetchSec =
    provider.lastFetchMs > 0 ? Math.max(0, (nowMs - provider.lastFetchMs) / 1000) : null;

  const failPct = Math.round(provider.failureRate * 100);
  const slowPct = Math.round(provider.slowCallRate * 100);
  const showRateLimit = provider.rateLimitMax > 0;
  const rlRatio = Math.min(1, provider.requestsPerMin / provider.rateLimitMax);
  const rlWarn = rlRatio > 0.8;

  const stateLabel = format_breaker_state_label(provider.state);

  return (
    <article
      className="flex flex-col rounded-[var(--radius-md)] border border-[var(--border)] bg-[var(--bg-surface)] p-4 shadow-sm"
      style={{ borderTopColor: palette.accent, borderTopWidth: 2 }}
    >
      <header className="mb-3 flex flex-wrap items-start justify-between gap-2">
        <div className="flex min-w-0 flex-1 flex-wrap items-center gap-2">
          <ProviderBrandLogo name={provider.name} className="h-5 w-5 shrink-0" />
          <span
            className={cn(
              "font-mono text-xs font-semibold tracking-tight",
              palette.pulse && "motion-safe:animate-pulse",
            )}
            style={{ color: palette.accent }}
          >
            {stateLabel}
          </span>
          <h3 className="truncate font-mono text-sm font-semibold text-[var(--text-primary)]">
            {provider.name}
          </h3>
        </div>
        <Badge variant="shadow" className="shrink-0 font-mono text-[10px] normal-case">
          Tier {provider.tier} #{provider.trustRank}
        </Badge>
        <button
          type="button"
          disabled={tierBusy || anyTesting}
          aria-busy={tierBusy ? true : undefined}
          aria-disabled={tierBusy || anyTesting ? true : undefined}
          aria-label={
            tierBusy ? `Testing ${provider.name}…` : `Test ${provider.name}`
          }
          onClick={() => {
            void testProvider(provider.name);
          }}
          className={cn(
            "inline-flex shrink-0 items-center gap-1.5 rounded border border-[var(--border)]",
            "bg-[var(--bg-elevated)] px-2 py-1 font-mono text-[10px] uppercase tracking-wide",
            "text-[var(--text-primary)] hover:bg-[var(--bg-overlay)] disabled:opacity-45",
            "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent-cyan)] focus-visible:ring-offset-2 focus-visible:ring-offset-[var(--bg-surface)]",
          )}
        >
          {tierBusy ? <Spinner className="h-3 w-3 shrink-0" /> : null}
          Test
        </button>
      </header>

      <div aria-live="polite" className="mb-2 min-h-[18px] text-[10px] text-[var(--text-secondary)]">
        {lastTest !== undefined ? (
          <span key={lastTest.tested_at}>
            Last manual test: {lastTest.latency_ms}
            ms · {lastTest.status}
            {lastTest.error !== null ? ` (${lastTest.error.slice(0, 80)})` : ""}
          </span>
        ) : (
          <span className="text-[var(--text-tertiary)]">Last manual test: —</span>
        )}
      </div>

      <div className="mb-2">
        <div className="mb-1 flex items-center justify-between gap-2">
          <span className="text-xs text-[var(--text-secondary)]">Health</span>
          <span className="font-mono text-[11px] tabular-nums text-[var(--text-primary)]">
            {healthRatio.toFixed(2)}
          </span>
        </div>
        <div className="h-2 overflow-hidden rounded-full bg-[var(--bg-elevated)]">
          <div
            className="h-full rounded-full transition-[width] duration-500 ease-out"
            style={{
              width: `${healthRatio * 100}%`,
              backgroundColor: barColor,
            }}
          />
        </div>
      </div>

      <p className="mb-2 font-mono text-[11px] text-[var(--text-secondary)]">
        Latency · p50:{" "}
        <span
          className={cn("tabular-nums", latency_color_class(provider.p50Ms))}
          aria-label={`${provider.name} p50 latency ${provider.p50Ms} milliseconds`}
        >
          {provider.p50Ms}ms
        </span>{" "}
        <span aria-hidden="true">→</span> p95: {provider.p95Ms}ms <span aria-hidden="true">→</span> p99:{" "}
        {provider.p99Ms}ms
      </p>

      <div className="mb-2 flex flex-wrap gap-1.5">
        <span className="rounded bg-[var(--bg-elevated)] px-2 py-0.5 font-mono text-[10px] text-[var(--text-secondary)]">
          {failPct}% fail ▼
        </span>
        <span className="rounded bg-[var(--bg-elevated)] px-2 py-0.5 font-mono text-[10px] text-[var(--text-secondary)]">
          {slowPct}% slow <span aria-hidden="true">&gt;</span>3s
        </span>
        <span className="rounded bg-[var(--bg-elevated)] px-2 py-0.5 font-mono text-[10px] text-[var(--text-secondary)]">
          {Math.round(provider.requestsPerMin)}/min req
        </span>
      </div>

      <p className="mb-2 font-mono text-[10px] leading-relaxed text-[var(--text-tertiary)]">
        Window: {provider.windowSize} calls <span aria-hidden="true">|</span> {provider.windowFailures}{" "}
        failures <span aria-hidden="true">|</span> {provider.windowSlowCalls} slow call
        {provider.windowSlowCalls === 1 ? "" : "s"}
      </p>

      <div className="flex flex-wrap items-center justify-between gap-2 border-t border-[var(--border)] pt-2 font-mono text-[10px] text-[var(--text-secondary)]">
        <span>
          Last fetch:{" "}
          {lastFetchSec !== null ? (
            <>
              {lastFetchSec.toFixed(1)}s ago <span aria-hidden="true">→</span>
            </>
          ) : (
            "—"
          )}
        </span>
        <span>
          Cache: <span className="text-[var(--text-primary)]">{provider.cacheStatus}</span>
        </span>
      </div>

      {showRateLimit && (
        <div className="mt-2">
          <div className="mb-0.5 flex items-center justify-between text-[10px] text-[var(--text-secondary)]">
            <span>
              Requests/min <span aria-hidden="true">→</span>
            </span>
            <span className={cn("tabular-nums", rlWarn && "text-[var(--warning)]")}>
              {Math.round(provider.requestsPerMin)}/{Math.round(provider.rateLimitMax)} (
              {Math.round(rlRatio * 100)}%)
            </span>
          </div>
          <div className="h-1.5 w-full overflow-hidden rounded-full bg-[var(--bg-elevated)]">
            <div
              className={cn(
                "h-full rounded-full",
                rlWarn ? "bg-[var(--warning)]" : "bg-[var(--accent-cyan)]",
              )}
              style={{ width: `${Math.min(100, rlRatio * 100)}%` }}
            />
          </div>
        </div>
      )}

      <div className="mt-2 flex items-center justify-between gap-2">
        <span className="text-[10px] text-[var(--text-tertiary)]">p99 trend</span>
        <LatencySparkline points={p99History} />
      </div>

      {(provider.state === "OPEN" || provider.state === "HALF_OPEN") && (
        <CircuitBreakerTimeline state={provider.state} lastFailureTs={provider.lastFailureTs} />
      )}
    </article>
  );
});
