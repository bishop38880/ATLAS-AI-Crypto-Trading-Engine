import { describe, expect, it } from "vitest";

import {
  extract_omnibox_sse_events,
  narrow_omnibox_sse_event,
  type SseLineBuffer,
} from "./extract-omnibox-sse-events";

describe("narrow_omnibox_sse_event", () => {
  it("accepts classification frames", () => {
    expect(
      narrow_omnibox_sse_event({
        type: "classification",
        data: { route: "HYBRID", confidence: 0.9, rationale: "x" },
      }),
    ).toEqual({
      type: "classification",
      data: { route: "HYBRID", confidence: 0.9, rationale: "x" },
    });
  });

  it("rejects malformed classification", () => {
    expect(
      narrow_omnibox_sse_event({
        type: "classification",
        data: { route: "OTHER", confidence: 1 },
      }),
    ).toBeNull();
  });

  it("maps intent-only classifier payloads to a route", () => {
    expect(
      narrow_omnibox_sse_event({
        type: "classification",
        data: {
          intent: "market_summary",
          confidence: 0.99,
          requiredProvider: "none",
        },
      }),
    ).toEqual({
      type: "classification",
      data: {
        route: "HYBRID",
        confidence: 0.99,
        rationale: "intent=market_summary · requiredProvider=none",
      },
    });
  });
});

describe("extract_omnibox_sse_events", () => {
  it("parses buffered SSE blocks", () => {
    const buf: SseLineBuffer = { value: "" };
    const frame =
      'data: {"type":"token","data":"hi"}\n\n' +
      'data: {"type":"done","data":{"latencyMs":1,"llmTier":"DeepSeek V3 (FAST)"}}\n\n';
    const events = extract_omnibox_sse_events(frame, buf);
    expect(events.map((e) => e.type)).toEqual(["token", "done"]);
    expect(buf.value).toBe("");
  });
});
