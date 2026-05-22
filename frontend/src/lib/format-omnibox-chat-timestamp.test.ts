import { describe, expect, it } from "vitest";

import { format_omnibox_chat_timestamp } from "./format-omnibox-chat-timestamp";

describe("format_omnibox_chat_timestamp", () => {
  it("formats ISO timestamps without throwing", () => {
    const formatted = format_omnibox_chat_timestamp("2026-02-14T03:00:00.000Z");
    expect(formatted.length).toBeGreaterThan(4);
  });

  it("falls back on invalid input", () => {
    expect(format_omnibox_chat_timestamp("not-a-date")).toBe("not-a-date");
  });
});
