import { useEffect, useMemo, useState } from "react";

import { cn } from "../../lib/cn";

export interface ScoreBarProps {
  value: number;
  max: number;
  label: string;
  showValue?: boolean;
  animated?: boolean;
  size?: "sm" | "md" | "lg";
}

function calculateBarColor(ratio: number): string {
  const t = Math.min(1, Math.max(0, ratio));
  const rLow = { r: 239, g: 83, b: 80 };
  const rMid = { r: 255, g: 167, b: 38 };
  const rHigh = { r: 0, g: 230, b: 118 };

  const lerp = (
    a: { r: number; g: number; b: number },
    b: { r: number; g: number; b: number },
    x: number,
  ) => ({
    r: Math.round(a.r + (b.r - a.r) * x),
    g: Math.round(a.g + (b.g - a.g) * x),
    b: Math.round(a.b + (b.b - a.b) * x),
  });

  let rgb: { r: number; g: number; b: number };
  if (t < 1 / 3) {
    rgb = lerp(rLow, rMid, t * 3);
  } else if (t < 2 / 3) {
    rgb = lerp(rMid, rHigh, (t - 1 / 3) * 3);
  } else {
    rgb = rHigh;
  }

  return `rgb(${rgb.r} ${rgb.g} ${rgb.b})`;
}

const sizeHeights: Record<NonNullable<ScoreBarProps["size"]>, string> = {
  sm: "h-1.5",
  md: "h-2.5",
  lg: "h-3.5",
};

export function ScoreBar({
  value,
  max,
  label,
  showValue = true,
  animated = false,
  size = "md",
}: ScoreBarProps) {
  const safeMax = max > 0 ? max : 1;
  const ratio = value / safeMax;
  const fillColor = useMemo(() => calculateBarColor(ratio), [ratio]);

  const targetWidthPct = ratio * 100;
  const [animatedWidthPct, setAnimatedWidthPct] = useState(animated ? 0 : targetWidthPct);

  useEffect(() => {
    if (!animated) {
      return;
    }

    const frameId = requestAnimationFrame(() => {
      setAnimatedWidthPct(targetWidthPct);
    });
    return () => cancelAnimationFrame(frameId);
  }, [animated, targetWidthPct]);

  const displayPct = animated ? animatedWidthPct : targetWidthPct;

  return (
    <div className="flex flex-col gap-1">
      <div className="flex items-center justify-between gap-2">
        <span className="truncate text-xs text-[var(--text-secondary)]">{label}</span>
        {showValue && (
          <span className="shrink-0 font-mono text-[11px] tabular-nums text-[var(--text-primary)]">
            {Math.round(value)} / {Math.round(safeMax)}
          </span>
        )}
      </div>
      <div
        className={cn(
          "w-full overflow-hidden rounded-full bg-[var(--bg-elevated)]",
          sizeHeights[size],
        )}
      >
        <div
          className={cn("h-full rounded-full transition-[width] duration-500 ease-out")}
          style={{
            width: `${displayPct}%`,
            backgroundColor: fillColor,
          }}
        />
      </div>
    </div>
  );
}
