import type { ReactElement } from "react";

export interface ProviderMonogramProps {
  /** One or two character label for placeholder discs. */
  letter: string;
}

export function ProviderMonogram({ letter }: ProviderMonogramProps): ReactElement {
  return (
    <svg
      viewBox="0 0 32 32"
      xmlns="http://www.w3.org/2000/svg"
      role="img"
      aria-label={`${letter} provider logo placeholder`}
    >
      <circle cx="16" cy="16" r="16" fill="var(--bg-elevated)" />
      <text
        x="16"
        y="21"
        textAnchor="middle"
        fontSize="11"
        fontFamily="var(--font-data)"
        fill="var(--text-secondary)"
      >
        {letter}
      </text>
    </svg>
  );
}
