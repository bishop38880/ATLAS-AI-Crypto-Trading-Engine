import type { ButtonHTMLAttributes, ReactNode } from "react";

import { cn } from "../../lib/cn";

export type ButtonVariant = "pill" | "pill-primary" | "pill-ghost" | "pill-danger";
export type ButtonSize = "xs" | "sm" | "md";

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
  children: ReactNode;
}

const variantClass: Record<ButtonVariant, string> = {
  pill: "btn-pill",
  "pill-primary": "btn-pill btn-pill-primary",
  "pill-ghost": "btn-pill btn-pill-ghost",
  "pill-danger": "btn-pill border-red-500/30 bg-red-950/40 text-red-200 hover:border-red-400/45 hover:bg-red-950/65",
};

const sizeClass: Record<ButtonSize, string> = {
  xs: "px-2 py-1 text-[10px]",
  sm: "px-2.5 py-1.5 text-[11px]",
  md: "px-3 py-2 text-xs",
};

export function Button({
  variant = "pill",
  size = "sm",
  className,
  type = "button",
  children,
  ...rest
}: ButtonProps) {
  return (
    <button type={type} className={cn(variantClass[variant], sizeClass[size], className)} {...rest}>
      {children}
    </button>
  );
}
