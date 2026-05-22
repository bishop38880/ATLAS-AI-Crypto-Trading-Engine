import type { ReactElement } from "react";

import type { GnnInferenceStatsViewModel } from "../../lib/calculate_gnn_inference_display";

export interface GNNInferenceStatsProps {
  model: GnnInferenceStatsViewModel;
}

export function GNNInferenceStats(props: GNNInferenceStatsProps): ReactElement {
  const { model } = props;

  return (
    <section
      className="rounded-[var(--radius-lg)] border border-[var(--border)] bg-[var(--bg-surface)] p-[var(--space-4)]"
      aria-live="polite"
      aria-label="GNN inference performance"
    >
      <header className="mb-[var(--space-3)] flex flex-wrap items-baseline justify-between gap-2 border-b border-[var(--border)] pb-[var(--space-3)]">
        <h2 className="font-[family-name:var(--font-display)] text-sm font-semibold text-[var(--text-primary)]">
          Inference Performance
        </h2>
        <span className="font-data text-xs text-[var(--text-tertiary)]">[{model.periodLabel}]</span>
      </header>

      <dl className="space-y-2 font-data text-xs text-[var(--text-secondary)]">
        <div className="flex justify-between gap-3">
          <dt>Total inferences</dt>
          <dd className="text-[var(--text-primary)]">{model.totalInferences}</dd>
        </div>
        <div className="flex justify-between gap-3">
          <dt>Avg latency</dt>
          <dd className="text-[var(--text-primary)]">{model.avgLatencyLine}</dd>
        </div>
        <div className="flex justify-between gap-3">
          <dt>P99 latency</dt>
          <dd className="text-[var(--text-primary)]">{model.p99LatencyLine}</dd>
        </div>
        <div className="flex justify-between gap-3">
          <dt>Budget compliance</dt>
          <dd className="text-[var(--text-primary)]">{model.budgetLine}</dd>
        </div>
      </dl>

      <div className="mt-[var(--space-4)] border-t border-[var(--border)] pt-[var(--space-3)]">
        <p className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-[var(--text-tertiary)]">
          Graph stats
        </p>
        <dl className="space-y-2 font-data text-xs text-[var(--text-secondary)]">
          <div className="flex justify-between gap-3">
            <dt>Avg wallet nodes</dt>
            <dd className="text-[var(--text-primary)]">{model.avgWalletNodes}</dd>
          </div>
          <div className="flex justify-between gap-3">
            <dt>Avg transfer edges</dt>
            <dd className="text-[var(--text-primary)]">{model.avgTransferEdges}</dd>
          </div>
          <div className="flex justify-between gap-3">
            <dt>Avg correlation edges</dt>
            <dd className="text-[var(--text-primary)]">{model.avgCorrelationEdges}</dd>
          </div>
        </dl>
      </div>
    </section>
  );
}
