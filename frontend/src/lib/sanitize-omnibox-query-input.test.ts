import { describe, expect, it } from "vitest";

import {
  OMNIBOX_QUERY_MAX_LENGTH,
  sanitize_omnibox_query_input,
} from "./sanitize-omnibox-query-input";

describe("sanitize_omnibox_query_input", () => {
  it("removes zero-width characters", () => {
    const injected = `hello\u200Bworld\uFEFF`;
    expect(sanitize_omnibox_query_input(injected)).toBe("helloworld");
  });

  it("removes ascii control characters except newline and tab", () => {
    expect(sanitize_omnibox_query_input("a\u0001b")).toBe("ab");
  });

  it("preserves newlines within the cap", () => {
    expect(sanitize_omnibox_query_input("line1\nline2")).toBe("line1\nline2");
  });

  it("preserves tab characters", () => {
    expect(sanitize_omnibox_query_input("a\tb")).toBe("a\tb");
  });

  it("truncates to max length", () => {
    const long = "x".repeat(OMNIBOX_QUERY_MAX_LENGTH + 80);
    expect(sanitize_omnibox_query_input(long).length).toBe(
      OMNIBOX_QUERY_MAX_LENGTH,
    );
  });
});
