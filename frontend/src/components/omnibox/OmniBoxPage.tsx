import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactElement,
} from "react";

import {
  calculate_omnibox_minute_limit_hit,
} from "../../lib/calculate-omnibox-budget-meter-style";
import { apiUrl } from "../../lib/url";
import { sanitize_omnibox_query_input } from "../../lib/sanitize-omnibox-query-input";
import { stream_omnibox_query } from "../../lib/stream-omnibox-query";
import { useOmniBoxStore } from "../../store/omnibox-store";
import type { OmniBoxBudgetPayload } from "../../types/omnibox-api";
import { ConversationHistory } from "./ConversationHistory";
import { QueryBudgetMeter } from "./QueryBudgetMeter";
import { QueryInput } from "./QueryInput";
import { SuggestedQueries } from "./SuggestedQueries";

async function fetch_omnibox_budget(): Promise<OmniBoxBudgetPayload> {
  const response = await fetch(apiUrl("/api/omnibox/budget"), {
    // Omit credentials so `Access-Control-Allow-Origin: *` can apply when the SPA
    // calls a separately-hosted API origin in dev/production.
    credentials: "omit",
    cache: "no-store",
  });
  if (!response.ok) {
    throw new Error(`budget_${response.status}`);
  }
  return response.json() as Promise<OmniBoxBudgetPayload>;
}

export function OmniBoxPage(): ReactElement {
  const query_client = useQueryClient();
  const abort_ref = useRef<AbortController | null>(null);
  const transcript_ref = useRef<HTMLDivElement | null>(null);

  const messages = useOmniBoxStore((state) => state.messages);
  const append_user_message = useOmniBoxStore((state) => state.appendUserMessage);
  const append_assistant_shell = useOmniBoxStore(
    (state) => state.appendAssistantShell,
  );
  const patch_assistant_message = useOmniBoxStore(
    (state) => state.patchAssistantMessage,
  );
  const clear_messages = useOmniBoxStore((state) => state.clearMessages);

  const [draft, set_draft] = useState("");
  const [submitting, set_submitting] = useState(false);

  const budget_query = useQuery({
    queryKey: ["omnibox", "budget"],
    queryFn: fetch_omnibox_budget,
    staleTime: 15_000,
  });

  useEffect(() => {
    return () => {
      abort_ref.current?.abort();
    };
  }, []);

  useEffect(() => {
    const node = transcript_ref.current;
    if (!node) {
      return;
    }
    node.scrollTop = node.scrollHeight;
  }, [messages]);

  const budget = budget_query.data;

  const token_budget_exhausted = useMemo(() => {
    if (!budget) {
      return false;
    }
    return budget.sessionTokensUsed >= budget.sessionTokenLimit;
  }, [budget]);

  const minute_wall_hit = useMemo(() => {
    if (!budget) {
      return false;
    }
    return calculate_omnibox_minute_limit_hit(
      budget.queriesThisMinute,
      budget.queriesPerMinuteLimit,
    );
  }, [budget]);

  const disable_reason = useMemo(() => {
    if (budget?.dailyCapReached) {
      const hours = budget.costCapResetHours ?? "—";
      return `Daily cost cap reached. Reset in ${hours}h.`;
    }
    if (token_budget_exhausted) {
      return "Session token budget exhausted.";
    }
    if (minute_wall_hit) {
      return "Per-minute query rate limit reached.";
    }
    return undefined;
  }, [budget, minute_wall_hit, token_budget_exhausted]);

  const input_disabled =
    submitting ||
    Boolean(budget?.dailyCapReached) ||
    token_budget_exhausted ||
    minute_wall_hit;

  const handle_submit = useCallback(
    async (payload: {
      query: string;
      asset: string;
      route_override: string | null;
      route_label: string;
    }) => {
      abort_ref.current?.abort();
      abort_ref.current = new AbortController();
      const signal = abort_ref.current.signal;

      append_user_message(payload.query, payload.asset, payload.route_label);
      const assistant_id = append_assistant_shell();

      set_submitting(true);
      let aggregate = "";

      try {
        await stream_omnibox_query(
          {
            query: payload.query,
            asset: payload.asset,
            routeOverride: payload.route_override,
          },
          (event) => {
            if (signal.aborted) {
              return;
            }
            switch (event.type) {
              case "classification":
                patch_assistant_message(assistant_id, {
                  route: event.data.route,
                  classificationConfidence: event.data.confidence,
                  classificationRationale: event.data.rationale,
                });
                break;
              case "token":
                aggregate += event.data;
                patch_assistant_message(assistant_id, {
                  body: aggregate,
                });
                break;
              case "sources":
                patch_assistant_message(assistant_id, {
                  sources: event.data,
                });
                break;
              case "done":
                patch_assistant_message(assistant_id, {
                  streaming: false,
                  ariaLive: "polite",
                  latencyMs: event.data.latencyMs,
                  llmTier: event.data.llmTier,
                  liveDataSummary: event.data.liveDataSummary ?? null,
                });
                break;
              default:
                break;
            }
          },
          signal,
        );
      } catch {
        if (!signal.aborted) {
          patch_assistant_message(assistant_id, {
            body:
              aggregate ||
              "Unable to reach OmniBox. Confirm API ingress or retry shortly.",
            streaming: false,
            ariaLive: "polite",
          });
        }
      } finally {
        set_submitting(false);
        void query_client.invalidateQueries({ queryKey: ["omnibox", "budget"] });
      }
    },
    [
      append_assistant_shell,
      append_user_message,
      patch_assistant_message,
      query_client,
    ],
  );

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-3 px-4 py-3">
      <header className="flex shrink-0 flex-wrap items-center justify-between gap-3 border-b border-[var(--border)] pb-3">
        <div>
          <h1 className="display text-lg font-semibold tracking-tight">
            POLARIS OmniBox
          </h1>
          <p className="text-xs text-[var(--text-tertiary)]">
            Natural-language intelligence surface — citations grounded to POLARIS memory.
          </p>
        </div>
        <details className="relative">
          <summary className="cursor-pointer list-none rounded-md border border-[var(--border)] px-3 py-1 text-xs font-semibold text-[var(--text-secondary)] hover:border-[var(--border-hover)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent-cyan)] [&::-webkit-details-marker]:hidden">
            History ▼
          </summary>
          <div className="absolute right-0 z-10 mt-1 min-w-[180px] rounded-md border border-[var(--border)] bg-[var(--bg-elevated)] p-2 shadow-lg">
            <button
              type="button"
              className="w-full rounded-md px-2 py-1 text-left text-xs text-[var(--text-primary)] hover:bg-[var(--bg-overlay)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent-cyan)]"
              onClick={() => clear_messages()}
            >
              Clear conversation
            </button>
          </div>
        </details>
      </header>

      <div
        ref={transcript_ref}
        className="flex min-h-[240px] flex-1 flex-col overflow-hidden"
      >
        {messages.length === 0 ? (
          <SuggestedQueries
            on_select={(text) =>
              set_draft(sanitize_omnibox_query_input(text))
            }
          />
        ) : (
          <ConversationHistory messages={messages} />
        )}
      </div>

      <div className="shrink-0 space-y-2 border-t border-[var(--border)] pt-3">
        <QueryBudgetMeter
          budget={budget_query.data}
          load_error={budget_query.isError}
        />
        <QueryInput
          value={draft}
          on_change={set_draft}
          disabled={input_disabled}
          disable_reason={disable_reason}
          on_submit={handle_submit}
        />
      </div>
    </div>
  );
}
