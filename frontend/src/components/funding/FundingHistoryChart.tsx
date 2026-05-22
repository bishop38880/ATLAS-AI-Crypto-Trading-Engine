import type { ReactElement } from "react";

import { calculate_sparkline_polyline_points } from "../../lib/calculate_sparkline_polyline";
import { cn } from "../../lib/cn";

export type FundingChartWindowDays = 7 | 30;

export interface FundingHistoryChartPoint {
  ts: string;
  rate: string;
}

function filter_points_by_age(points: FundingHistoryChartPoint[], days: number): FundingHistoryChartPoint[] {
  const cutoff_ms = Date.now() - days * 86_400_000;

  const filtered = points.filter((cell) => {
    const epoch = Date.parse(cell.ts);
    if (!Number.isFinite(epoch)) {
      return false;
    }
    return epoch >= cutoff_ms;
  });

  if (filtered.length === 0) {
    const tail = [...points];
    tail.reverse();
    return tail.slice(0, Math.min(tail.length, Math.max(days * 3, 7))).reverse();
  }

  return filtered;
}

interface FundingHistoryChartProps {
  readonly points_all: FundingHistoryChartPoint[];
  readonly window_days: FundingChartWindowDays;
  readonly className?: string;
}

export function FundingHistoryChart(props: FundingHistoryChartProps): ReactElement {
  const slice = filter_points_by_age(props.points_all, props.window_days);
  const values = slice.map((cell) => {
    const n = Number(cell.rate);
    return Number.isFinite(n) ? n * 100 : 0;
  });

  const polyline =
    values.length > 0
      ? calculate_sparkline_polyline_points(values)
      : ({ pointsAttr: "", min: 0, max: 0 } as ReturnType<
          typeof calculate_sparkline_polyline_points
        >);

  const first_label = slice[0]?.ts?.slice(0, 10) ?? "—";
  const last_label = slice[slice.length - 1]?.ts?.slice(0, 10) ?? "—";

  const stroke =
    slice.length >= 3 && typeof polyline.max === "number" && typeof polyline.min === "number"
      ? polyline.max >= polyline.min
        ? polyline.min >= 0
          ? "rgb(239,83,80)"
          : "rgb(0,230,118)"
        : "rgba(148,163,184,0.95)"
      : "rgba(148,163,184,0.95)";

  return (
    <div className={cn("space-y-1.5", props.className)}>
      <svg
        className="h-28 w-full overflow-visible rounded-md border border-[var(--border)] bg-[rgba(15,23,42,0.35)] px-2 py-2"
        viewBox="0 0 100 32"
        preserveAspectRatio="none"
        role="img"
        aria-label="Funding rate history sparkline (% per interval magnitude)"
      >
        {polyline.pointsAttr.length === 0 ? (
          <text x="50" y="18" fill="rgba(148,163,184,0.95)" fontSize="4" textAnchor="middle">
            No cached history yet
          </text>
        ) : (
          <polyline
            fill="none"
            strokeWidth="1.2"
            stroke={stroke}
            points={polyline.pointsAttr}
          />
        )}
      </svg>

      <div className="flex justify-between px-1 text-[10px] tabular-nums text-[var(--text-secondary)]">
        <span className="min-w-0 truncate">{props.window_days}d slice · left {first_label}</span>
        <span className="min-w-0 truncate">{last_label} · {slice.length} bars</span>
      </div>
    </div>
  );
}
