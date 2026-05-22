import { useCallback, type ReactElement } from "react";

import { format_omnibox_chat_timestamp } from "../../lib/format-omnibox-chat-timestamp";
import type { OmniBoxAssistantMessage } from "../../store/omnibox-store";
import { Badge } from "../ui/Badge";
import { OmniboxMarkdownBody } from "./OmniboxMarkdownBody";
import { RouteClassificationBadge } from "./RouteClassificationBadge";
import { SourceCitationCard } from "./SourceCitationCard";

export interface LLMResponseBubbleProps {
  message: OmniBoxAssistantMessage;
}

export function LLMResponseBubble(props: LLMResponseBubbleProps): ReactElement {
  const { message } = props;

  const handle_copy = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(message.body);
    } catch {
      /* clipboard may be unavailable */
    }
  }, [message.body]);

  const latency_label =
    typeof message.latencyMs === "number"
      ? `Responded in ${(message.latencyMs / 1000).toFixed(1)}s`
      : null;

  const tier_label = message.llmTier ?? null;

  return (
    <article className="max-w-[min(920px,100%)] rounded-lg border border-[var(--border)] bg-[var(--bg-elevated)] px-4 py-3">
      <div className="mb-2 flex flex-wrap items-center gap-2">
        {tier_label ? (
          <Badge variant="live">{tier_label}</Badge>
        ) : null}
        {latency_label ? (
          <span className="text-xs text-[var(--text-tertiary)]">{latency_label}</span>
        ) : null}
      </div>

      <RouteClassificationBadge
        pending={message.streaming && !message.route}
        route={message.route}
        confidence={message.classificationConfidence}
        rationale={message.classificationRationale}
      />

      <div
        className="mt-3"
        aria-live={message.ariaLive}
        {...(message.streaming ? { "aria-busy": true as const } : {})}
      >
        {message.body ? (
          <OmniboxMarkdownBody text={message.body} />
        ) : message.streaming ? (
          <p className="text-sm text-[var(--text-tertiary)]">Thinking…</p>
        ) : null}
      </div>

      {message.liveDataSummary ? (
        <div className="mt-3 rounded-md border border-[var(--border)] bg-[var(--bg-surface)] px-3 py-2 text-xs text-[var(--text-secondary)]">
          <span className="font-semibold text-[var(--text-primary)]">
            Live data snapshot:{" "}
          </span>
          {message.liveDataSummary}
        </div>
      ) : null}

      {message.sources.length > 0 ? (
        <div className="mt-4 space-y-2">
          <h4 className="text-xs font-semibold uppercase tracking-wide text-[var(--text-tertiary)]">
            Sources
          </h4>
          {message.sources.map((citation) => (
            <SourceCitationCard key={citation.id} citation={citation} />
          ))}
        </div>
      ) : null}

      <div className="mt-3 flex flex-wrap items-center justify-between gap-2">
        <time
          className="text-[10px] text-[var(--text-tertiary)]"
          dateTime={message.ts}
        >
          {format_omnibox_chat_timestamp(message.ts)}
        </time>
        <button
          type="button"
          onClick={handle_copy}
          disabled={!message.body}
          aria-label="Copy response"
          className="rounded-md border border-[var(--border)] px-2 py-1 text-xs text-[var(--text-secondary)] hover:border-[var(--border-hover)] hover:text-[var(--text-primary)] disabled:opacity-40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent-cyan)]"
        >
          Copy
        </button>
      </div>
    </article>
  );
}
