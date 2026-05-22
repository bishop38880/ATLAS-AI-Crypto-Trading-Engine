import { describe, expect, it } from "vitest";

import { calculate_confluence_action_badge_presentation } from "./calculate-confluence-action-badge";
import {
  CONFLUENCE_GATE_THRESHOLD_DEFAULT,
  CONFLUENCE_LADDER_THRESHOLD_T1,
  CONFLUENCE_LADDER_THRESHOLD_T3,
} from "./confluence-score-constants";

describe("calculate_confluence_action_badge_presentation", () => {
  const gate = CONFLUENCE_GATE_THRESHOLD_DEFAULT;

  it("shows BLOCKED when veto is active regardless of score", () => {
    const row = calculate_confluence_action_badge_presentation({
      totalScoreCapped: 200,
      gateThreshold: gate,
      vetoActive: true,
      decision: "Strong Buy",
    });
    expect(row.label).toBe("BLOCKED");
    expect(row.variant).toBe("degraded");
  });

  it("shows NO POSITION below T1", () => {
    const row = calculate_confluence_action_badge_presentation({
      totalScoreCapped: CONFLUENCE_LADDER_THRESHOLD_T1 - 1,
      gateThreshold: gate,
      vetoActive: false,
      decision: "Sell",
    });
    expect(row.label).toContain("NO POSITION");
    expect(row.variant).toBe("no-position");
  });

  it("shows HOLD between T1 and gate", () => {
    const mid = Math.round((CONFLUENCE_LADDER_THRESHOLD_T1 + gate) / 2);
    const row = calculate_confluence_action_badge_presentation({
      totalScoreCapped: mid,
      gateThreshold: gate,
      vetoActive: false,
      decision: "Hold",
    });
    expect(row.label).toContain("HOLD");
    expect(row.variant).toBe("hold");
  });

  it("shows READY when gate cleared but below elite tier", () => {
    const mid = Math.round((gate + CONFLUENCE_LADDER_THRESHOLD_T3) / 2);
    const row = calculate_confluence_action_badge_presentation({
      totalScoreCapped: mid,
      gateThreshold: gate,
      vetoActive: false,
      decision: "Buy",
    });
    expect(row.label).toBe("READY ↑");
    expect(row.variant).toBe("healthy");
  });

  it("shows TRADE in elite zone with directional styling (186 long)", () => {
    const row = calculate_confluence_action_badge_presentation({
      totalScoreCapped: 186,
      gateThreshold: gate,
      vetoActive: false,
      decision: "Buy",
    });
    expect(row.label).toBe("TRADE ↑");
    expect(row.variant).toBe("strong-buy");
  });

  it("shows TRADE for strong buy with double arrow glyph", () => {
    const row = calculate_confluence_action_badge_presentation({
      totalScoreCapped: CONFLUENCE_LADDER_THRESHOLD_T3 + 5,
      gateThreshold: gate,
      vetoActive: false,
      decision: "Strong Buy",
    });
    expect(row.label).toBe("TRADE ↑↑");
    expect(row.variant).toBe("strong-buy");
  });

  it("shows READY/TRADE on short side with sell variants", () => {
    const ready_short = calculate_confluence_action_badge_presentation({
      totalScoreCapped: 165,
      gateThreshold: gate,
      vetoActive: false,
      decision: "Sell",
    });
    expect(ready_short.label).toBe("READY ▼");
    expect(ready_short.variant).toBe("sell");

    const trade_short = calculate_confluence_action_badge_presentation({
      totalScoreCapped: 190,
      gateThreshold: gate,
      vetoActive: false,
      decision: "Strong Sell",
    });
    expect(trade_short.label).toBe("TRADE ↓↓");
    expect(trade_short.variant).toBe("strong-sell");
  });
});
