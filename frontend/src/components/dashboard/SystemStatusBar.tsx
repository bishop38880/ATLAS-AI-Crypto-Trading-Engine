import { useCallback, useEffect, useRef, useState } from "react";

import { derive_base_asset_from_pair } from "../../lib/dashboard-symbol";
import type { DashboardPositionSlot } from "../../lib/dashboard-positions";
import { format_portfolio_pnl_bias_display } from "../../lib/format-portfolio-pnl";
import { format_countdown_label } from "../../hooks/useNextCycleCountdown";
import { cn } from "../../lib/cn";
import { apiUrl } from "../../lib/url";
import { RegimeStatusBadge } from "./RegimeStatusBadge";
import { Button } from "../ui/Button";
import { StatusPill } from "../ui/StatusPill";
import type { PricePayload } from "../../store/index";
import { usePricesStore, useSystemStore } from "../../store/index";

const MAX_PROMETHEUS_SLOTS = 6;

function derive_btc_quote(prices_lookup: PricePayload[]): PricePayload | null {
  const priority = ["BTC/USDT", "BTCUSDT", "BTC"];
  for (const key of priority) {
    const exact = prices_lookup.find((quote) => quote.symbol.toUpperCase() === key);
    if (exact) {
      return exact;
    }
  }

  const starts = prices_lookup.find((quote) => derive_base_asset_from_pair(quote.symbol) === "BTC");
  return starts ?? null;
}

function summarize_numeric_glyph(value: number | null): "▲" | "▼" | "→" {
  if (value === null || !Number.isFinite(value)) {
    return "→";
  }
  if (value > 0) {
    return "▲";
  }
  if (value < 0) {
    return "▼";
  }
  return "→";
}

function coerce_percent_number(raw?: string): number | null {
  if (raw === undefined) {
    return null;
  }
  const sanitized = Number.parseFloat(raw.replace(/%/g, "").replace(/,/g, ""));
  return Number.isFinite(sanitized) ? sanitized : null;
}

function derive_pnl_glyph_from_display(text: string): "▲" | "▼" | "→" {
  const parsed = Number.parseFloat(text.replace(/[%+,]/g, ""));
  if (!Number.isFinite(parsed) || parsed === 0) {
    return "→";
  }

  return parsed > 0 ? "▲" : "▼";
}

