import { describe, expect, it } from "vitest";

import { derive_signal_route_slug } from "./dashboard-route-slug";

describe("derive_signal_route_slug", () => {
  it("truncates condensed markets to uppercase bases", () => {
    expect(derive_signal_route_slug("btcusdt")).toBe("BTC");
  });

  it("parses slash pairs", () => {
    expect(derive_signal_route_slug("sol/usdt")).toBe("SOL");
  });
});
