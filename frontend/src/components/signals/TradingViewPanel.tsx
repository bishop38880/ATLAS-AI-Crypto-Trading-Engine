import { useEffect, useMemo, type ReactElement } from "react";

declare global {
  interface Window {
    TradingView?: {
      widget: new (options: Record<string, unknown>) => unknown;
    };
  }
}

const TRADING_VIEW_SCRIPT_SRC = "https://s3.tradingview.com/tv.js";

/** Production builds default off unless `VITE_FEATURE_TRADINGVIEW=true`; dev server defaults on unless explicitly `false`. */
export function is_tradingview_embed_enabled(): boolean {
  const token = import.meta.env.VITE_FEATURE_TRADINGVIEW?.trim().toLowerCase() ?? "";
  if (token === "true" || token === "1" || token === "on") {
    return true;
  }
  if (token === "false" || token === "0" || token === "off") {
    return false;
  }
  return import.meta.env.DEV;
}

export function trading_view_symbol_from_base(asset: string | undefined): string {
  if (!asset || asset.trim().length === 0) {
    return "BITGET:BTCUSDT";
  }
  const compact = asset.replace("/", "").replace("-", "").toUpperCase();
  return `BITGET:${compact}`;
}

export interface TradingViewPanelProps {
  asset_base: string;
  subtitle?: string;
}

export function TradingViewPanel(props: TradingViewPanelProps): ReactElement {
  const { asset_base, subtitle } = props;
  const embed_enabled = is_tradingview_embed_enabled();
  const containerId = useMemo(() => `tradingview-${crypto.randomUUID()}`, []);
  const symbol = trading_view_symbol_from_base(asset_base);

  useEffect(() => {
    if (!embed_enabled) {
      return undefined;
    }

    let cancelled = false;

    const mountWidget = () => {
      if (cancelled || !window.TradingView) {
        return;
      }

      const container = document.getElementById(containerId);
      container?.replaceChildren();

      new window.TradingView.widget({
        autosize: true,
        symbol,
        interval: "30",
        timezone: "Etc/UTC",
        theme: "dark",
        style: "1",
        locale: "en",
        enable_publishing: false,
        allow_symbol_change: true,
        hide_side_toolbar: false,
        details: true,
        studies: ["Volume@tv-basicstudies"],
        container_id: containerId,
      });
    };

    if (window.TradingView) {
      mountWidget();
    } else {
      const existingScript = document.querySelector<HTMLScriptElement>(
        `script[src="${TRADING_VIEW_SCRIPT_SRC}"]`,
      );
      const script = existingScript ?? document.createElement("script");
      script.src = TRADING_VIEW_SCRIPT_SRC;
      script.async = true;
      script.onload = mountWidget;

      if (!existingScript) {
        document.body.appendChild(script);
      }
    }

    return () => {
      cancelled = true;
      const container = document.getElementById(containerId);
      container?.replaceChildren();
    };
  }, [containerId, embed_enabled, symbol]);

  if (!embed_enabled) {
    return (
      <section className="rounded-[var(--radius-lg)] border border-[var(--border)] bg-[var(--bg-surface)] p-5">
        <h2 className="text-[11px] font-black uppercase tracking-wide text-[var(--text-tertiary)]">
          TradingView
        </h2>
        <p className="mt-2 text-sm text-[var(--text-secondary)]">
          Chart embed is off for this build. Set{" "}
          <span className="font-mono text-[var(--text-primary)]">VITE_FEATURE_TRADINGVIEW=true</span> for
          production, or use the dev server (defaults on).
        </p>
      </section>
    );
  }

  return (
    <section className="rounded-[var(--radius-lg)] border border-[var(--border)] bg-[var(--bg-surface)] p-5">
      <div className="mb-3 flex flex-col gap-2 md:flex-row md:items-end md:justify-between">
        <div>
          <h2 className="text-[11px] font-black uppercase tracking-wide text-[var(--text-tertiary)]">
            TradingView — 30m
          </h2>
          {subtitle !== undefined && subtitle.length > 0 ? (
            <p className="mt-1 text-xs text-[var(--text-secondary)]">{subtitle}</p>
          ) : null}
        </div>
        <span className="w-fit rounded border border-[var(--border-hover)] px-2 py-0.5 font-mono text-[10px] text-[var(--text-secondary)]">
          {symbol}
        </span>
      </div>
      <div className="h-[420px] overflow-hidden rounded-[var(--radius-md)] border border-[var(--border-hover)] bg-[var(--bg-base)]">
        <div className="h-full w-full" id={containerId} />
      </div>
    </section>
  );
}
