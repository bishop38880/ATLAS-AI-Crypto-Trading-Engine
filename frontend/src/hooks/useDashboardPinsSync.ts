import { useEffect } from "react";

import { apiUrl } from "../lib/url";
import { useAssetUniverseStore } from "../store/assetUniverseStore";

/**
 * Mirrors locally persisted dashboard pins to Redis so the autonomous scorer
 * includes those symbols (same ladder behaviour as rotation-backed pairs).
 */
export function useDashboardPinsSync(): void {
  const pinned_pairs = useAssetUniverseStore((state) => state.dashboardAssets);

  useEffect(() => {
    const timeout_handle = window.setTimeout(() => {
      void (async () => {
        try {
          await fetch(apiUrl("/api/dashboard/pins"), {
            method: "POST",
            headers: { "Content-Type": "application/json", Accept: "application/json" },
            body: JSON.stringify({ pairs: pinned_pairs }),
          });
        } catch {
          /* Offline API — pins remain client-local until next successful POST */
        }
      })();
    }, 600);

    return () => window.clearTimeout(timeout_handle);
  }, [pinned_pairs]);
}
