import { memo, useMemo } from "react";

import { ConfluenceThresholdScoreBar } from "./ui/ConfluenceThresholdScoreBar";
import { ScoreBar } from "./ui/ScoreBar";
import { Skeleton } from "./ui/Skeleton";
import { Badge } from "./ui/Badge";
import { CONFLUENCE_SCORE_CAP } from "../lib/confluence-score-constants";
import { formatDecisionGlyph } from "../lib/format-decision-glyph";
import { useScoresStore } from "../store/index";

const CATEGORY_MAX = 40;

export interface LiveScoresPanelProps {
  selectedAsset: string;
}

function LiveScoresPanelInner({ selectedAsset }: LiveScoresPanelProps) {
  const payload = useScoresStore((state) => state.scoresByAsset.get(selectedAsset));
  const isConnected = useScoresStore((state) => state.isConnected);

  const categoryRows = useMemo(() => {
    if (!payload) {
      return [];
    }

    const { categoryScores } = payload;
    return [
      { key: "derivatives", label: "Derivatives", value: categoryScores.derivatives },
      { key: "onchain", label: "On-chain", value: categoryScores.onchain },
      { key: "technical", label: "Technical", value: categoryScores.technical },
      { key: "sentiment", label: "Sentiment", value: categoryScores.sentiment },
      {
        key: "marketContext",
        label: "Market context",
        value: categoryScores.marketContext,
      },
    ];
  }, [payload]);

  if (!payload) {
    return (
      <section aria-busy="true" aria-label="Scores loading">
        <Skeleton className="mb-2 block w-2/3" height={28} />
        <Skeleton className="mb-4 block w-full" height={72} />
        <div className="space-y-3">
          {Array.from({ length: 5 }, (_, index) => (
            <Skeleton className="block w-full" height={36} key={index as number} />
          ))}
        </div>
        <p className="mt-3 text-xs text-[var(--text-tertiary)]">
          {isConnected ? `Waiting for scores for ${selectedAsset}…` : "Connecting to scores channel…"}
        </p>
      </section>
    );
  }

  const glyph = formatDecisionGlyph(payload.decision);

  return (
    <section className="space-y-4" aria-live="polite">
      <header className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <p className="text-[10px] font-bold uppercase tracking-wide text-[var(--text-tertiary)]">
            Asset
          </p>
          <p className="text-lg font-black text-[var(--text-primary)]">{selectedAsset}</p>
        </div>
        <div className="text-right">
          <p className="text-[10px] font-bold uppercase tracking-wide text-[var(--text-tertiary)]">
            Confluence (raw / norm)
          </p>
          <p className="font-mono text-xl font-black tabular-nums text-[var(--accent-cyan)]">
            {payload.totalScore} / {CONFLUENCE_SCORE_CAP}{" "}
            <span className="text-[var(--text-secondary)]">→ {payload.normalizedScore}</span>
          </p>
          <Badge
            variant={
              payload.decision.includes("Sell")
                ? "bear"
                : payload.decision.includes("Buy")
                  ? "bull"
                  : "hold"
            }
            className="mt-1 inline-flex"
          >
            <span aria-hidden>{glyph}</span> {payload.decision}
          </Badge>
          {payload.marlRevision !== null ? (
            <span className="ml-2 inline-flex rounded border border-[var(--accent-purple)] px-2 py-0.5 text-[10px] font-black uppercase tracking-wide text-[var(--accent-purple)]">
              MARL {payload.marlRevision > 0 ? "▲" : payload.marlRevision < 0 ? "▼" : "→"} {payload.marlRevision}%
            </span>
          ) : null}
        </div>
      </header>

      <ConfluenceThresholdScoreBar value={payload.totalScore} label="Confluence ladder" size="md" />

      <div className="space-y-3">
        {categoryRows.map((row) => (
          <ScoreBar key={row.key} label={row.label} max={CATEGORY_MAX} value={row.value} />
        ))}
      </div>

      <footer className="flex flex-wrap gap-3 border-t border-[var(--border)] pt-3 text-[11px] text-[var(--text-tertiary)]">
        <span>Passes gate {payload.passesGate ? "● yes" : "○ no"}</span>
        <span tabular-nums>Confidence {(payload.confidence * 100).toFixed(0)}%</span>
        <span className="tabular-nums" title={payload.cycleTs}>
          Cycle {payload.cycleTs.slice(11, 19) || payload.cycleTs}
        </span>
      </footer>
    </section>
  );
}

export const LiveScoresPanel = memo(LiveScoresPanelInner);
