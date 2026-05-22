import { useQuery } from "@tanstack/react-query";
import { useEffect, useRef } from "react";

import { apiUrl } from "../lib/url";
import { wsManager } from "../lib/ws-manager";
import {
  useTelemetryRailStore,
  type TelemetryRailLevel,
} from "../stores/telemetryRailStore";
import type { AlertEntry } from "../types/monitoring";
import {
  useAgentStore,
  useProviderStore,
  useScoresStore,
  useSystemStore,
} from "../store/index";

const WS_CHANNELS = ["system", "agents", "providers", "scores", "prices"] as const;

const RAIL_STORAGE_KEY = "polaris.telemetryRail.ingest.enabled";

async function fetch_monitoring_alerts(): Promise<AlertEntry[]> {
  const response = await fetch(apiUrl("/api/monitoring/alerts"), {
    headers: { Accept: "application/json" },
  });
  if (!response.ok) {
    throw new Error(`telemetry_alerts_http_${String(response.status)}`);
  }
  return (await response.json()) as AlertEntry[];
}

function map_alert_level(raw: string | undefined): TelemetryRailLevel {
  const u = String(raw ?? "INFO").toUpperCase();
  if (u === "ERROR" || u === "CRITICAL") {
    return "error";
  }
  if (u === "WARN" || u === "WARNING") {
    return "warn";
  }
  return "info";
}

function alert_dedupe_key(row: AlertEntry): string {
  if (typeof row.id === "string" && row.id.trim().length > 0) {
    return row.id;
  }
  return `${row.timestamp}::${row.message}`;
}

function create_initial_ws_state_map(): Map<string, string> {
  const initial = new Map<string, string>();
  for (const channel of WS_CHANNELS) {
    initial.set(channel, wsManager.getState(channel));
  }
  return initial;
}

/**
 * Feeds the live telemetry rail from WebSocket posture, store deltas, and periodic monitoring alerts.
 * Mount once beside {@link AppShell} (or the rail).
 */