function is_record(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export interface SystemStatusBarProps {
  position_slots?: DashboardPositionSlot[] | undefined;
}

export function SystemStatusBar({ position_slots }: SystemStatusBarProps) {
  const health = useSystemStore((state) => state.health);
  const btc_payload = usePricesStore((state) => derive_btc_quote([...state.prices.values()]));

  const [engine_poll, set_engine_poll] = useState<boolean | null>(null);
  const [start_engine_pending, set_start_engine_pending] = useState(false);
  const [start_engine_message, set_start_engine_message] = useState<string | null>(null);

  const refresh_engine_status = useCallback(async () => {
    try {
      const response = await fetch(apiUrl("/api/system/autonomous-engine"), {
        headers: { Accept: "application/json" },
      });
      if (!response.ok) {
        return;
      }
      const body: unknown = await response.json();
      if (is_record(body) && typeof body.running === "boolean") {
        set_engine_poll(body.running);
      }
    } catch {
      /* API down or blocked — WS may still deliver health. */
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    void refresh_engine_status();
    const timer_id = window.setInterval(() => {
      if (!cancelled) {
        void refresh_engine_status();
      }
    }, 10_000);
    return () => {
      cancelled = true;
      window.clearInterval(timer_id);
    };
  }, [refresh_engine_status]);

  const start_analysis_engine = useCallback(async () => {
    set_start_engine_pending(true);
    set_start_engine_message(null);
    try {
      const response = await fetch(apiUrl("/api/system/start-autonomous-engine"), {
        method: "POST",
        headers: { Accept: "application/json" },
      });
      const body: unknown = await response.json();
      const status =
        is_record(body) && typeof body.status === "string" ? body.status : "unknown";
      set_start_engine_message(status);
      if (status === "started" || status === "already_running") {
        set_engine_poll(true);
      }
      await refresh_engine_status();
    } catch {
      set_start_engine_message("request_failed");
    } finally {
      set_start_engine_pending(false);
    }
  }, [refresh_engine_status]);

  const engine_active =
    typeof health?.autonomousEngineActive === "boolean"
      ? health.autonomousEngineActive
      : engine_poll;

  const engine_dot_level: "healthy" | "offline" | "shadow" =
    engine_active === true ? "healthy" : engine_active === false ? "offline" : "shadow";

  const engine_label =
    engine_active === true ? "Running" : engine_active === false ? "Stopped" : "Checking…";

  const filled_slots =
    position_slots?.filter((slot) => typeof slot.asset === "string" && slot.asset.trim().length > 0).length ??
    Math.min(MAX_PROMETHEUS_SLOTS, Math.max(0, health?.openPositions ?? 0));

  const next_server_seconds = Math.max(0, health?.nextCycleSeconds ?? 0);

  const server_seconds_ref = useRef(0);

  useEffect(() => {
    server_seconds_ref.current = next_server_seconds;
  }, [next_server_seconds]);

  const [display_seconds, set_display_seconds] = useState(next_server_seconds);

  useEffect(() => {
    const timer_id = window.setInterval(() => {
      set_display_seconds((previous_display) => {
        const server_snapshot = Math.max(0, server_seconds_ref.current);

        if (server_snapshot > previous_display) {
          return server_snapshot;
        }

        if (Math.abs(server_snapshot - previous_display) > 2) {
          return server_snapshot;
        }

        return Math.max(0, previous_display - 1);
      });
    }, 1000);

    return () => window.clearInterval(timer_id);
  }, []);

  const [countdown_flash, set_countdown_flash] = useState(false);
  const flash_prior_ref = useRef(next_server_seconds);
  useEffect(() => {
    const prior_value = flash_prior_ref.current;

    if (next_server_seconds > prior_value && prior_value !== 0) {
      set_countdown_flash(true);
      const timeout_id = window.setTimeout(() => set_countdown_flash(false), 450);
      flash_prior_ref.current = next_server_seconds;
      return () => window.clearTimeout(timeout_id);
    }

    flash_prior_ref.current = next_server_seconds;

    return undefined;
  }, [next_server_seconds]);

  const status_dot_level =
    health?.overallStatus === "HALTED"
      ? "error"
      : health?.overallStatus === "DEGRADED"
        ? "degraded"
        : "healthy";

  const btc_change_token = btc_payload?.change24h;
  const display_btc_price = health?.btcPrice ?? btc_payload?.price ?? "—";

  const pnl_visual = format_portfolio_pnl_bias_display(health?.portfolioPnl24h ?? "0");
  const pnl_glyph = derive_pnl_glyph_from_display(pnl_visual.text);

  const countdown = format_countdown_label(display_seconds);

  const status_word =
    health?.overallStatus === "HALTED" ? "HALTED" : health?.overallStatus === "DEGRADED" ? "DEGRADED" : "HEALTHY";

  const cycle_label =
    typeof health?.cycleCount === "number" && Number.isFinite(health.cycleCount)
      ? `${health.cycleCount.toLocaleString()}`
      : "—";

  const btc_quote_change = coerce_percent_number(health?.btcChange24h ?? btc_change_token);

  const btc_glyph = summarize_numeric_glyph(btc_quote_change);

  return (
    <>
      {health?.overallStatus === "HALTED" ? (
        <div role="alert" aria-live="assertive" className="sr-only">
          System halted. Operator attention required.
        </div>
      ) : null}
      <div className="command-strip mb-4 w-full">
        <div aria-live="polite" aria-atomic="false" className="flex flex-wrap items-center gap-3 md:gap-4">
          <StatusPill
            level={
              status_dot_level === "healthy"
                ? "healthy"
                : status_dot_level === "degraded"
                  ? "degraded"
                  : "error"
            }
            label={status_word}
          />

          <RegimeStatusBadge health={health ?? null} />

          <span className="font-data text-[12px] text-[var(--text-secondary)]">{`Cycle ${cycle_label}`}</span>

          <span
            className={cn(
              "font-data text-[13px] font-semibold text-[var(--accent-cyan)] tabular-nums",
              countdown_flash && "cycle-timer-flash",
            )}
          >
            {`Next: ${countdown}`}
          </span>

          <span
            className={cn(
              "font-data text-[12px] tabular-nums",
              filled_slots >= MAX_PROMETHEUS_SLOTS ? "font-semibold text-[var(--danger)]" : "text-[var(--text-secondary)]",
            )}
          >
            {`${filled_slots}/${MAX_PROMETHEUS_SLOTS} slots`}
          </span>

          <span className="metric-glow flex items-center gap-1 font-data text-[12px] font-semibold tabular-nums">
            <span
              className={pnl_visual.is_positive_bias ? "text-[var(--success)]" : "text-[var(--danger)]"}
              aria-hidden
            >
              {pnl_glyph}
            </span>
            <span className={pnl_visual.is_positive_bias ? "text-[var(--success)]" : "text-[var(--danger)]"}>
              {`PnL ${pnl_visual.text}`}
            </span>
          </span>

          <div className="flex flex-wrap items-center gap-2 border-l border-slate-800 pl-3 md:pl-4">
            <span className="section-label">Analysis engine</span>
            <StatusPill level={engine_dot_level} label={engine_label} />
            <Button
              variant="pill-primary"
              size="xs"
              disabled={start_engine_pending || engine_active === true}
              aria-label="Start autonomous analysis engine"
              onClick={() => void start_analysis_engine()}
            >
              {start_engine_pending ? "Starting…" : "Start"}
            </Button>
            {start_engine_message !== null ? (
              <span className="max-w-[14rem] truncate font-data text-[10px] text-slate-500">
                {start_engine_message}
              </span>
            ) : null}
          </div>

          <span className="ml-auto hidden items-center gap-1 font-data text-[12px] xl:flex">
            <span className="text-[var(--text-secondary)]">BTC:</span>
            <span className="text-[var(--text-primary)] tabular-nums">{`$${display_btc_price}`}</span>
            <span
              className={cn(
                "tabular-nums",
                btc_quote_change !== null && btc_quote_change > 0
                  ? "text-[var(--success)]"
                  : btc_quote_change !== null && btc_quote_change < 0
                    ? "text-[var(--danger)]"
                    : "text-[var(--text-secondary)]",
              )}
            >
              <span aria-hidden>{btc_glyph}</span>
              {` ${health?.btcChange24h ?? btc_change_token ?? ""}`}
            </span>
          </span>
        </div>
      </div>
    </>
  );
}
