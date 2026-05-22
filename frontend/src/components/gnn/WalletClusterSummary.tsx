import type { ReactElement } from "react";

import type { WalletClusterPanelViewModel } from "../../lib/calculate_wallet_cluster_display";

export interface WalletClusterSummaryProps {
  assetBase: string;
  model: WalletClusterPanelViewModel;
}

export function WalletClusterSummary(props: WalletClusterSummaryProps): ReactElement {
  const { assetBase, model } = props;
  const sym = assetBase.trim().toUpperCase();

  return (
    <section
      className="rounded-[var(--radius-lg)] border border-[var(--border)] bg-[var(--bg-surface)] p-[var(--space-4)]"
      aria-live="polite"
      aria-label={`Wallet cluster analysis for ${sym}`}
    >
      <header className="mb-[var(--space-3)] border-b border-[var(--border)] pb-[var(--space-3)]">
        <h2 className="font-[family-name:var(--font-display)] text-sm font-semibold text-[var(--text-primary)]">
          Wallet Cluster Analysis{" "}
          <span className="font-data text-[var(--accent-cyan)]">[{sym}]</span>
        </h2>
      </header>

      <dl className="grid gap-y-2 font-data text-xs text-[var(--text-secondary)] sm:grid-cols-2">
        <div className="flex justify-between gap-3 border-b border-[var(--border)] border-opacity-50 py-2 sm:border-0">
          <dt>Clusters detected</dt>
          <dd className="text-[var(--text-primary)]">{model.clustersDetected ?? "—"}</dd>
        </div>
        <div className="flex justify-between gap-3 border-b border-[var(--border)] border-opacity-50 py-2 sm:border-0">
          <dt>Largest cluster</dt>
          <dd className="text-right text-[var(--text-primary)]">
            {model.largestClusterWallets ?? "—"} wallets{" "}
            {model.largestBehavior !== null ? (
              <span className="whitespace-nowrap">
                ({model.largestBehavior.toUpperCase()} {model.largestGlyph})
              </span>
            ) : (
              ""
            )}
          </dd>
        </div>
        <div className="flex justify-between gap-3 border-b border-[var(--border)] border-opacity-50 py-2 sm:border-0">
          <dt>Anomaly wallets</dt>
          <dd className="text-[var(--text-primary)]">{model.anomalyWallets ?? "—"}</dd>
        </div>
        <div className="flex justify-between gap-3 border-b border-[var(--border)] border-opacity-50 py-2 sm:border-0">
          <dt>Smart money score</dt>
          <dd className="text-right text-[var(--text-primary)]">
            {model.smartMoneyScore !== null ? model.smartMoneyScore.toFixed(2) : "—"}
            {model.smartMoneyLabel !== null ? (
              <span className="block text-[var(--text-tertiary)]">({model.smartMoneyLabel})</span>
            ) : null}
          </dd>
        </div>
      </dl>

      <ul className="mt-[var(--space-4)] space-y-2 font-data text-xs text-[var(--text-secondary)]">
        {model.subclusters.length === 0 ? (
          <li className="text-[var(--text-tertiary)]">No cluster rows in payload.</li>
        ) : (
          model.subclusters.map((row) => (
            <li key={row.label} className="border-l-2 border-[var(--border-accent)] pl-3">
              <span className="text-[var(--text-primary)]">{row.label}</span>: {row.wallets} wallets —{" "}
              {row.behavior.toUpperCase()}
            </li>
          ))
        )}
      </ul>

      <p className="mt-[var(--space-4)] font-data text-xs text-[var(--text-secondary)]">{model.stressLine}</p>
    </section>
  );
}
