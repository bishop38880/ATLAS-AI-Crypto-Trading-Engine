import type { ReactElement } from "react";

import type { LeadLagMatrixViewModel } from "../../lib/calculate_lead_lag_matrix";
import { calculate_lead_lag_cell_surface_style } from "../../lib/calculate_lead_lag_cell_style";

export interface LASSOLeadLagHeatmapProps {
  model: LeadLagMatrixViewModel;
}

export function LASSOLeadLagHeatmap(props: LASSOLeadLagHeatmapProps): ReactElement {
  const { model } = props;

  return (
    <section
      className="rounded-[var(--radius-lg)] border border-[var(--border)] bg-[var(--bg-surface)] p-[var(--space-4)]"
      aria-live="polite"
      aria-label="Cross-asset lead-lag coefficients"
    >
      <header className="mb-[var(--space-3)]">
        <h2 className="font-[family-name:var(--font-display)] text-sm font-semibold text-[var(--text-primary)]">
          CROSS-ASSET LEAD-LAG COEFFICIENTS
        </h2>
        <p className="mt-1 text-xs text-[var(--text-tertiary)]">
          Positive = predictor leads target 30 minutes ahead.
        </p>
      </header>

      <div className="overflow-x-auto [-webkit-overflow-scrolling:touch]">
        <table className="min-w-[520px] border-collapse font-data text-[11px]">
          <thead>
            <tr>
              <th
                scope="col"
                className="sticky left-0 z-10 bg-[var(--bg-surface)] px-2 py-2 text-left text-[var(--text-tertiary)]"
              />
              {model.labels.map((label) => (
                <th
                  key={`c-${label}`}
                  scope="col"
                  className="min-w-[52px] px-1 py-2 text-center font-semibold text-[var(--text-secondary)]"
                >
                  <span aria-hidden>→ </span>
                  {label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {model.labels.map((row_label, row_idx) => (
              <tr key={`r-${row_label}`}>
                <th
                  scope="row"
                  className="sticky left-0 z-10 bg-[var(--bg-surface)] px-2 py-1 text-left font-semibold text-[var(--text-secondary)]"
                >
                  {row_label}
                </th>
                {model.rows[row_idx]?.map((cell, col_idx) => {
                  const style = calculate_lead_lag_cell_surface_style(cell);
                  const key = `${row_label}-${model.labels[col_idx] ?? col_idx}`;
                  return (
                    <td
                      key={key}
                      className={`border border-[var(--border)] px-1 py-2 text-center ${cell.isStrong ? "font-bold" : "font-medium"}`}
                      style={style}
                    >
                      {cell.displayText}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {model.seesaw !== null ? (
        <aside
          className="mt-[var(--space-4)] rounded-[var(--radius-md)] border border-[var(--warning)] border-opacity-35 bg-[rgba(255,171,64,0.06)] p-[var(--space-3)]"
          aria-label="Seesaw effect observation"
        >
          <h3 className="font-[family-name:var(--font-display)] text-xs font-semibold text-[var(--warning)]">
            {model.seesaw.headline}
          </h3>
          {model.seesaw.bodyLines.map((line, idx) => (
            <p key={idx} className="mt-2 text-xs leading-relaxed text-[var(--text-secondary)]">
              {line}
            </p>
          ))}
        </aside>
      ) : null}
    </section>
  );
}
