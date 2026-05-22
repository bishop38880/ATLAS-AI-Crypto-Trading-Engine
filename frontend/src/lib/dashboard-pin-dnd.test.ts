import { describe, expect, it } from "vitest";

import {
  DASHBOARD_PIN_DRAG_MIME_TYPE,
  read_dashboard_pin_drop,
  serialize_dashboard_pin_for_drag,
} from "./dashboard-pin-dnd";

function create_stub_data_transfer(mime_bundle: Record<string, string>): DataTransfer {
  return {
    getData: (mime_key: string) => mime_bundle[mime_key] ?? "",
  } as unknown as DataTransfer;
}

describe("dashboard_pin_dnd_helpers", () => {
  it("serialize_dashboard_pin_for_drag trims pair strings", () => {
    expect(serialize_dashboard_pin_for_drag("  ETH/USDT  ")).toBe("ETH/USDT");
  });

  it("read_dashboard_pin_drop prefers pinned MIME bundle over text/plain fallback", () => {
    const fixture = create_stub_data_transfer({
      [DASHBOARD_PIN_DRAG_MIME_TYPE]: " BTC/USDT ",
      "text/plain": "noise",
    });
    expect(read_dashboard_pin_drop(fixture)).toBe("BTC/USDT");
  });

  it("read_dashboard_pin_drop consumes text/plain when custom mime empty whitespace", () => {
    const fixture = create_stub_data_transfer({
      [DASHBOARD_PIN_DRAG_MIME_TYPE]: "   ",
      "text/plain": " SOL/USDT",
    });
    expect(read_dashboard_pin_drop(fixture)).toBe("SOL/USDT");
  });

  it("read_dashboard_pin_drop returns null without usable payload fragments", () => {
    const fixture = create_stub_data_transfer({});
    expect(read_dashboard_pin_drop(fixture)).toBeNull();
  });
});
