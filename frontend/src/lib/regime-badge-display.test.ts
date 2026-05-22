import { describe, expect, it } from "vitest";

import { detects_dashboard_regime_mask, normalize_hmm_regime_token } from "./regime-badge-display";
import type { SystemHealth } from "../store/index";

describe("normalize_hmm_regime_token", () => {
  it("returns native tokens for HMM labels", () => {
    expect(normalize_hmm_regime_token("VOLATILE")).toBe("volatile");
    expect(normalize_hmm_regime_token("bear")).toBe("bear");
    expect(normalize_hmm_regime_token("BULL")).toBe("bull");
    expect(normalize_hmm_regime_token("RANGING")).toBeNull();
  });
});

describe("detects_dashboard_regime_mask", () => {
  it("detects volatile HMM under ranging bucket", () => {
    const health: SystemHealth = {
      overallStatus: "HEALTHY",
      currentRegime: "RANGING",
      regimeConfidence: 55,
      activeCycle: true,
      nextCycleSeconds: 60,
      openPositions: 0,
      portfolioPnl24h: "0",
      hmmRegimeNative: "volatile",
    };
    expect(detects_dashboard_regime_mask(health)).toBe(true);
  });
});
