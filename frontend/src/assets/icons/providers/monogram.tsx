import type { SVGProps } from "react";

export function ProviderMonogram({
  letter,
  ...rest
}: { letter: string } & SVGProps<SVGSVGElement>) {
  const label = `${letter} provider logo placeholder`;

  return (
    <svg
      viewBox="0 0 32 32"
      xmlns="http://www.w3.org/2000/svg"
      role="img"
      aria-label={label}
      {...rest}
    >
      <circle cx="16" cy="16" r="15" fill="var(--bg-elevated)" />
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
