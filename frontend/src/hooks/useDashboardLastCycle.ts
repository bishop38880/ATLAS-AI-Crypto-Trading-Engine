import { useScoresStore } from "../store/index";
import {
  calculate_age_minutes_from_iso,
  format_minutes_since_label,
} from "../lib/format-relative-age";

/** Human label for freshest `cycleTs` across streamed score payloads (excluding aggregate lane). */
export function useDashboardLastCycleHumanLabel(): string {
  return useScoresStore((state) => {
    let latest_iso: string | null = null;
    let latest_ms = NaN;

    for (const row of state.scoresByAsset.values()) {
      if (row.asset === "__aggregate__") {
        continue;
      }

      const candidate = Date.parse(row.cycleTs);
      if (!Number.isFinite(candidate)) {
        continue;
      }

      if (!Number.isFinite(latest_ms) || candidate > latest_ms) {
        latest_ms = candidate;
        latest_iso = row.cycleTs;
      }
    }

    if (latest_iso === null) {
      return "—";
    }

    const minutes = calculate_age_minutes_from_iso(latest_iso);
    return format_minutes_since_label(minutes);
  });
}
