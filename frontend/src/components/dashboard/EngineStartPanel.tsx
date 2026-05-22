import { useCallback, useState } from "react";

import { cn } from "../../lib/cn";
import { apiUrl } from "../../lib/url";
import { useSystemStore } from "../../store/index";
import { Badge } from "../ui/Badge";
import { GlassPanel } from "../ui/GlassPanel";
import { StatusDot } from "../ui/StatusDot";

type EngineStartStatus = "idle" | "started" | "already_running" | "partial" | "error" | "request_failed";

interface StartAllResult {
  hydraStatus: string | null;
  engineStatus: string | null;
}

function is_record(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

async function post_json(path: string): Promise<{ ok: boolean; status: number; body: unknown }> {
  const response = await fetch(apiUrl(path), {
    method: "POST",
    headers: { Accept: "application/json" },
  });
  const body: unknown = await response.json().catch(() => null);
  return { ok: response.ok, status: response.status, body };
}

async function start_everything_request(): Promise<{
  ok: boolean;
  status: EngineStartStatus;
  hydraStatus: string | null;
  engineStatus: string | null;
}> {
  const combined_response = await post_json("/api/system/start-all");
  if (combined_response.status !== 404) {
    const body = combined_response.body;
    const response_status = is_record(body) && typeof body.status === "string" ? body.status : "error";
    const hydra_status = is_record(body) && typeof body.hydra_status === "string" ? body.hydra_status : null;
    const engine_status = is_record(body) && typeof body.engine_status === "string" ? body.engine_status : null;
    const status: EngineStartStatus =
      combined_response.ok &&
      (response_status === "started" || response_status === "already_running" || response_status === "partial")
        ? response_status
        : "error";
    return {
      ok: combined_response.ok,
      status,
      hydraStatus: hydra_status,
      engineStatus: engine_status,
    };
  }

  const hydra_response = await post_json("/api/system/arm-hydra");
  const engine_response = await post_json("/api/system/start-autonomous-engine");
  const hydra_body = hydra_response.body;
  const engine_body = engine_response.body;
  const hydra_status = is_record(hydra_body) && typeof hydra_body.status === "string" ? hydra_body.status : "error";
  const engine_status =
    is_record(engine_body) && typeof engine_body.status === "string" ? engine_body.status : "error";
  const is_engine_ok = engine_status === "started" || engine_status === "already_running";
  const is_hydra_ok = hydra_status === "success";
  return {
    ok: is_hydra_ok && is_engine_ok,
    status: is_hydra_ok && is_engine_ok ? "started" : is_engine_ok ? "partial" : "error",
    hydraStatus: hydra_status,
    engineStatus: engine_status,
  };
}

function read_status_message(status: EngineStartStatus): string {
  switch (status) {
    case "started":
      return "Everything started. HYDRA is armed and confluence/RAG analysis will populate as snapshots arrive.";
    case "already_running":
      return "Engine is already running. Waiting for the next analysis snapshots.";
    case "partial":
      return "Analysis started, but one startup step reported a warning. Check component statuses below.";
    case "error":
      return "Backend could not start everything. Check API logs for the start_all failure.";
    case "request_failed":
      return "Request failed. Confirm the ATLAS API is reachable from this frontend.";
    default:
      return "Start HYDRA ingestion and the background analysis loop that publishes confluence scores and RAG context.";
  }
}

export function EngineStartPanel(): React.ReactElement {
  const health_engine_active = useSystemStore((state) => state.health?.autonomousEngineActive);
  const [engine_running, set_engine_running] = useState<boolean | null>(null);
  const [pending, set_pending] = useState(false);
  const [status, set_status] = useState<EngineStartStatus>("idle");
  const [result, set_result] = useState<StartAllResult>({
    hydraStatus: null,
    engineStatus: null,
  });

  const start_everything = useCallback(async () => {
    set_pending(true);
    set_status("idle");
    set_result({ hydraStatus: null, engineStatus: null });
    try {
      const start_result = await start_everything_request();
      set_status(start_result.status);
      set_result({
        hydraStatus: start_result.hydraStatus,
        engineStatus: start_result.engineStatus,
      });
      if (start_result.engineStatus === "started" || start_result.engineStatus === "already_running") {
        set_engine_running(true);
      }
    } catch {
      set_status("request_failed");
      set_result({ hydraStatus: null, engineStatus: null });
    } finally {
      set_pending(false);
    }
  }, []);

  const active = typeof health_engine_active === "boolean" ? health_engine_active : engine_running;
  const status_level = active === true ? "healthy" : active === false ? "offline" : "shadow";
  const status_label = active === true ? "Running" : active === false ? "Stopped" : "Checking";
  const button_disabled = pending || active === true;

  return (
    <GlassPanel glow className="border border-[var(--accent-cyan)]/25 bg-[var(--bg-surface)]/85 p-4">
      <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <Badge variant={active === true ? "live" : "shadow"}>Engine</Badge>
            <StatusDot level={status_level} />
            <span className="font-data text-xs uppercase tracking-[0.18em] text-[var(--text-secondary)]">
              {status_label}
            </span>
          </div>
          <h2 className="mt-3 text-lg font-semibold text-[var(--text-primary)]">Start ATLAS</h2>
          <p className="mt-2 max-w-3xl text-sm leading-6 text-[var(--text-secondary)]">
            One click arms HYDRA, starts the autonomous RAG runner, scores the active asset universe, and publishes live
            confluence snapshots for the dashboard.
          </p>
          <p className="mt-2 text-xs text-[var(--text-tertiary)]" role="status" aria-live="polite">
            {read_status_message(status)}
          </p>
          {result.hydraStatus !== null || result.engineStatus !== null ? (
            <div className="mt-3 flex flex-wrap gap-2 text-[11px] text-[var(--text-tertiary)]">
              <span className="rounded-full border border-white/10 px-2 py-1 font-mono">
                HYDRA: {result.hydraStatus ?? "unknown"}
              </span>
              <span className="rounded-full border border-white/10 px-2 py-1 font-mono">
                Engine: {result.engineStatus ?? "unknown"}
              </span>
            </div>
          ) : null}
        </div>
        <button
          type="button"
          onClick={() => void start_everything()}
          disabled={button_disabled}
          className={cn(
            "shrink-0 rounded-[var(--radius-md)] border border-[var(--accent-cyan)]/50 bg-[var(--accent-cyan)]/12 px-5 py-3 text-sm font-bold uppercase tracking-wide text-[var(--accent-cyan)]",
            "shadow-[0_0_24px_rgba(0,229,255,0.12)] transition-[background,border-color,transform]",
            "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent-cyan)]",
            "disabled:cursor-not-allowed disabled:opacity-45",
            "enabled:hover:-translate-y-0.5 enabled:hover:border-[var(--accent-cyan)] enabled:hover:bg-[var(--accent-cyan)]/20",
          )}
        >
          {pending ? "Starting everything…" : active === true ? "ATLAS running" : "Start everything"}
        </button>
      </div>
    </GlassPanel>
  );
}
