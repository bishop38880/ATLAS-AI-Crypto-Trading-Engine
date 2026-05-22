import type {
  OmniBoxRouteKey,
  OmniBoxSseEvent,
  SourceCitationPayload,
} from "../types/omnibox-api";

export interface SseLineBuffer {
  value: string;
}

function normalize_classifier_scalar(value: unknown): string {
  if (typeof value !== "string") {
    return "";
  }
  return value.trim().toLowerCase().replace(/\s+/g, "_");
}

function is_known_route(route: unknown): route is OmniBoxRouteKey {
  return (
    route === "RAG_ONLY" ||
    route === "MCP_ONLY" ||
    route === "HYBRID" ||
    route === "DIRECT"
  );
}

/**
 * Alternate classifier payloads (pre-router or sandbox backends) omit `route` and emit
 * `intent` plus optional provider hints instead.
 */
function infer_route_from_alternate_classifier(
  data: Record<string, unknown>,
): OmniBoxRouteKey {
  const provider = normalize_classifier_scalar(data.requiredProvider);
  const provider_fallback = normalize_classifier_scalar(data.required_provider);
  const combined_provider = `${provider} ${provider_fallback}`.trim();

  const intent =
    normalize_classifier_scalar(typeof data.intent === "string" ? data.intent : "");

  if (
    intent.includes("rag") ||
    intent.includes("technical") ||
    intent.includes("pattern") ||
    combined_provider.includes("memory") ||
    combined_provider.includes("retrieval") ||
    combined_provider.includes("qdrant") ||
    combined_provider.includes("lance") ||
    combined_provider.includes("embed")
  ) {
    return "RAG_ONLY";
  }

  if (
    intent.includes("live") ||
    intent.includes("funding") ||
    intent.includes("liquidation") ||
    intent.includes("real_time") ||
    combined_provider.includes("coinalyze") ||
    combined_provider.includes("hydra") ||
    combined_provider.includes("pyth") ||
    combined_provider.includes("okx") ||
    combined_provider.includes("hyperliquid")
  ) {
    return "MCP_ONLY";
  }

  return "HYBRID";
}

function coerce_classification_tuple(
  data: Record<string, unknown>,
): { route: OmniBoxRouteKey; confidence: number; rationale?: string } | null {
  const confidence = data.confidence;
  if (typeof confidence !== "number") {
    return null;
  }

  let route: OmniBoxRouteKey | null = is_known_route(data.route)
    ? data.route
    : null;

  if (route === null && typeof data.intent === "string") {
    route = infer_route_from_alternate_classifier(data);
  }

  if (route === null) {
    return null;
  }

  let rationale =
    typeof data.rationale === "string"
      ? data.rationale.trim()
      : undefined;

  if (
    rationale === undefined &&
    (typeof data.intent === "string" ||
      typeof data.requiredProvider === "string" ||
      typeof data.required_provider === "string")
  ) {
    const pieces: string[] = [];
    if (typeof data.intent === "string") {
      pieces.push(`intent=${data.intent}`);
    }
    if (typeof data.requiredProvider === "string") {
      pieces.push(`requiredProvider=${data.requiredProvider}`);
    } else if (typeof data.required_provider === "string") {
      pieces.push(`requiredProvider=${data.required_provider}`);
    }
    rationale = pieces.join(" · ");
  }

  return { route, confidence, rationale };
}

function narrow_source_citation(raw: unknown): SourceCitationPayload | null {
  if (!raw || typeof raw !== "object") {
    return null;
  }
  const o = raw as Record<string, unknown>;
  if (typeof o.id !== "string" || typeof o.title !== "string") {
    return null;
  }
  return raw as SourceCitationPayload;
}

export function narrow_omnibox_sse_event(raw: unknown): OmniBoxSseEvent | null {
  if (!raw || typeof raw !== "object") {
    return null;
  }
  const record = raw as Record<string, unknown>;
  const eventType = record.type;

  if (eventType === "token" && typeof record.data === "string") {
    return { type: "token", data: record.data };
  }

  if (eventType === "classification" && record.data && typeof record.data === "object") {
    const d = record.data as Record<string, unknown>;
    const coerced = coerce_classification_tuple(d);
    if (coerced !== null) {
      return {
        type: "classification",
        data: coerced,
      };
    }
  }

  if (eventType === "sources" && Array.isArray(record.data)) {
    const citations = record.data
      .map((item) => narrow_source_citation(item))
      .filter((x): x is SourceCitationPayload => x !== null);
    return { type: "sources", data: citations };
  }

  if (eventType === "done" && record.data && typeof record.data === "object") {
    const d = record.data as Record<string, unknown>;
    const latencyMs = d.latencyMs;
    const llmTier = d.llmTier;
    if (typeof latencyMs === "number" && typeof llmTier === "string") {
      return {
        type: "done",
        data: {
          latencyMs,
          llmTier,
          liveDataSummary:
            typeof d.liveDataSummary === "string"
              ? d.liveDataSummary
              : d.liveDataSummary === null
                ? null
                : undefined,
        },
      };
    }
  }

  return null;
}

export function extract_omnibox_sse_events(
  chunk_text: string,
  buffer: SseLineBuffer,
): OmniBoxSseEvent[] {
  buffer.value += chunk_text;
  const events: OmniBoxSseEvent[] = [];
  while (true) {
    const boundary = buffer.value.indexOf("\n\n");
    if (boundary === -1) {
      break;
    }
    const raw_block = buffer.value.slice(0, boundary);
    buffer.value = buffer.value.slice(boundary + 2);
    for (const line of raw_block.split("\n")) {
      if (!line.startsWith("data: ")) {
        continue;
      }
      const json_text = line.slice(6).trim();
      if (!json_text) {
        continue;
      }
      try {
        const parsed: unknown = JSON.parse(json_text);
        const narrowed = narrow_omnibox_sse_event(parsed);
        if (narrowed) {
          events.push(narrowed);
        }
      } catch {
        /* malformed SSE frame — skip */
      }
    }
  }
  return events;
}
