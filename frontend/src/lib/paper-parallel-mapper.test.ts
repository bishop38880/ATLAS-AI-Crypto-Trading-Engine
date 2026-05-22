import { describe, expect, it } from "vitest";

import { mapPaperParallelValidationPayload } from "./paper-parallel-mapper";

describe("mapPaperParallelValidationPayload", () => {
  it("maps camelCase payloads", () => {
    const parsed = mapPaperParallelValidationPayload({
      initialUsd: "100000",
      endingUsd: "101234",
      equityCurve: [{ ts: "2026-01-01T00Z", equityUsd: "100000" }],
      drawdownCurve: [{ ts: "2026-01-01T00Z", drawdownPct: 0 }],
      weeklySharpeAnnualized: 1.42,
      tierWinRates: {
        "180_plus": { wins: 3, trades: 5, winRate: 0.6 },
        under_150: { wins: 0, trades: 2, winRate: 0 },
        "150_179": { wins: 1, trades: 4, winRate: 0.25 },
      },
      rowCountUsed: 11,
      pricingModel: "score_delta_back_anchor",
      disclaimer: "Test disclaimer",
    });

    expect(parsed).not.toBeNull();
    expect(parsed?.endingUsd).toBe("101234");
    expect(parsed?.tierWinRates["180_plus"].winRate).toBeCloseTo(0.6);
  });
});
