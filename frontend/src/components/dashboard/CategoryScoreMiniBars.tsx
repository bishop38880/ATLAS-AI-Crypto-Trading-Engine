import { memo, useMemo } from "react";

import {
  CONFLUENCE_CATEGORY_MAX,
  type ConfluenceCategoryScoreKey,
} from "../../lib/confluence-score-constants";
import { cn } from "../../lib/cn";

import type { ScoresPayload } from "../../store/index";

interface CategoryBarConfig {
  readonly key: ConfluenceCategoryScoreKey;
  readonly label: string;
  /** CSS variable name without `var()` — e.g. `--accent-violet`. */
  readonly colorVar: string;
}

const CATEGORY_BAR_CONFIG: readonly CategoryBarConfig[] = [
  { key: "derivatives", label: "Derivatives", colorVar: "--accent-violet" },
  { key: "onchain", label: "Whale / on-chain", colorVar: "--accent-cyan" },
  { key: "technical", label: "Technical", colorVar: "--accent-teal" },
  { key: "sentiment", label: "Sentiment", colorVar: "--accent-amber" },
  { key: "marketContext", label: "Market context", colorVar: "--success" },
] as const;

export interface CategoryScoreMiniBarsProps {
  categoryScores: ScoresPayload["categoryScores"] | undefined;
  className?: string;
}

export const CategoryScoreMiniBars = memo(function CategoryScoreMiniBars({
  categoryScores,
  className,
}: CategoryScoreMiniBarsProps) {
  const segments = useMemo(() => {
    return CATEGORY_BAR_CONFIG.map((config) => {
      const maxPoints = CONFLUENCE_CATEGORY_MAX[config.key];
      const rawValue =
        categoryScores === undefined ? 0 : Math.max(0, categoryScores[config.key]);
      const fillRatio = maxPoints > 0 ? Math.min(1, rawValue / maxPoints) : 0;
      const title = `${config.label}: ${Math.round(rawValue)} / ${maxPoints} pts`;
      return { config, rawValue, fillRatio, title, maxPoints };
    });
  }, [categoryScores]);

  const summaryLabel = useMemo(() => {
    if (categoryScores === undefined) {
      return "Category score breakdown: no data yet.";
    }
    return [
      "Category score breakdown.",
      ...segments.map((s) => `${s.config.label} ${Math.round(s.rawValue)} of ${s.maxPoints} points.`),
    ].join(" ");
  }, [categoryScores, segments]);

  return (
    <div
      className={cn("flex w-full items-end gap-1", className)}
      role="group"
      aria-label={summaryLabel}
    >
      {segments.map((segment) => (
        <div
          key={segment.config.key}
          className="flex min-w-0 flex-1 flex-col justify-end"
          title={segment.title}
        >
          <div
            className={cn(
              "h-2 w-full overflow-hidden rounded-sm bg-[var(--bg-elevated)]",
              categoryScores === undefined && "opacity-50",
            )}
          >
            <div
              className="h-full min-h-px rounded-sm transition-[width] duration-300 ease-out"
              style={{
                width: `${segment.fillRatio * 100}%`,
                backgroundColor: `var(${segment.config.colorVar})`,
                opacity: categoryScores === undefined ? 0.35 : 0.92,
              }}
            />
          </div>
        </div>
      ))}
    </div>
  );
});
