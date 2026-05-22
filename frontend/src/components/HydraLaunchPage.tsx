import type { ReactElement } from "react";

import { GlassPanel } from "./ui/GlassPanel";

const DEFAULT_HYDRA_DASHBOARD_URL = "http://127.0.0.1:3001";

function getHydraDashboardUrl(): string {
  const configuredUrl = import.meta.env.VITE_HYDRA_DASHBOARD_URL;
  if (typeof configuredUrl === "string" && configuredUrl.trim().length > 0) {
    return configuredUrl.trim();
  }
  return DEFAULT_HYDRA_DASHBOARD_URL;
}

export function HydraLaunchPage(): ReactElement {
  const hydraUrl = getHydraDashboardUrl();

  return (
    <div className="mx-auto flex min-h-full max-w-4xl flex-col justify-center gap-6">
      <GlassPanel glow className="p-8">
        <div className="mb-3 text-xs font-semibold uppercase tracking-[0.3em] text-[var(--accent-cyan)]">
          External System
        </div>
        <h1 className="display text-3xl font-semibold text-[var(--text-primary)]">
          HYDRA Liquidation Cascade Dashboard
        </h1>
        <p className="mt-4 max-w-2xl text-sm leading-6 text-[var(--text-secondary)]">
          HYDRA remains a standalone read-only liquidation alerting service. This
          entry point opens the HYDRA dashboard without importing its code,
          sharing schemas, or subscribing ATLAS to HYDRA internals.
        </p>
        <div className="mt-6 flex flex-wrap items-center gap-3">
          <a
            href={hydraUrl}
            target="_blank"
            rel="noreferrer"
            className="rounded-[var(--radius-sm)] border border-[var(--accent-cyan)] bg-cyan-400/10 px-4 py-2 text-sm font-semibold text-[var(--accent-cyan)] transition hover:bg-cyan-400/20"
          >
            Open HYDRA Dashboard
          </a>
          <span className="font-mono text-xs text-[var(--text-muted)]">
            {hydraUrl}
          </span>
        </div>
      </GlassPanel>

      <GlassPanel className="p-5">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-[var(--text-primary)]">
          Boundary Contract
        </h2>
        <ul className="mt-3 space-y-2 text-sm text-[var(--text-secondary)]">
          <li>HYDRA owns public exchange liquidation streams and cascade detection.</li>
          <li>ATLAS owns intelligence, scoring, RAG, agents, and signal publication.</li>
          <li>This page is navigation-only; it does not bridge runtime data paths.</li>
        </ul>
      </GlassPanel>
    </div>
  );
}
