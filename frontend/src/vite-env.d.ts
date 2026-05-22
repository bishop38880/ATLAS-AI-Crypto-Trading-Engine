/// <reference types="vite/client" />

declare module "*.css";

interface ImportMetaEnv {
  /** When unset in dev, API/WS use same-origin (Vite proxy). Set to `true` to honor VITE_API_BASE_URL / VITE_WS_BASE_URL during `npm run dev`. */
  readonly VITE_DEV_ABSOLUTE_API?: string;
  readonly VITE_API_BASE_URL: string;
  readonly VITE_WS_BASE_URL: string;
  readonly VITE_APP_ENV: string;
  readonly VITE_CONFLUENCE_WS_PATH: string;
  readonly VITE_HYDRA_DASHBOARD_URL?: string;
  /** When `true` / `1` / `on`, load TradingView.tv.js in prod; when unset, prod defaults off and dev defaults on. */
  readonly VITE_FEATURE_TRADINGVIEW?: string;
  /** Dev-only kill-switch prefill; UI ignores this when `import.meta.env.PROD`. */
  readonly VITE_POLARIS_PANIC_KEY?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
