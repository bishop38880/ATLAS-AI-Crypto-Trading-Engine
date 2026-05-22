import type { ReactElement } from "react";

export interface SvgSparklinePoint {
  x: number;
  y: number;
}

interface SvgSparklineProps {
  title: string;
  points: SvgSparklinePoint[];
  ariaLabel?: string;
  accentClassName?: string;
}

/**
 * Lightweight SVG stroke chart — avoids adding a heavyweight chart dependency.
 */
export function SvgSparkline({
  title,
  points,
  ariaLabel,
  accentClassName = "stroke-[var(--accent-cyan)]",
}: SvgSparklineProps): ReactElement | null {
  if (points.length < 2) {
    return null;
  }

  const width = 100;
  const height = 42;
  const pad = 1.8;
  const xs = points.map((p) => p.x);
  const ys = points.map((p) => p.y);
  const xmin = Math.min(...xs);
  const xmax = Math.max(...xs);
  const ymin = Math.min(...ys);
  const ymax = Math.max(...ys);
  const xspan = xmax - xmin || 1;
  const yspan = ymax - ymin || 1;

  const mapped = points
    .map((p) => {
      const sx = pad + ((p.x - xmin) / xspan) * (width - pad * 2);
      const sy = height - pad - ((p.y - ymin) / yspan) * (height - pad * 2);
      return `${sx.toFixed(2)},${sy.toFixed(2)}`;
    })
    .join(" ");

  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      preserveAspectRatio="none"
      className="h-28 w-full"
      aria-label={ariaLabel ?? title}
      role="img"
    >
      <title>{title}</title>
      <polyline
        fill="none"
        className={accentClassName}
        strokeWidth={2.25}
        vectorEffect="non-scaling-stroke"
        strokeLinecap="round"
        strokeLinejoin="round"
        points={mapped}
      />
    </svg>
  );
}
