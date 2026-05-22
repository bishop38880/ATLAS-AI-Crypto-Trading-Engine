import { createElement, memo, useState, type ReactElement } from "react";

import { getProviderLogo } from "../../assets/icons/providers/index";
import { getProviderLogoUrl } from "../../lib/provider-logo-urls";
import { cn } from "../../lib/cn";

export interface ProviderBrandLogoProps {
  /** Registry display name, e.g. `Coinalyze`. */
  name: string;
  className?: string;
}

/**
 * Prefer bundled marks from `public/providers/`; fall back to inline SVG monograms.
 */
export const ProviderBrandLogo = memo(function ProviderBrandLogo({
  name,
  className,
}: ProviderBrandLogoProps): ReactElement {
  const url = getProviderLogoUrl(name);
  const SvgComponent = getProviderLogo(name);
  const [useSvg, setUseSvg] = useState(false);

  if (!url || useSvg) {
    return createElement(SvgComponent, {
      className,
      "aria-hidden": true,
    });
  }

  return (
    <img
      src={url}
      alt=""
      aria-hidden
      loading="lazy"
      decoding="async"
      onError={() => {
        setUseSvg(true);
      }}
      className={cn("object-contain", className)}
    />
  );
});
