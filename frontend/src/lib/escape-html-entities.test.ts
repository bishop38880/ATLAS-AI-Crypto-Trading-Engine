import { describe, expect, it } from "vitest";

import { escape_html_entities } from "./escape-html-entities";

describe("escape_html_entities", () => {
  it("escapes HTML metacharacters", () => {
    expect(escape_html_entities(`<&>"`)).toBe("&lt;&amp;&gt;&quot;");
  });
});
