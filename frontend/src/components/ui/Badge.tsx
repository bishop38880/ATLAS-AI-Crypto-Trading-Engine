import type { ReactNode } from "react";

import { cn } from "../../lib/cn";

export type BadgeVariant =
  | "strong-buy"
  | "buy"
  | "hold"
  | "sell"
  | "strong-sell"
  | "no-position"
  | "healthy"
  | "degraded"
  | "stale"
  | "open"
  | "half-open"
  | "bull"
  | "bear"
  | "ranging"
  | "shadow"
  | "live";

export interface BadgeProps {
  variant?: BadgeVariant;
  children: ReactNode;
  className?: string;
}

const variantClass: Record<BadgeVariant, string> = {
  "strong-buy":
    "border-emerald-500/40 bg-emerald-950/50 text-emerald-300 shadow-[inset_0_0_12px_rgba(16,185,129,0.12)]",
  buy: "border-emerald-600/30 bg-emerald-950/35 text-emerald-200/90",
  hold: "border-amber-500/35 bg-amber-950/45 text-amber-200 shadow-[inset_0_0_10px_rgba(245,158,11,0.08)]",
  sell: "border-red-500/35 bg-red-950/45 text-red-200",
  "strong-sell": "border-red-600/45 bg-red-950/55 text-red-300 shadow-[inset_0_0_12px_rgba(239,68,68,0.1)]",
  "no-position": "border-slate-600/40 bg-slate-900/60 text-slate-400",
  healthy:
    "border-emerald-500/35 bg-emerald-950/40 text-emerald-300 shadow-[0_0_10px_rgba(52,211,153,0.12)]",
  degraded: "border-amber-500/35 bg-amber-950/40 text-amber-200",
  stale: "border-amber-500/50 bg-amber-950/55 text-amber-100 shadow-[0_0_8px_rgba(245,158,11,0.15)]",
  open: "border-cyan-500/35 bg-cyan-950/40 text-cyan-200",
  "half-open": "border-amber-500/35 bg-amber-950/40 text-amber-200",
  bull: "border-emerald-500/40 bg-emerald-950/50 text-emerald-300 shadow-[0_0_10px_rgba(52,211,153,0.12)]",
  bear: "border-red-500/35 bg-red-950/45 text-red-200",
  ranging: "border-amber-500/35 bg-amber-950/45 text-amber-200",
  shadow: "border-violet-500/35 bg-violet-950/45 text-violet-200 shadow-[0_0_10px_rgba(167,139,250,0.1)]",
  live: "border-teal-500/35 bg-teal-950/45 text-teal-200",
};

export function Badge({ variant = "hold", children, className }: BadgeProps) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full border px-2.5 py-0.5 text-[10px] font-semibold uppercase tracking-[0.12em]",
        variantClass[variant],
        className,
      )}
    >
      {children}
    </span>
  );
}
