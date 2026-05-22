import { describe, expect, it } from "vitest";

import { getProviderLogoUrl, PROVIDER_LOGO_URLS } from "./provider-logo-urls";

describe("getProviderLogoUrl", () => {
  it("returns public paths for providers with bundled marks", () => {
    expect(getProviderLogoUrl("Coinalyze")).toBe("/providers/coinalyze.png");
    expect(getProviderLogoUrl("HYDRA")).toBe("/providers/hydra.svg");
    expect(getProviderLogoUrl("Alternative.me")).toBe("/providers/alternative-me.png");
  });

  it("returns undefined when no raster/svg is bundled", () => {
    expect(getProviderLogoUrl("Nansen")).toBeUndefined();
    expect(getProviderLogoUrl("DefiLlama")).toBeUndefined();
  });

  it("keeps PROVIDER_LOGO_URLS keys aligned with registry names only", () => {
    for (const path of Object.values(PROVIDER_LOGO_URLS)) {
      expect(path.startsWith("/providers/")).toBe(true);
    }
  });
});
