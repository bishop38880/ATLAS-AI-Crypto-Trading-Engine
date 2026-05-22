import { describe, expect, it } from "vitest";

import {
  derive_base_asset_from_pair,
  derive_pair_from_rotation_entry,
  calculate_symbol_keys_deduped,
} from "./dashboard-symbol";

describe("derive_pair_from_rotation_entry", () => {
  it("preserves slash pairs", () => {
    expect(derive_pair_from_rotation_entry("sol/usdt")).toBe("SOL/USDT");
  });

  it("normalises condensed USDT markets", () => {
    expect(derive_pair_from_rotation_entry("BTCUSDT")).toBe("BTC/USDT");
    expect(derive_pair_from_rotation_entry("hype")).toBe("HYPE/USDT");
  });
});

describe("derive_base_asset_from_pair", () => {
  it("handles slash and condensed forms", () => {
    expect(derive_base_asset_from_pair("BTC/USDT")).toBe("BTC");
    expect(derive_base_asset_from_pair("ETHUSDT")).toBe("ETH");
  });
});

describe("calculate_symbol_keys_deduped", () => {
  it("fans out synonyms without duplicates", () => {
    const keys = calculate_symbol_keys_deduped("btc/usdt");
    expect(keys.map((symbol) => symbol.toUpperCase())).toContain("BTC/USDT");
    expect(keys.map((symbol) => symbol.toUpperCase())).toContain("BTCUSDT");
  });
});
