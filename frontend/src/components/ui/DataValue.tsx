import { cn } from "../../lib/cn";

export interface DataValueProps {
  value: number | string;
  change?: number;
  prefix?: string;
  suffix?: string;
  size?: "xs" | "sm" | "md" | "lg" | "xl";
  mono?: boolean;
}

function parseDisplayNumber(value: number | string): string {
  if (typeof value === "number") {
    return Number.isFinite(value) ? value.toLocaleString("en-US") : "—";
  }
  const trimmed = value.trim();
  if (trimmed === "") {
    return "—";
  }
  const n = Number(trimmed.replace(/,/g, ""));
  if (!Number.isFinite(n)) {
    return trimmed;
  }
  return n.toLocaleString("en-US");
}

const sizeClass: Record<NonNullable<DataValueProps["size"]>, string> = {
  xs: "text-xs",
  sm: "text-sm",
  md: "text-base",
  lg: "text-lg",
  xl: "text-2xl",
};

export function DataValue({
  value,
  change,
  prefix,
  suffix,
  size = "md",
  mono = true,
}: DataValueProps) {
  const changeDefined = change !== undefined && Number.isFinite(change);
  const changePositive = changeDefined && (change as number) > 0;
  const changeNegative = changeDefined && (change as number) < 0;

  return (
    <span className={cn("inline-flex flex-wrap items-baseline gap-1.5", mono && "font-data")}>
      <span
        className={cn(
          "font-semibold tabular-nums tracking-tight text-[var(--text-primary)]",
          sizeClass[size],
        )}
      >
        {prefix !== undefined ? prefix : ""}
        {parseDisplayNumber(value)}
        {suffix !== undefined ? suffix : ""}
      </span>
      {changeDefined && (
        <span
          className={cn(
            "text-xs font-medium tabular-nums",
            changePositive && "text-[var(--success)]",
            changeNegative && "text-[var(--danger)]",
            !changePositive && !changeNegative && "text-[var(--text-tertiary)]",
          )}
        >
          {changePositive ? "▲" : changeNegative ? "▼" : "—"}{" "}
          {Math.abs(change as number).toFixed(2)}%
        </span>
      )}
    </span>
  );
}
