import type { ReactNode } from "react";

import { cn } from "../../lib/cn";

export interface CommandCardProps {
  title: string;
  subtitle?: string;
  headerActions?: ReactNode;
  children: ReactNode;
  className?: string;
  accent?: "cyan" | "amber" | "violet" | "teal" | "none";
  id?: string;
}

const accentTop: Record<NonNullable<CommandCardProps["accent"]>, string> = {
  none: "",
  cyan: "command-card-accent-cyan",
  amber: "command-card-accent-amber",
  violet: "command-card-accent-violet",
  teal: "command-card-accent-teal",
};

/** Dense institutional panel — layered slate surface with micro-border. */
export function CommandCard({
  title,
  subtitle,
  headerActions,
  children,
  className,
  accent = "none",
  id,
}: CommandCardProps) {
  return (
    <section
      id={id}
      className={cn("command-card transition-[box-shadow,border-color] duration-[var(--transition-base)]", accentTop[accent], className)}
    >
      <header className="command-card-header">
        <div className="min-w-0">
          <h2 className="command-card-title">{title}</h2>
          {subtitle !== undefined ? <p className="command-card-subtitle">{subtitle}</p> : null}
        </div>
        {headerActions !== undefined ? (
          <div className="flex shrink-0 items-center gap-2">{headerActions}</div>
        ) : null}
      </header>
      <div className="command-card-body">{children}</div>
    </section>
  );
}
