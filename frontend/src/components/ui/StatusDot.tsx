import { cn } from "../../lib/cn";

export type StatusLevel = "healthy" | "degraded" | "error" | "offline" | "shadow";

export interface StatusDotProps {
  level: StatusLevel;
  label?: string;
  className?: string;
}

const levelStyles: Record<
  StatusLevel,
  { bg: string; pulse?: "health" | "shadow" }
> = {
  healthy: { bg: "bg-[var(--success)]", pulse: "health" },
  degraded: { bg: "bg-[var(--warning)]" },
  error: { bg: "bg-[var(--danger)]" },
  offline: { bg: "bg-[var(--no-position)]" },
  shadow: { bg: "bg-[var(--accent-violet)]", pulse: "shadow" },
};

export function StatusDot({ level, label, className }: StatusDotProps) {
  const style = levelStyles[level];

  return (
    <span className={cn("inline-flex items-center gap-2", className)}>
      <span
        aria-hidden
        className={cn(
          "inline-block size-2 rounded-full ring-2 ring-white/10",
          style.bg,
          style.pulse === "health" && "health-dot-pulse",
          style.pulse === "shadow" && "shadow-dot-pulse",
        )}
      />
      {label !== undefined && (
        <span className="text-xs font-medium text-[var(--text-secondary)]">{label}</span>
      )}
    </span>
  );
}
