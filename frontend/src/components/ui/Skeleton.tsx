import { cn } from "../../lib/cn";

export interface SkeletonProps {
  width?: string | number;
  height?: string | number;
  rounded?: boolean;
  className?: string;
}

function sizeValue(value: string | number | undefined, fallback: string): string {
  if (value === undefined) {
    return fallback;
  }
  return typeof value === "number" ? `${value}px` : value;
}

export function Skeleton({ width, height, rounded = false, className }: SkeletonProps) {
  return (
    <span
      aria-hidden
      className={cn(
        "inline-block skeleton-shimmer-bg",
        rounded ? "rounded-full" : "rounded-[var(--radius-sm)]",
        className,
      )}
      style={{
        width: sizeValue(width, "100%"),
        height: sizeValue(height, "12px"),
        minWidth: "1rem",
      }}
    />
  );
}
