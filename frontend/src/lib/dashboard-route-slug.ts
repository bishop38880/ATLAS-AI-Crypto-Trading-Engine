import { derive_base_asset_from_pair } from "./dashboard-symbol";

/** Normalised slug for `/signals/$asset` deep links. */
export function derive_signal_route_slug(pair: string): string {
  return derive_base_asset_from_pair(pair);
}
