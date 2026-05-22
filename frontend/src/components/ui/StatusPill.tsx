import { cn } from "../../lib/cn";

import { StatusDot, type StatusLevel } from "./StatusDot";

export interface StatusPillProps {
  level: StatusLevel;
  label: string;
  className?: string;
}

/** Hardware-style status tag with LED dot. */
export function StatusPill({ level, label, className }: StatusPillProps) {
  const tone =
    level === "healthy"
      ? "status-pill-healthy"
      : level === "degraded"
        ? "status-pill-degraded"
        : level === "shadow"
          ? "status-pill-shadow"
          : level === "offline"
            ? "status-pill-offline"
            : "status-pill-error";

  return (
    <span className={cn("status-pill", tone, className)}>
      <StatusDot level={level} />
      <span className="font-data text-[10px] font-semibold uppercase tracking-wide">{label}</span>
    </span>
  );
}
