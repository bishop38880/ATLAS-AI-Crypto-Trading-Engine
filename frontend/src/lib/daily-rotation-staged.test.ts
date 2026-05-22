import { describe, expect, it } from "vitest";

import { try_append_daily_rotation_staged } from "./daily-rotation-staged";

describe("try_append_daily_rotation_staged", () => {
  it("allows first unique asset", () => {
    const next = try_append_daily_rotation_staged([], "BTC/USDT");
    expect(next).toEqual(["BTC/USDT"]);
  });

  it("rejects duplicates", () => {
    expect(try_append_daily_rotation_staged(["BTC/USDT"], "BTC/USDT")).toBeNull();
  });

  it("rejecting duplicate leaves length unchanged", () => {
    const base = ["BTC/USDT"] as string[];
    const duplicate = try_append_daily_rotation_staged(base, "BTC/USDT");
    expect(duplicate).toBeNull();
    expect(base.length).toBe(1);
  });

  it("caps at four assets", () => {
    const full = ["A", "B", "C", "D"] as string[];
    expect(try_append_daily_rotation_staged(full, "E")).toBeNull();
  });
});
