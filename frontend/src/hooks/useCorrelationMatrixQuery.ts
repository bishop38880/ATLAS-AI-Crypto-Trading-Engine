import { useQuery } from "@tanstack/react-query";

import type { AssetCorrelationMatrixWire } from "../lib/correlation-matrix-types";
import { is_asset_correlation_matrix_wire } from "../lib/correlation-matrix-types";
import { apiUrl } from "../lib/url";

async function correlation_matrix_fetch(positions_csv: string): Promise<AssetCorrelationMatrixWire> {
  const suffix = positions_csv.trim().length > 0 ? `?positions=${encodeURIComponent(positions_csv)}` : "";
  const response = await fetch(apiUrl(`/api/dashboard/correlation-matrix${suffix}`));
  if (!response.ok) {
    throw new Error(`correlation_matrix_http_${response.status}`);
  }
  const payload: unknown = await response.json().catch(() => null);
  if (!is_asset_correlation_matrix_wire(payload)) {
    throw new Error("correlation_matrix_shape_invalid");
  }
  return payload;
}

/** Rolling correlation snapshot — respects dashboard ladder + optional open-slot bases. */
export function useCorrelationMatrixQuery(positions_csv: string) {
  return useQuery({
    queryKey: ["dashboard", "correlation_matrix_v2", positions_csv],
    queryFn: () => correlation_matrix_fetch(positions_csv),
    staleTime: 45_000,
    gcTime: 300_000,
  });
}
