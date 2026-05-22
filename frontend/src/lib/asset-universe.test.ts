import { describe, expect, it } from "vitest";

import { calculate_asset_universe_view_model } from "./asset-universe";

describe("calculate_asset_universe_view_model", () => {
  it("maps full, active, and daily rotation lists into coverage rows", () => {
    const view_model = calculate_asset_universe_view_model({
      full_universe: ["BTCUSDT", "ETHUSDT", "SOLUSDT"],
      active_33: ["BTCUSDT", "ETHUSDT"],
      daily_8: ["ETHUSDT"],
      fetched_at: "2026-05-15T06:00:00Z",
      is_stale: false,
    });

    expect(view_model.rows).toHaveLength(3);
    expect(view_model.rows.map((row) => row.coverage)).toEqual(["active_33", "daily_8", "universe"]);
    expect(view_model.active_count).toBe(2);
    expect(view_model.daily_count).toBe(1);
    expect(view_model.is_stale).toBe(false);
  });

  it("falls back to the canonical dashboard ladder when Redis sets are empty", () => {
    const view_model = calculate_asset_universe_view_model({
      full_universe: [],
      active_33: [],
      daily_8: [],
      is_stale: true,
    });

    expect(view_model.rows).toHaveLength(33);
    expect(view_model.rows[0]).toMatchObject({
      base: "BTC",
      pair: "BTC/USDT",
      coverage: "fallback",
    });
    expect(view_model.used_fallback).toBe(true);
    expect(view_model.is_stale).toBe(true);
  });
});
