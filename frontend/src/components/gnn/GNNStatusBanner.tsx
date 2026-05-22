import type { ReactElement } from "react";

export function GNNStatusBanner(): ReactElement {
  return (
    <section
      className="rounded-[var(--radius-lg)] border border-[rgba(124,77,255,0.55)] bg-[rgba(26,14,48,0.96)] px-[var(--space-4)] py-[var(--space-4)] shadow-[0_0_24px_rgba(124,77,255,0.18)]"
      aria-label="GNN shadow mode notice"
    >
      <div className="flex flex-wrap items-start gap-3">
        <span className="display text-xl text-[var(--accent-violet)]" aria-hidden>
          ⬡
        </span>
        <div className="min-w-0 flex-1 space-y-2">
          <h1 className="font-[family-name:var(--font-display)] text-base font-semibold tracking-wide text-[var(--text-primary)]">
            GNN INTELLIGENCE — SHADOW MODE
          </h1>
          <p className="text-sm leading-relaxed text-[var(--text-secondary)]">
            Outputs tracked for Phase F validation. Not scored live.
          </p>
          <p className="text-xs leading-relaxed text-[var(--text-tertiary)]">
            Promotion requires: 200 trades + SHAP top-10 + human sign-off
          </p>
        </div>
      </div>
    </section>
  );
}
