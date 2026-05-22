import type { ImgHTMLAttributes } from "react";

import { cn } from "../../lib/cn";

export interface AtlasLogoProps extends Omit<ImgHTMLAttributes<HTMLImageElement>, "src"> {}

const atlas_logo_src = `${import.meta.env.BASE_URL}atlas_logo.png`;

/** Raster Atlas wordmark — place `atlas_logo.png` under `frontend/public/`. */
export function AtlasLogo({ className, alt = "Atlas", ...rest }: AtlasLogoProps) {
  return (
    <img
      src={atlas_logo_src}
      alt={alt}
      decoding="async"
      loading="lazy"
      className={cn("object-contain object-left", className)}
      {...rest}
    />
  );
}
