import type { CSSProperties } from "react";

import type { LeadLagMatrixCell } from "./calculate_lead_lag_matrix";

/**
 * Blue tint for positive coefficients, red for negative. Diagonal / empty cells stay neutral.
 */
export function calculate_lead_lag_cell_surface_style(cell: LeadLagMatrixCell): CSSProperties {
  if (cell.isDiagonal || cell.coefficient === null) {
    return {
      backgroundColor: "rgba(255, 255, 255, 0.03)",
      color: "var(--text-secondary)",
    };
  }
  const magnitude = Math.min(1, Math.abs(cell.coefficient) / 0.35);
  if (cell.coefficient > 0) {
    const alpha = 0.06 + magnitude * 0.42;
    return {
      backgroundColor: `rgba(0, 229, 255, ${alpha})`,
      color: "var(--text-primary)",
    };
  }
  const alpha = 0.07 + magnitude * 0.48;
  return {
    backgroundColor: `rgba(255, 82, 82, ${alpha})`,
    color: "var(--text-primary)",
  };
}
