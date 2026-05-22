import {
  useCallback,
  useEffect,
  useState,
  type ClipboardEvent,
  type FormEvent,
  type KeyboardEvent,
  type ReactElement,
} from "react";

import {
  OMNIBOX_QUERY_MAX_LENGTH,
  sanitize_omnibox_query_input,
} from "../../lib/sanitize-omnibox-query-input";

const ASSET_OPTIONS = [
  "BTC",
  "ETH",
  "SOL",
  "INJ",
  "AVAX",
  "ARB",
  "DOGE",
  "LINK",
] as const;

const ROUTE_OPTIONS = [
  { label: "Auto", value: "" },
  { label: "RAG Only", value: "RAG_ONLY" },
  { label: "Live Data", value: "MCP_ONLY" },
  { label: "Direct", value: "DIRECT" },
] as const;

export interface QueryInputProps {
  value: string;
  on_change: (next: string) => void;
  disabled: boolean;
  disable_reason?: string;
  /** When set, asset context is locked (e.g. asset inspector page). */
  forced_asset?: string;
  on_submit: (payload: {
    query: string;
    asset: string;
    route_override: string | null;
    route_label: string;
  }) => void;
}

export function QueryInput(props: QueryInputProps): ReactElement {
  const {
    value,
    on_change,
    disabled,
    disable_reason,
    forced_asset,
    on_submit,
  } = props;
  const [asset, set_asset] = useState<string>(() => forced_asset ?? "BTC");
  const [route_value, set_route_value] = useState<string>("");

  const effective_asset = forced_asset ?? asset;

  useEffect(() => {
    if (forced_asset !== undefined) {
      set_asset(forced_asset);
    }
  }, [forced_asset]);

  const trimmed_length = value.trim().length;
  const send_disabled = disabled || trimmed_length === 0;

  const handle_submit = useCallback(
    (event?: FormEvent) => {
      event?.preventDefault();
      if (send_disabled) {
        return;
      }
      const cleaned = sanitize_omnibox_query_input(value);
      if (!cleaned.trim()) {
        return;
      }
      const route_label =
        ROUTE_OPTIONS.find((option) => option.value === route_value)?.label ??
        "Auto";
      on_submit({
        query: cleaned,
        asset: effective_asset,
        route_override: route_value === "" ? null : route_value,
        route_label,
      });
      on_change("");
    },
    [effective_asset, on_change, on_submit, route_value, send_disabled, value],
  );

  const handle_key_down = useCallback(
    (event: KeyboardEvent<HTMLTextAreaElement>) => {
      if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
        event.preventDefault();
        handle_submit();
      }
    },
    [handle_submit],
  );

  const handle_paste = useCallback(
    (event: ClipboardEvent<HTMLTextAreaElement>) => {
      event.preventDefault();
      const textarea = event.currentTarget;
      const pasted = event.clipboardData.getData("text");
      const start = textarea.selectionStart;
      const end = textarea.selectionEnd;
      const merged = `${value.slice(0, start)}${pasted}${value.slice(end)}`;
      on_change(sanitize_omnibox_query_input(merged));
    },
    [on_change, value],
  );

  return (
    <form onSubmit={handle_submit} className="space-y-2">
      <div className="flex flex-wrap items-center gap-3 text-xs text-[var(--text-secondary)]">
        <label className="flex items-center gap-2">
          <span>Asset context:</span>
          {forced_asset !== undefined ? (
            <span className="rounded-md border border-[var(--border-hover)] bg-[var(--bg-elevated)] px-2 py-1 font-mono text-[var(--text-primary)]">
              {forced_asset}
            </span>
          ) : (
            <select
              value={asset}
              onChange={(event) => set_asset(event.target.value)}
              disabled={disabled}
              className="rounded-md border border-[var(--border)] bg-[var(--bg-surface)] px-2 py-1 text-[var(--text-primary)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent-cyan)]"
            >
              {ASSET_OPTIONS.map((symbol) => (
                <option key={symbol} value={symbol}>
                  {symbol}
                </option>
              ))}
            </select>
          )}
        </label>
        <label className="flex items-center gap-2">
          <span>Route:</span>
          <select
            value={route_value}
            onChange={(event) => set_route_value(event.target.value)}
            disabled={disabled}
            className="rounded-md border border-[var(--border)] bg-[var(--bg-surface)] px-2 py-1 text-[var(--text-primary)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent-cyan)]"
          >
            {ROUTE_OPTIONS.map((option) => (
              <option key={option.label} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </label>
      </div>

      <div className="flex gap-2">
        <textarea
          value={value}
          onChange={(event) =>
            on_change(sanitize_omnibox_query_input(event.target.value))
          }
          onPaste={handle_paste}
          onKeyDown={handle_key_down}
          disabled={disabled}
          rows={3}
          placeholder="Ask about market conditions, signals, historical patterns…"
          className="min-h-[88px] flex-1 resize-y rounded-md border border-[var(--border)] bg-[var(--bg-surface)] px-3 py-2 text-sm text-[var(--text-primary)] placeholder:text-[var(--text-tertiary)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent-cyan)] disabled:opacity-50"
        />
        <button
          type="submit"
          disabled={send_disabled}
          className="self-end rounded-md bg-[var(--accent-cyan)] px-4 py-2 text-sm font-semibold text-[var(--bg-base)] hover:opacity-90 disabled:opacity-40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent-cyan)] focus-visible:ring-offset-2 focus-visible:ring-offset-[var(--bg-base)]"
        >
          Send
        </button>
      </div>
      <div className="flex flex-wrap justify-between gap-2 text-[11px] text-[var(--text-tertiary)]">
        <span>
          {value.length} / {OMNIBOX_QUERY_MAX_LENGTH} · Cmd+Enter to send
        </span>
        {disable_reason ? (
          <span className="text-[var(--danger)]">{disable_reason}</span>
        ) : null}
      </div>
    </form>
  );
}
