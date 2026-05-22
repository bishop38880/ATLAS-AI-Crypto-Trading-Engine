import { describe, expect, it } from "vitest";

import { DefaultProviderLogo } from "../assets/icons/providers/default-provider-logo";
import { getProviderLogo } from "../assets/icons/providers/index";

describe("getProviderLogo", () => {
  it("resolves known registry names to deterministic components", () => {
    expect(getProviderLogo("Coinalyze")).toBe(getProviderLogo("Coinalyze"));
  });

  it("falls back to DefaultProviderLogo when the name is absent from the map", () => {
    const a = getProviderLogo("__no_such_provider_slug__");
    const b = getProviderLogo("__other_unknown__");
    expect(a).toBe(DefaultProviderLogo);
    expect(b).toBe(a);
  });

  it("maps HYDRA logo key for cascade intelligence tile", () => {
    expect(getProviderLogo("HYDRA")).not.toBe(DefaultProviderLogo);
  });
});
