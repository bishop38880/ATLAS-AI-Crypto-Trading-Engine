import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactElement,
} from "react";

import { calculate_omnibox_minute_limit_hit } from "../../lib/calculate-omnibox-budget-meter-style";
import { apiUrl } from "../../lib/url";
import { stream_omnibox_query } from "../../lib/stream-omnibox-query";
import type { OmniBoxBudgetPayload } from "../../types/omnibox-api";
import type {
  OmniBoxAssistantMessage,
  OmniBoxMessage,
} from "../../store/omnibox-store";
import { ConversationHistory } from "../omnibox/ConversationHistory";
import { QueryBudgetMeter } from "../omnibox/QueryBudgetMeter";
import { QueryInput } from "../omnibox/QueryInput";

async function fetch_omnibox_budget(): Promise<OmniBoxBudgetPayload> {
  const response = await fetch(apiUrl("/api/omnibox/budget"), {
    credentials: "omit",
    cache: "no-store",
  });
  if (!response.ok) {
    throw new Error(`budget_${response.status}`);
  }
  return response.json() as Promise<OmniBoxBudgetPayload>;
}

function storage_key_for_asset(asset: string): string {
  return `polaris:omnibox:asset:${asset.trim().toUpperCase()}:v1`;
}

function load_messages(asset: string): OmniBoxMessage[] {
  try {
    const raw = localStorage.getItem(storage_key_for_asset(asset));
    if (raw === null || raw.trim().length === 0) {
      return [];
    }
    const parsed = JSON.parse(raw) as unknown;
    if (!Array.isArray(parsed)) {
      return [];
    }
    return parsed as OmniBoxMessage[];
  } catch {
    return [];
  }
}

function save_messages(asset: string, messages: OmniBoxMessage[]): void {
  try {
    localStorage.setItem(storage_key_for_asset(asset), JSON.stringify(messages));
  } catch {
    /* quota or private mode */
  }
}

export interface AssetAskAtlasSectionProps {
  /** Base symbol, e.g. ``SOL`` (matches omnibox / Redis wire style). */
  asset: string;
}

export function AssetAskAtlasSection(props: AssetAskAtlasSectionProps): ReactElement {
  const { asset } = props;
  const asset_upper = asset.trim().toUpperCase();
  const query_client = useQueryClient();
  const abort_ref = useRef<AbortController | null>(null);
  const transcript_ref = useRef<HTMLDivElement | null>(null);

  const [messages, set_messages] = useState<OmniBoxMessage[]>(() => load_messages(asset_upper));

  useEffect(() => {
    set_messages(load_messages(asset_upper));
  }, [asset_upper]);

  useEffect(() => {
    save_messages(asset_upper, messages);
  }, [asset_upper, messages]);

  const append_user_message = useCallback(
    (body: string, ctx_asset: string, route_label: string) => {
      set_messages((prev) => [
        ...prev,
        {
          id: crypto.randomUUID(),
          role: "user",
          body,
          asset: ctx_asset,
          routeLabel: route_label,
          ts: new Date().toISOString(),
        },
      ]);
    },
    [],
  );

  const append_assistant_shell = useCallback(() => {
    const id = crypto.randomUUID();
    set_messages((prev) => [
      ...prev,
      {
        id,
        role: "assistant",
        body: "",
        ts: new Date().toISOString(),
        streaming: true,
        ariaLive: "off",
        sources: [],
      },
    ]);
    return id;
  }, []);

  const patch_assistant_message = useCallback(
    (id: string, patch: Partial<Omit<OmniBoxAssistantMessage, "id" | "role">>) => {
      set_messages((prev) =>
        prev.map((message) => {
          if (message.id !== id || message.role !== "assistant") {
            return message;
          }
          return { ...message, ...patch };
        }),
      );
    },
    [],
  );

  const clear_thread = useCallback(() => {
    set_messages([]);
    try {
      localStorage.removeItem(storage_key_for_asset(asset_upper));
    } catch {
      /* ignore */
    }
  }, [asset_upper]);

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
    <section className="space-y-3 rounded-[var(--radius-lg)] border border-[var(--border)] bg-[var(--bg-surface)] p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-[11px] font-black uppercase tracking-wide text-[var(--text-tertiary)]">
            Ask ATLAS — this asset
          </h2>
          <p className="mt-1 text-xs text-[var(--text-secondary)]">
            Same OmniBox stack as the full page; thread is saved in this browser per symbol (
            <span className="font-mono">{asset_upper}</span>).
          </p>
        </div>
        <button
          type="button"
          className="rounded-[var(--radius-sm)] border border-[var(--border-hover)] px-2 py-1 text-[10px] font-bold uppercase tracking-wide text-[var(--text-secondary)] hover:border-[var(--accent-cyan)] hover:text-[var(--accent-cyan)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent-cyan)]"
          onClick={() => clear_thread()}
        >
          Clear thread
        </button>
      </div>

      <div
        ref={transcript_ref}
        className="max-h-[320px] min-h-[160px] overflow-y-auto rounded-[var(--radius-md)] border border-[var(--border-hover)] bg-[var(--bg-elevated)]/40 p-3"
      >
        {messages.length === 0 ? (
          <p className="text-sm text-[var(--text-tertiary)]">
            Ask about {asset_upper} confluence, memory, or post-trade context.
          </p>
        ) : (
          <ConversationHistory messages={messages} />
        )}
      </div>

      <QueryBudgetMeter budget={budget_query.data} load_error={budget_query.isError} />
      <QueryInput
        value={draft}
        on_change={set_draft}
        disabled={input_disabled}
        disable_reason={disable_reason}
        forced_asset={asset_upper}
        on_submit={handle_submit}
      />
    </section>
  );
}
