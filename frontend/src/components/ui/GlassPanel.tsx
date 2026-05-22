import type { ReactNode } from "react";

import { cn } from "../../lib/cn";

export interface GlassPanelProps {
  children: ReactNode;
  className?: string;
  glow?: boolean;
}

export function GlassPanel({ children, className, glow }: GlassPanelProps) {
  return (
    <div
      className={cn(
        "rounded-2xl border border-slate-800/90 bg-slate-950/55 shadow-[0_8px_32px_rgba(0,0,0,0.45),inset_0_1px_0_rgba(255,255,255,0.04)] backdrop-blur-xl",
        glow && "border-t-cyan-500/35 shadow-[0_8px_32px_rgba(0,0,0,0.45),0_-1px_0_rgba(34,211,238,0.12)]",
        className,
      )}
    >
      {children}
    </div>
  );
}
