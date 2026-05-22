import { useCallback, useState, type ReactElement } from "react";

import { GlassPanel } from "../ui/GlassPanel";

const LAUNCH_DEV_PATH = "/__atlas_dev__/launch-full-stack";

/**
 * Development only: one button to spawn the host script that brings up Docker, API, frontend
 * terminals, and opens the dashboard. Requires `ATLAS_VITE_LAUNCH_STACK=1` when starting Vite.
 */
export function FullStackLaunchCard(): React.ReactElement | null {
  if (!import.meta.env.DEV) {
    return null;
  }

  const [pending, set_pending] = useState(false);
  const [message, set_message] = useState<string | null>(null);

  const launch_full_stack = useCallback(async () => {
    set_pending(true);
    set_message(null);
    try {
      const response = await fetch(LAUNCH_DEV_PATH, { method: "POST" });
      const raw: unknown = await response.json().catch(() => null);
      const detail =
        typeof raw === "object" && raw !== null && "error" in raw && typeof (raw as { error: unknown }).error === "string"
          ? (raw as { error: string }).error
          : null;
      if (response.ok) {
        set_message("Started. Check the stack terminal and new tabs; the dashboard should open in your browser.");
      } else {
        set_message(detail ?? `Request failed (${String(response.status)}).`);
      }
    } catch {
      set_message("Request failed — is `npm run dev` running?");
    } finally {
      set_pending(false);
    }
  }, []);

  return (
    <GlassPanel className="border border-[var(--border-accent)]/35 bg-[var(--bg-surface)]/80 px-4 py-3">
      <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h2 className="text-sm font-semibold text-[var(--text-primary)]">Local full stack</h2>
          <p className="mt-1 max-w-xl text-xs text-[var(--text-secondary)]">
            Starts Docker (Redis, Postgres, Qdrant), runs migrations, opens backend and frontend terminals, then opens
            this dashboard in your browser (this dev server is reused — no second Vite). For the
            button to work, set{" "}
            <span className="font-mono text-[var(--accent-cyan)]">ATLAS_VITE_LAUNCH_STACK=1</span> when you run{" "}
            <span className="font-mono">npm run dev</span>, or run{" "}
            <span className="font-mono">./scripts/install-atlas-desktop-launcher.sh</span> once for a single «ATLAS» icon on
            your Desktop.
          </p>
        </div>
        <button
          type="button"
          onClick={() => void launch_full_stack()}
          disabled={pending}
          className="shrink-0 rounded-[var(--radius-sm)] border border-[var(--accent-cyan)]/40 bg-[var(--accent-cyan)]/10 px-4 py-2 text-xs font-bold uppercase tracking-wide text-[var(--accent-cyan)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent-cyan)] disabled:cursor-not-allowed disabled:opacity-40 hover:bg-[var(--accent-cyan)]/20"
        >
          {pending ? "Starting…" : "Start everything + open dashboard"}
        </button>
      </div>
      {message !== null ? (
        <p className="mt-2 text-xs text-[var(--text-tertiary)]" role="status">
          {message}
        </p>
      ) : null}
    </GlassPanel>
  );
}
