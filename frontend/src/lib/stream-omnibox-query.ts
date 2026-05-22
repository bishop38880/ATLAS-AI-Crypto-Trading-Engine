import type { OmniBoxSseEvent } from "../types/omnibox-api";
import {
  extract_omnibox_sse_events,
  type SseLineBuffer,
} from "./extract-omnibox-sse-events";
import { apiUrl } from "./url";

export interface OmniBoxQueryRequestBody {
  query: string;
  asset: string;
  routeOverride?: string | null;
}

export async function stream_omnibox_query(
  body: OmniBoxQueryRequestBody,
  on_event: (event: OmniBoxSseEvent) => void,
  signal: AbortSignal,
): Promise<void> {
  const payload: Record<string, string> = {
    query: body.query,
    asset: body.asset,
  };
  if (body.routeOverride) {
    payload.routeOverride = body.routeOverride;
  }

  const response = await fetch(apiUrl("/api/omnibox/query"), {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Accept: "text/event-stream",
    },
    credentials: "omit",
    cache: "no-store",
    body: JSON.stringify(payload),
    signal,
  });

  if (!response.ok || !response.body) {
    throw new Error(`omnibox_query_http_${response.status}`);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  const buffer: SseLineBuffer = { value: "" };

  while (true) {
    const { done, value } = await reader.read();
    if (done) {
      break;
    }
    const chunk_text = decoder.decode(value, { stream: true });
    const events = extract_omnibox_sse_events(chunk_text, buffer);
    for (const event of events) {
      on_event(event);
    }
  }
  const final_chunk = decoder.decode();
  if (final_chunk.length > 0) {
    const tail_events = extract_omnibox_sse_events(final_chunk, buffer);
    for (const event of tail_events) {
      on_event(event);
    }
  }
}
