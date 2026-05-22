import type { ReactNode } from "react";

import { cn } from "../../lib/cn";

export interface CardProps {
  title?: string;
  subtitle?: string;
  headerActions?: ReactNode;
  children: ReactNode;
  className?: string;
  accent?: "cyan" | "amber" | "violet" | "teal" | "none";
}

const accentTop: Record<NonNullable<CardProps["accent"]>, string> = {
  none: "border-t-2 border-transparent",
  cyan: "border-t-2 border-t-[var(--accent-cyan)]",
  amber: "border-t-2 border-t-[var(--accent-amber)]",
  violet: "border-t-2 border-t-[var(--accent-violet)]",
  teal: "border-t-2 border-t-[var(--accent-teal)]",
};

export function Card({
  title,
  subtitle,
  headerActions,
  children,
  className,
  accent = "none",
}: CardProps) {
  return (
    <section
      className={cn(
        "rounded-[var(--radius-md)] border border-[var(--border)] bg-[var(--bg-surface)]",
        accentTop[accent],
        className,
      )}
    >
      {(title !== undefined || subtitle !== undefined || headerActions !== undefined) && (
        <header className="flex items-start justify-between gap-3 border-b border-[var(--border)] px-4 py-3">
          <div className="min-w-0">
            {title !== undefined && (
              <h2 className="text-sm font-semibold tracking-tight text-slate-100">{title}</h2>
            )}
            {subtitle !== undefined && (
              <p className="mt-0.5 text-xs text-[var(--text-secondary)]">{subtitle}</p>
            )}
          </div>
          {headerActions !== undefined && (
            <div className="flex shrink-0 items-center gap-2">{headerActions}</div>
          )}
        </header>
      )}
      <div className="p-4">{children}</div>
    </section>
  );
}
