import { useMemo, useState } from "react";

import {
  calculate_agent_scoreboard_rows,
  type AgentScoreboardDisplayRow,
} from "../../lib/calculate_agent_scoreboard_rows";
import { CONFLUENCE_SCORE_CAP } from "../../lib/confluence-score-constants";
import { resolve_scores_snapshot_for_base } from "../../lib/resolve-scores-snapshot";
import type { ScoresPayload } from "../../store/index";
import { useAgentStore, useScoresStore } from "../../store/index";
import { PageHeader } from "../ui/PageHeader";
import { AgentDetailDrawer } from "./AgentDetailDrawer";
import { AgentScoreboard } from "./AgentScoreboard";
import { ConfluenceMeter } from "./ConfluenceMeter";
import { MARLDeliberationPanel } from "./MARLDeliberationPanel";

/** Overlay category ladders from `/ws/scores` when snapshot matches selected base. */
function apply_confluence_snapshot_scores(
  rows: AgentScoreboardDisplayRow[],
  snapshot: ScoresPayload,
): AgentScoreboardDisplayRow[] {
  /** Matches ConfluenceMeter "Context" slice and rollup_flat_category_scores_for_ui.marketContext */
  const sub: Partial<Record<string, number>> = {
    derivatives: snapshot.categoryScores.derivatives,
    technical: snapshot.categoryScores.technical,
    onchain: snapshot.categoryScores.onchain,
    sentiment: snapshot.categoryScores.sentiment,
    regime: snapshot.categoryScores.marketContext,
  };

  return rows.map((row) => {
    if (row.catalog.role !== "CONFLUENCE") {
      return row;
    }
    const next = sub[row.catalog.id];
    if (typeof next !== "number" || !Number.isFinite(next)) {
      return row;
    }
    return { ...row, score: Math.round(Math.max(0, next)) };
  });
}

function clear_confluence_snapshot_scores(rows: AgentScoreboardDisplayRow[]): AgentScoreboardDisplayRow[] {
  return rows.map((row) => {
    if (row.catalog.role !== "CONFLUENCE") {
      return row;
    }
    return {
      ...row,
      score: 0,
      explanation: "Waiting for the selected asset snapshot before displaying confluence points.",
    };
  });
}

export function AgentsPage() {
  const [asset, set_asset] = useState("BTC");
  const [drawer_row, set_drawer_row] = useState<AgentScoreboardDisplayRow | null>(null);

  const agents = useAgentStore((state) => state.agents);
  const scores_by_asset = useScoresStore((state) => state.scoresByAsset);

  const scores_snapshot = useMemo(
    () => resolve_scores_snapshot_for_base(scores_by_asset, asset),
    [scores_by_asset, asset],
  );

  const scoreboard_rows = useMemo(() => {
    const rows = calculate_agent_scoreboard_rows(agents);
    if (scores_snapshot === undefined) {
      return clear_confluence_snapshot_scores(rows);
    }
    return apply_confluence_snapshot_scores(rows, scores_snapshot);
  }, [agents, scores_snapshot]);

  return (
    <div className="mx-auto max-w-6xl space-y-6 font-[family-name:var(--font-body)] text-[var(--text-primary)]">
      <PageHeader
        kicker="Polaris intelligence"
        title="Agents & MARL deliberation"
        description={
          <>
            Live `/ws/agents` pulses aligned with the operator ladder model: CONFLUENCE sums stop at{" "}
            {CONFLUENCE_SCORE_CAP} points; overlays and veto agents are labelled explicitly so sizing overlays are
            never mistaken for ladder contributions. Open any agent to see its vote on each of the last 20 decisions
            for the asset selected on the meter (bullish/bearish/neutral vs final call), plus whether that vote matched
            the realised outcome once PROMETHEUS labels the trade — the operational basis for weight calibration.
          </>
        }
      />

      <div className="grid gap-4 lg:grid-cols-[minmax(0,2fr)_minmax(0,3fr)]">
        <AgentScoreboard rows={scoreboard_rows} onOpenRow={set_drawer_row} />
        <ConfluenceMeter asset={asset} onAssetChange={set_asset} snapshot={scores_snapshot} />
      </div>

      <MARLDeliberationPanel asset={asset} snapshotTotal={scores_snapshot?.totalScore ?? null} />

      <AgentDetailDrawer row={drawer_row} asset={asset} onClose={() => set_drawer_row(null)} />
    </div>
  );
}
