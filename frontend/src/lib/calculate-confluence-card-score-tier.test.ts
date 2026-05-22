import { describe, expect, it } from "vitest";

import {
  calculate_confluence_card_score_tier,
  calculate_confluence_card_tier_border_classes,
} from "./calculate-confluence-card-score-tier";

import {
  CONFLUENCE_LADDER_THRESHOLD_T1,
  CONFLUENCE_LADDER_THRESHOLD_T2,
  CONFLUENCE_LADDER_THRESHOLD_T3,
} from "./confluence-score-constants";

describe("calculate_confluence_card_score_tier", () => {
  it("returns muted below T1", () => {
    expect(calculate_confluence_card_score_tier(0)).toBe("muted");
    expect(calculate_confluence_card_score_tier(CONFLUENCE_LADDER_THRESHOLD_T1 - 1)).toBe("muted");
  });

  it("returns amber from T1 through T2 − 1", () => {
    expect(calculate_confluence_card_score_tier(CONFLUENCE_LADDER_THRESHOLD_T1)).toBe("amber");
    expect(calculate_confluence_card_score_tier(CONFLUENCE_LADDER_THRESHOLD_T2 - 1)).toBe("amber");
  });

  it("returns orange from T2 through T3 − 1", () => {
    expect(calculate_confluence_card_score_tier(CONFLUENCE_LADDER_THRESHOLD_T2)).toBe("orange");
    expect(calculate_confluence_card_score_tier(CONFLUENCE_LADDER_THRESHOLD_T3 - 1)).toBe("orange");
  });

  it("returns elite at T3 and above", () => {
    expect(calculate_confluence_card_score_tier(CONFLUENCE_LADDER_THRESHOLD_T3)).toBe("elite");
    expect(calculate_confluence_card_score_tier(220)).toBe("elite");
  });
});

describe("calculate_confluence_card_tier_border_classes", () => {
  it("prioritizes veto over tier", () => {
    expect(
      calculate_confluence_card_tier_border_classes({
        total_score_capped: 200,
        veto_active: true,
      }),
    ).toContain("danger");
    expect(
      calculate_confluence_card_tier_border_classes({
        total_score_capped: 200,
        veto_active: true,
      }),
    ).not.toContain("health-pulse");
  });

  it("adds pulse for elite tier", () => {
    expect(
      calculate_confluence_card_tier_border_classes({
        total_score_capped: CONFLUENCE_LADDER_THRESHOLD_T3,
        veto_active: false,
      }),
    ).toContain("health-pulse");
  });
});
