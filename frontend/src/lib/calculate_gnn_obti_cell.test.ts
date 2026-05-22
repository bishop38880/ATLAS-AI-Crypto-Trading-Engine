import { describe, expect, it } from "vitest";

import { calculate_gnn_obti_glyph_cell } from "./calculate_gnn_obti_cell";

describe("calculate_gnn_obti_glyph_cell", () => {
  it("returns balanced dash when summary missing", () => {
    const cell = calculate_gnn_obti_glyph_cell(undefined, undefined, "DOGE");
    expect(cell.visual).toBe("─");
    expect(cell.ariaLabel).toBeUndefined();
  });

  it("maps moderate with bid-side aria copy", () => {
    const cell = calculate_gnn_obti_glyph_cell("moderate", "bid", "DOGE");
    expect(cell.visual).toBe("◐");
    expect(cell.ariaLabel).toBe("Asset DOGE: OBTI moderate, bid-side");
  });

  it("maps extreme with ask-side aria copy", () => {
    const cell = calculate_gnn_obti_glyph_cell("extreme", "ask", "SOL");
    expect(cell.visual).toBe("●");
    expect(cell.ariaLabel).toBe("Asset SOL: OBTI extreme, ask-side");
  });
});
