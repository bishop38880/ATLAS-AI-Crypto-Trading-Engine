import { useEffect, useMemo, useState } from "react";

import {
  calculate_confluence_needle_left_percent,
  calculate_confluence_threshold_zone,
} from "../../lib/calculate-confluence-threshold-zone";
import {
  CONFLUENCE_LADDER_THRESHOLD_T1,
  CONFLUENCE_LADDER_THRESHOLD_T2,
  CONFLUENCE_LADDER_THRESHOLD_T3,
  CONFLUENCE_SCORE_CAP,
} from "../../lib/confluence-score-constants";
import { cn } from "../../lib/cn";

export interface ConfluenceThresholdScoreBarProps {
  value: number;
  label: string;
  showValue?: boolean;
  animated?: boolean;
  /** When false, omits the proportional legend row under the track (narrow layouts). */
  showZoneLegend?: boolean;
  size?: "sm" | "md" | "lg";
}

const sizeHeights: Record<NonNullable<ConfluenceThresholdScoreBarProps["size"]>, string> = {
  sm: "h-1.5",
  md: "h-2.5",
  lg: "h-3.5",
};

/** Zone fill colors — distinct on dark dashboard cards. */
const ZONE_GREY = "rgba(92, 101, 120, 0.92)";
const ZONE_AMBER = "rgba(217, 119, 6, 0.95)";
const ZONE_ORANGE = "rgba(234, 88, 12, 0.95)";
const ZONE_GREEN = "rgba(0, 230, 118, 0.92)";

export function ConfluenceThresholdScoreBar({
  value,
  label,
  showValue = true,
  animated = false,
  showZoneLegend = true,
  size = "md",
}: ConfluenceThresholdScoreBarProps) {
  const cap = CONFLUENCE_SCORE_CAP;
  const clamped = Math.min(cap, Math.max(0, value));
  const rounded = Math.round(clamped);

  const zone_meta = useMemo(() => calculate_confluence_threshold_zone(clamped, cap), [clamped, cap]);

  const target_needle_pct = useMemo(
    () => calculate_confluence_needle_left_percent(clamped, cap),
    [clamped, cap],
  );

  const [animated_needle_pct, setAnimatedNeedlePct] = useState(
    animated ? 0 : target_needle_pct,
  );

  useEffect(() => {
    if (!animated) {
      return;
    }
    const frame = requestAnimationFrame(() => {
      setAnimatedNeedlePct(target_needle_pct);
    });
    return () => cancelAnimationFrame(frame);
  }, [animated, target_needle_pct]);

  const needle_pct = animated ? animated_needle_pct : target_needle_pct;
  const needle_layout_pct = Math.min(99.4, Math.max(0.6, needle_pct));

  const w_grey = (CONFLUENCE_LADDER_THRESHOLD_T1 / cap) * 100;
  const w_amber = ((CONFLUENCE_LADDER_THRESHOLD_T2 - CONFLUENCE_LADDER_THRESHOLD_T1) / cap) * 100;
  const w_orange = ((CONFLUENCE_LADDER_THRESHOLD_T3 - CONFLUENCE_LADDER_THRESHOLD_T2) / cap) * 100;
  const w_green = ((cap - CONFLUENCE_LADDER_THRESHOLD_T3) / cap) * 100;

  const meter_label = `${label}: ${rounded} of ${cap}, ${zone_meta.label}`;

  return (
    <div className="flex flex-col gap-1">
      <div className="flex items-center justify-between gap-2">
        <span className="truncate text-xs text-[var(--text-secondary)]">{label}</span>
        {showValue ? (
          <span className="shrink-0 font-data text-[11px] tabular-nums text-slate-200">
            {rounded} / {cap}
          </span>
        ) : null}
      </div>

      <div
        className="relative w-full"
        role="meter"
        aria-valuemin={0}
        aria-valuemax={cap}
        aria-valuenow={rounded}
        aria-label={meter_label}
        title={`${rounded} pts — ${zone_meta.label}`}
      >
        <div
          className={cn(
            "relative w-full overflow-hidden rounded-full border border-slate-700/80 ring-1 ring-inset ring-white/[0.04] shadow-[inset_0_1px_2px_rgba(0,0,0,0.35)]",
            sizeHeights[size],
          )}
        >
          <div className="flex h-full w-full">
            <div className="h-full shrink-0 border-r border-[var(--bg-base)]/80" style={{ width: `${w_grey}%`, backgroundColor: ZONE_GREY }} />
            <div className="h-full shrink-0 border-r border-[var(--bg-base)]/80" style={{ width: `${w_amber}%`, backgroundColor: ZONE_AMBER }} />
            <div className="h-full shrink-0 border-r border-[var(--bg-base)]/80" style={{ width: `${w_orange}%`, backgroundColor: ZONE_ORANGE }} />
            <div className="h-full shrink-0" style={{ width: `${w_green}%`, backgroundColor: ZONE_GREEN }} />
          </div>

          <div
            className={cn(
              "pointer-events-none absolute top-0 bottom-0 z-[1] w-[3px] -translate-x-1/2 rounded-[1px]",
              "bg-slate-100 shadow-[0_0_8px_rgba(255,255,255,0.45),0_0_4px_rgba(0,0,0,0.8)]",
              animated && "transition-[left] duration-500 ease-out",
            )}
            style={{ left: `${needle_layout_pct}%` }}
          />
        </div>

        <div className="relative mt-0.5 h-3.5 w-full text-[9px] tabular-nums leading-none text-[var(--text-tertiary)]">
          <span className="absolute left-0 top-0">0</span>
          <span
            className="absolute top-0 -translate-x-1/2"
            style={{ left: `${(CONFLUENCE_LADDER_THRESHOLD_T1 / cap) * 100}%` }}
          >
            {CONFLUENCE_LADDER_THRESHOLD_T1}
          </span>
          <span
            className="absolute top-0 -translate-x-1/2"
            style={{ left: `${(CONFLUENCE_LADDER_THRESHOLD_T2 / cap) * 100}%` }}
          >
            {CONFLUENCE_LADDER_THRESHOLD_T2}
          </span>
          <span
            className="absolute top-0 -translate-x-1/2"
            style={{ left: `${(CONFLUENCE_LADDER_THRESHOLD_T3 / cap) * 100}%` }}
          >
            {CONFLUENCE_LADDER_THRESHOLD_T3}
          </span>
          <span className="absolute right-0 top-0">{cap}</span>
        </div>
      </div>

      {showZoneLegend ? (
        <div className="flex w-full gap-px text-[8px] font-semibold uppercase leading-tight tracking-wide text-[var(--text-tertiary)]">
          <div className="min-w-0 truncate pt-0.5 text-center" style={{ width: `${w_grey}%` }}>
            No trade
          </div>
          <div className="min-w-0 truncate pt-0.5 text-center" style={{ width: `${w_amber}%` }}>
            2% @ 2×
          </div>
          <div className="min-w-0 truncate pt-0.5 text-center" style={{ width: `${w_orange}%` }}>
            3% @ 3×
          </div>
          <div className="min-w-0 truncate pt-0.5 text-center" style={{ width: `${w_green}%` }}>
            5% @ 5×
          </div>
        </div>
      ) : null}
    </div>
  );
}
