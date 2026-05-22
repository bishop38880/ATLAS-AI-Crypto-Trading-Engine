import { describe, expect, it } from "vitest";

type WsScorePayload = {
  asset: string;
  totalScore: number;
  normalizedScore: number;
  decision: string;
  confidence: number;
  gateThreshold: number;
  categoryScores: Record<string, number>;
  llmTierLabel: string;
  cycleNumber: number;
  cycleTimestamp: string;
  price: string;
  fundingRate: string;
  openInterest: string;
  obtiSummary: string | null;
  obtiSide: string | null;
};

function validateWsScorePayload(payload: WsScorePayload): void {
  expect(typeof payload.asset).toBe("string");
  expect(Number.isInteger(payload.totalScore)).toBe(true);
  expect(payload.totalScore).toBeGreaterThanOrEqual(0);
  expect(payload.totalScore).toBeLessThanOrEqual(220);
  expect(Number.isInteger(payload.normalizedScore)).toBe(true);
  expect(payload.normalizedScore).toBeGreaterThanOrEqual(0);
  expect(payload.normalizedScore).toBeLessThanOrEqual(100);
  expect(typeof payload.decision).toBe("string");
  expect(payload.confidence).toBeGreaterThanOrEqual(0);
  expect(payload.confidence).toBeLessThanOrEqual(1);
  expect(Number.isInteger(payload.gateThreshold)).toBe(true);
  expect(typeof payload.llmTierLabel).toBe("string");
  expect(Number.isInteger(payload.cycleNumber)).toBe(true);
  expect(typeof payload.cycleTimestamp).toBe("string");
  expect(typeof payload.price).toBe("string");
  expect(typeof payload.fundingRate).toBe("string");
  expect(typeof payload.openInterest).toBe("string");
  expect(typeof payload.obtiSide === "string" || payload.obtiSide === null).toBe(true);

  for (const value of Object.values(payload.categoryScores)) {
    expect(Number.isInteger(value)).toBe(true);
  }
}

describe("WS score payload contract", () => {
  it("keeps financial fields as strings and score fields as integers", () => {
    validateWsScorePayload({
      asset: "BTCUSDT",
      totalScore: 167,
      normalizedScore: 76,
      decision: "BUY",
      confidence: 0.78,
      gateThreshold: 140,
      categoryScores: {
        derivatives: 64,
        whale_onchain: 41,
        technical: 38,
        sentiment: 24,
        context: 16,
      },
      llmTierLabel: "DeepSeek V3 (FAST)",
      cycleNumber: 1247,
      cycleTimestamp: "2026-05-05T08:00:00Z",
      price: "93240.50",
      fundingRate: "+0.012",
      openInterest: "1240000000",
      obtiSummary: "balanced",
      obtiSide: null,
    });
  });
});
