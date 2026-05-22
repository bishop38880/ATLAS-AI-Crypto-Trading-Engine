import { memo, useMemo } from "react";

import { Skeleton } from "./ui/Skeleton";
import { StatusDot } from "./ui/StatusDot";
import { useAgentStore, type AgentStatus } from "../store/index";

const PING_DEAD_MS = 60_000;

function statusTone(status: AgentStatus["status"], deadPing: boolean): "healthy" | "degraded" | "error" | "offline" {
  if (deadPing || status === "TIMEOUT" || status === "OFFLINE") {
    return "error";
  }
  if (status === "DEGRADED") {
    return "degraded";
  }
  if (status === "HEALTHY") {
    return "healthy";
  }
  return "offline";
}

function AgentRow({ agent, deadPing }: { agent: AgentStatus; deadPing: boolean }) {
  const tone = statusTone(agent.status, deadPing);
  const vetoFlash = agent.veto;

  return (
    <div
      role="group"
      className={`flex items-center gap-3 rounded-[var(--radius-sm)] border border-[var(--border)] bg-[var(--bg-surface)] px-3 py-2.5 ${
        vetoFlash ? "animate-pulse border-[var(--danger)] bg-[rgba(239,83,80,0.08)]" : ""
      }`}
      aria-live="polite"
    >
      <StatusDot level={tone} />
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-baseline gap-2">
          <span className="truncate font-semibold text-[var(--text-primary)]">{agent.name}</span>
          <span className="text-[10px] font-bold uppercase tracking-wide text-[var(--text-tertiary)]">
            {agent.category}
          </span>
          {agent.veto ? (
            <span className="rounded border border-[var(--danger)] px-1.5 py-0.5 text-[10px] font-black uppercase text-[var(--danger)]">
              Veto
            </span>
          ) : null}
        </div>
        <p className="mt-0.5 font-mono text-[11px] tabular-nums text-[var(--text-secondary)]">
          score {agent.lastScore} / {agent.maxPoints} · ping {agent.lastPingMs} ms
          {deadPing ? " · ▼ stale" : ""}
        </p>
      </div>
    </div>
  );
}

function AgentHealthPanelInner() {
  const agents = useAgentStore((state) => state.agents);
  const isConnected = useAgentStore((state) => state.isConnected);

  const deadAlert = useMemo(
    () => agents.some((agent) => agent.lastPingMs > PING_DEAD_MS),
    [agents],
  );

  if (agents.length === 0) {
    return (
      <section className="space-y-2" aria-busy="true" aria-label="Agent health loading">
        <Skeleton className="block w-full" height={48} />
        <Skeleton className="block w-full" height={48} />
        <Skeleton className="block w-full" height={48} />
        <p className="text-xs text-[var(--text-tertiary)]">
          {isConnected ? "Waiting for first agent frame…" : "Connecting to agent channel…"}
        </p>
      </section>
    );
  }

  return (
    <section className="space-y-3" aria-live="polite">
      <div className="flex items-center justify-between gap-2">
        <h2 className="text-sm font-bold text-[var(--text-primary)]">Agent health</h2>
        {deadAlert ? (
          <span className="text-[11px] font-bold uppercase text-[var(--danger)]">▲ Dead agent ping</span>
        ) : null}
      </div>
      <div className="space-y-2">
        {agents.map((agent) => (
          <AgentRow
            agent={agent}
            deadPing={agent.lastPingMs > PING_DEAD_MS}
            key={agent.name}
          />
        ))}
      </div>
    </section>
  );
}

export const AgentHealthPanel = memo(AgentHealthPanelInner);
