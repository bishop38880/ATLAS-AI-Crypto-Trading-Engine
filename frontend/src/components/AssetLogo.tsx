import { useState } from "react";

import { getLogoUrl } from "../lib/asset-logos";
import { cn } from "../lib/cn";

export interface AssetLogoProps {
  symbol: string;
  size?: "xs" | "sm" | "md" | "lg";
  className?: string;
}

const SIZE_CLASSES = {
  xs: "w-4 h-4 text-[7px]",
  sm: "w-5 h-5 text-[8px]",
  md: "w-7 h-7 text-[10px]",
  lg: "w-10 h-10 text-xs",
};

export function AssetLogo({ symbol, size = "sm", className }: AssetLogoProps) {
  const [errored, setErrored] = useState(false);
  const url = getLogoUrl(symbol);
  const ticker = symbol.replace(/[/:-]?USDT$/i, "").replace(/[/:-].*$/, "").toUpperCase();
  const sizeClass = SIZE_CLASSES[size];

  if (!url || errored) {
    return (
      <div
        className={cn(
          "rounded-full bg-[var(--bg-elevated)] text-[var(--text-secondary)]",
          "flex items-center justify-center font-bold uppercase",
          sizeClass,
          className,
        )}
        role="img"
        aria-label={`${ticker} logo unavailable`}
      >
        {ticker.slice(0, 2)}
      </div>
    );
  }

  return (
    <img
      src={url}
      alt={`${ticker} logo`}
      loading="lazy"
      decoding="async"
      onError={() => setErrored(true)}
      className={cn("rounded-full object-contain", sizeClass, className)}
    />
  );
}
