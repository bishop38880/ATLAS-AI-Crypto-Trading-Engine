/**
 * Raster / SVG marks shipped under `frontend/public/providers/`.
 * Keys mirror `name` from `/api/providers/health` (`PROVIDER_REGISTRY`).
 */

const BASE = "/providers";

/** Provider display name → URL served from Vite `public/`. */
export const PROVIDER_LOGO_URLS: Readonly<Record<string, string>> = {
  Coinalyze: `${BASE}/coinalyze.png`,
  Bitget: `${BASE}/bitget.png`,
  Pyth: `${BASE}/pyth.png`,
  CoinGecko: `${BASE}/coingecko.png`,
  HYDRA: `${BASE}/hydra.svg`,
  Dune: `${BASE}/dune.png`,
  "Alternative.me": `${BASE}/alternative-me.png`,
  Altfins: `${BASE}/altfins.png`,
};

export function getProviderLogoUrl(name: string): string | undefined {
  return PROVIDER_LOGO_URLS[name];
}