export function useTelemetryRailIngest(): void {
  const push = useTelemetryRailStore((s) => s.push);

  const alertsQuery = useQuery({
    queryKey: ["telemetry-rail", "monitoring-alerts"],
    queryFn: fetch_monitoring_alerts,
    refetchInterval: 12_000,
    staleTime: 8_000,
    /** One failed GET should not hammer the backend or clutter the browser console every 12s × retries. */
    retry: false,
  });

  const alertsPrimedRef = useRef(false);
  const seenAlertKeysRef = useRef<Set<string>>(new Set());

  useEffect(() => {
    const rows = alertsQuery.data;
    if (rows === undefined) {
      return;
    }
    const seen = seenAlertKeysRef.current;
    if (!alertsPrimedRef.current) {
      for (const row of rows) {
        seen.add(alert_dedupe_key(row));
      }
      alertsPrimedRef.current = true;
      return;
    }
    for (const row of rows) {
      const key = alert_dedupe_key(row);
      if (seen.has(key)) {
        continue;
      }
      seen.add(key);
      const src = typeof row.source === "string" && row.source.trim().length > 0 ? row.source : "alerts";
      const line =
        typeof row.level === "string" && row.level.length > 0
          ? `[${row.level}] ${row.message}`
          : row.message;
      push(map_alert_level(row.level), src, line);
    }
  }, [alertsQuery.data, push]);

  const wsPrevRef = useRef<Map<string, string>>(create_initial_ws_state_map());

  useEffect(() => {
    const id = window.setInterval(() => {
      for (const channel of WS_CHANNELS) {
        const next = wsManager.getState(channel);
        const was = wsPrevRef.current.get(channel) ?? "NONE";
        if (was === next) {
          continue;
        }
        if (next === "OPEN" && was !== "OPEN") {
          push("info", "ws", `${channel} socket ready`);
        } else if (was === "OPEN" && next === "CONNECTING") {
          push("warn", "ws", `${channel} reconnecting…`);
        } else if (was === "OPEN" && (next === "CLOSED" || next === "CLOSING")) {
          push("warn", "ws", `${channel} socket closed — scheduled reconnect`);
        } else if (was !== "NONE" && next === "NONE") {
          push("warn", "ws", `${channel} link idle`);
        }
        wsPrevRef.current.set(channel, next);
      }
    }, 450);
    return () => window.clearInterval(id);
  }, [push]);

  useEffect(() => {
    const seeded = new Map(
      useAgentStore.getState().agents.map((row) => [
        row.name,
        { status: row.status, veto: row.veto },
      ]),
    );
    return useAgentStore.subscribe((state) => {
      const nextAgents = state.agents;
      for (const row of nextAgents) {
        const prior = seeded.get(row.name);
        if (prior !== undefined && prior.status !== row.status) {
          const level: TelemetryRailLevel =
            row.status === "OFFLINE" || row.status === "TIMEOUT" ? "error" : "warn";
          push(level, "agents", `${row.name}: ${prior.status} → ${row.status}`);
        }
        if (prior !== undefined && prior.veto !== row.veto) {
          push(row.veto ? "warn" : "info", "agents", `${row.name} veto ${row.veto ? "asserted" : "cleared"}`);
        }
        seeded.set(row.name, { status: row.status, veto: row.veto });
      }
      const alive = new Set(nextAgents.map((r) => r.name));
      for (const name of seeded.keys()) {
        if (!alive.has(name)) {
          seeded.delete(name);
        }
      }
    });
  }, [push]);

  useEffect(() => {
    const prevBreaker = new Map(
      useProviderStore.getState().providers.map((row) => [row.name, row.state]),
    );
    return useProviderStore.subscribe((state) => {
      for (const row of state.providers) {
        const before = prevBreaker.get(row.name);
        if (before !== undefined && before !== row.state) {
          const level: TelemetryRailLevel =
            row.state === "OPEN" || row.state === "HALF_OPEN" ? "warn" : "info";
          push(level, "providers", `${row.name}: breaker ${before} → ${row.state}`);
        }
        prevBreaker.set(row.name, row.state);
      }
      const names = new Set(state.providers.map((r) => r.name));
      for (const key of prevBreaker.keys()) {
        if (!names.has(key)) {
          prevBreaker.delete(key);
        }
      }
    });
  }, [push]);

  useEffect(() => {
    let primed = false;
    let prevRegime: string | null = null;
    let prevOverall: string | null = null;

    return useSystemStore.subscribe((state) => {
      const health = state.health;
      if (health === null) {
        return;
      }
      if (!primed) {
        primed = true;
        prevRegime = health.currentRegime;
        prevOverall = health.overallStatus;
        return;
      }
      if (prevRegime !== null && prevRegime !== health.currentRegime) {
        const conf_display =
          typeof health.regimeConfidence === "number" && Number.isFinite(health.regimeConfidence)
            ? health.regimeConfidence.toFixed(2)
            : "—";
        push(
          "info",
          "regime",
          `Regime ${prevRegime} → ${health.currentRegime} · conf ${conf_display}`,
        );
        prevRegime = health.currentRegime;
      }
      if (prevOverall !== null && prevOverall !== health.overallStatus) {
        const level: TelemetryRailLevel =
          health.overallStatus === "HALTED" || health.overallStatus === "DEGRADED" ? "error" : "warn";
        push(level, "system", `Platform ${prevOverall} → ${health.overallStatus}`);
        prevOverall = health.overallStatus;
      }
    });
  }, [push]);

  const lastLoggedScoreDataTickRef = useRef(0);
  const lastScoreLogWallMsRef = useRef(0);

  useEffect(() => {
    const id = window.setInterval(() => {
      const { lastUpdated, isConnected, scoresByAsset } = useScoresStore.getState();
      if (!isConnected || lastUpdated === null) {
        return;
      }
      const dataTickMs = lastUpdated.getTime();
      if (dataTickMs <= lastLoggedScoreDataTickRef.current) {
        return;
      }
      const now = Date.now();
      if (now - lastScoreLogWallMsRef.current < 18_000 && lastScoreLogWallMsRef.current !== 0) {
        lastLoggedScoreDataTickRef.current = dataTickMs;
        return;
      }
      lastLoggedScoreDataTickRef.current = dataTickMs;
      lastScoreLogWallMsRef.current = now;
      push("info", "scores", `Confluence snapshots updated · ${scoresByAsset.size} row(s)`);
    }, 6_000);
    return () => window.clearInterval(id);
  }, [push]);

  useEffect(() => {
    if (typeof window === "undefined" || !window.sessionStorage) {
      return;
    }
    if (window.sessionStorage.getItem(RAIL_STORAGE_KEY) === "1") {
      return;
    }
    window.sessionStorage.setItem(RAIL_STORAGE_KEY, "1");
    push("info", "telemetry", "Live alerts stream armed · WebSocket + monitoring hooks");
  }, [push]);

  const alertsErrorNotifiedRef = useRef(false);

  useEffect(() => {
    if (!alertsQuery.isError) {
      alertsErrorNotifiedRef.current = false;
      return;
    }
    if (alertsErrorNotifiedRef.current) {
      return;
    }
    alertsErrorNotifiedRef.current = true;
    push("warn", "api", "Monitoring alerts poll failed — open Monitoring for full detail");
  }, [alertsQuery.isError, push]);
}
