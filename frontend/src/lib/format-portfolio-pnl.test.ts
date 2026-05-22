import { describe, expect, it } from "vitest";

import { format_portfolio_pnl_bias_display } from "./format-portfolio-pnl";

describe("format_portfolio_pnl_bias_display", () => {
  it("expands fractional inputs into percent strings", () => {
    const result = format_portfolio_pnl_bias_display("0.023");
    expect(result.text.startsWith("+")).toBe(true);
    expect(result.text.endsWith("%")).toBe(true);
    expect(result.is_positive_bias).toBe(true);
  });

  it("honours literal percent payloads", () => {
    expect(format_portfolio_pnl_bias_display("-3.8%").is_positive_bias).toBe(false);
  });
});
