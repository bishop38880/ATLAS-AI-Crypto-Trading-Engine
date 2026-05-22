import type { ReactElement } from "react";

import { format_omnibox_chat_timestamp } from "../../lib/format-omnibox-chat-timestamp";
import type { OmniBoxMessage } from "../../store/omnibox-store";
import { LLMResponseBubble } from "./LLMResponseBubble";

export interface ConversationHistoryProps {
  messages: OmniBoxMessage[];
}

export function ConversationHistory(props: ConversationHistoryProps): ReactElement {
  const { messages } = props;

  return (
    <div
      className="min-h-0 flex-1 space-y-4 overflow-y-auto px-1 pb-4 pt-2"
      aria-live="off"
    >
      {messages.map((message) => {
        if (message.role === "user") {
          return (
            <div key={message.id} className="flex justify-end">
              <article className="max-w-[min(720px,92%)] rounded-lg bg-[var(--bg-overlay)] px-4 py-2 text-sm text-[var(--text-primary)]">
                <div className="mb-1 text-[10px] uppercase tracking-wide text-[var(--text-tertiary)]">
                  {message.asset} · {message.routeLabel}
                </div>
                <p className="whitespace-pre-wrap">{message.body}</p>
                <time
                  className="mt-1 block text-[10px] text-[var(--text-tertiary)]"
                  dateTime={message.ts}
                >
                  {format_omnibox_chat_timestamp(message.ts)}
                </time>
              </article>
            </div>
          );
        }

        return (
          <div key={message.id} className="flex justify-start">
            <LLMResponseBubble message={message} />
          </div>
        );
      })}
    </div>
  );
}
