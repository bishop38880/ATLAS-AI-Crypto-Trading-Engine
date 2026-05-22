import { describe, expect, it } from "vitest";

import {
  coerce_dashboard_position_row,
  calculate_dashboard_position_slots_from_payload,
} from "./dashboard-positions";

describe("coerce_dashboard_position_row", () => {
  it("maps snake_case fields", () => {
    const slot = coerce_dashboard_position_row({
      slot_index: 2,
      asset: "BTC",
      direction: "long",
      unrealized_pnl_percent: "+1.23%",
    });
    expect(slot).toEqual({
      slot_index: 2,
      asset: "BTC",
      direction: "LONG",
      unrealized_pnl_percent: "+1.23%",
    });
  });

  it("accepts camelCase aliases", () => {
    const slot = coerce_dashboard_position_row({
      slotIndex: 1,
      symbol: "SOL",
      side: "SHORT",
      unrealizedPnlPercent: "-0.5%",
    });
    expect(slot?.slot_index).toBe(1);
    expect(slot?.asset).toBe("SOL");
    expect(slot?.direction).toBe("SHORT");
  });

  it("returns null when slot_index missing", () => {
    expect(coerce_dashboard_position_row({ asset: "ETH" })).toBeNull();
  });
});

describe("calculate_dashboard_position_slots_from_payload", () => {
  it("pads to six contiguous slots", () => {
    const rows = calculate_dashboard_position_slots_from_payload([]);
    expect(rows).toHaveLength(6);
    expect(rows.every((slot) => slot.slot_index >= 1 && slot.slot_index <= 6)).toBe(true);
    expect(rows[0].slot_index).toBe(1);
    expect(rows[5].slot_index).toBe(6);
  });

  it("merges partial API payload", () => {
    const rows = calculate_dashboard_position_slots_from_payload([
      { slot_index: 3, asset: "BTC", direction: "LONG", unrealized_pnl_percent: "+2%" },
    ]);
    const active = rows.find((slot) => slot.slot_index === 3);
    expect(active?.asset).toBe("BTC");
    expect(rows.find((slot) => slot.slot_index === 1)?.asset).toBeNull();
  });
});
