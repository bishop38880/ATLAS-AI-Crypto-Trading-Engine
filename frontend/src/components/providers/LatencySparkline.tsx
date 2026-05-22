import { memo, useMemo } from "react";

import { cn } from "../../lib/cn";

export interface LatencySparklineProps {
  /** Recent p99 samples, oldest → newest (max ~20). */
  points: number[];
  className?: string;
}

function calculate_spark_path(values: number[], width: number, height: number): string | null {
  if (values.length === 0) {
    return null;
  }
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const step = values.length <= 1 ? width : width / (values.length - 1);

  const parts: string[] = [];
  for (let i = 0; i < values.length; i += 1) {
    const x = i * step;
    const y = height - ((values[i] - min) / span) * height;
    parts.push(`${i === 0 ? "M" : "L"} ${x.toFixed(2)} ${y.toFixed(2)}`);
  }
  return parts.join(" ");
}

export const LatencySparkline = memo(function LatencySparkline({
  points,
  className,
}: LatencySparklineProps) {
  const width = 96;
  const height = 28;
  const d = useMemo(() => calculate_spark_path(points, width, height), [points]);

  return (
    <svg
      className={cn("shrink-0 overflow-visible", className)}
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      role="img"
      aria-label="p99 latency trend"
    >
      <rect
        x={0}
        y={0}
        width={width}
        height={height}
        rx={4}
        className="fill-[var(--bg-elevated)]"
      />
      {d !== null && (
        <path
          d={d}
          fill="none"
          stroke="var(--accent-cyan)"
          strokeWidth={1.25}
          vectorEffect="non-scaling-stroke"
        />
      )}
    </svg>
  );
});
