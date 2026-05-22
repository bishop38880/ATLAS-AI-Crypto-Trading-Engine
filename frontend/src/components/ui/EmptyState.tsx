import type { ReactNode } from "react";

import { cn } from "../../lib/cn";

export interface EmptyStateProps {
  title: string;
  description?: string;
  icon?: ReactNode;
  className?: string;
  children?: ReactNode;
}

export function EmptyState({ title, description, icon, className, children }: EmptyStateProps) {
  return (
    <div
      className={cn(
        "flex flex-col items-center justify-center gap-2 rounded-[var(--radius-md)] border border-dashed border-[var(--border)] bg-[var(--bg-surface)]/60 px-8 py-12 text-center",
        className,
      )}
    >
      {icon !== undefined && <div className="text-[var(--text-tertiary)]">{icon}</div>}
      <h3 className="font-mono text-sm font-semibold text-[var(--text-primary)]">{title}</h3>
      {description !== undefined && (
        <p className="max-w-sm text-xs leading-relaxed text-[var(--text-secondary)]">
          {description}
        </p>
      )}
      {children}
    </div>
  );
}
