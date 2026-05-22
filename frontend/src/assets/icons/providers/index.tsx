import type { ComponentType, SVGProps } from "react";

import {
  AltfinsLogo,
  AlternativeMeLogo,
  BitgetLogo,
  CoinGeckoLogo,
  CoinalyzeLogo,
  DefiLlamaLogo,
  DuneLogo,
  FredLogo,
  HydraLogo,
  HypertrackerLogo,
  NansenLogo,
  OkxMcpLogo,
  PythLogo,
} from "./brand-monograms";
import { DefaultProviderLogo } from "./default-provider-logo";

export type { SVGProps };

export type ProviderLogoComponent = ComponentType<SVGProps<SVGSVGElement>>;

/** Keys mirror `provider.name` from `/api/providers/health`. */
export const PROVIDER_LOGOS: Record<string, ProviderLogoComponent> = {
  Coinalyze: CoinalyzeLogo,
  HYDRA: HydraLogo,
  Bitget: BitgetLogo,
  Pyth: PythLogo,
  CoinGecko: CoinGeckoLogo,
  DefiLlama: DefiLlamaLogo,
  Nansen: NansenLogo,
  FRED: FredLogo,
  Dune: DuneLogo,
  Hypertracker: HypertrackerLogo,
  "Alternative.me": AlternativeMeLogo,
  "OKX MCP": OkxMcpLogo,
  Altfins: AltfinsLogo,
};

export function getProviderLogo(name: string): ProviderLogoComponent {
  const hit = PROVIDER_LOGOS[name];
  return hit ?? DefaultProviderLogo;
}
