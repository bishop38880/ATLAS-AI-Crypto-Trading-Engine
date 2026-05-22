import type { ReactElement } from "react";
import type { SVGProps } from "react";

export function DefaultProviderLogo(props: SVGProps<SVGSVGElement>): ReactElement {
  return (
    <svg viewBox="0 0 32 32" xmlns="http://www.w3.org/2000/svg" role="img" aria-hidden {...props}>
      <title>Unknown provider icon</title>
      <rect width="32" height="32" rx="16" fill="var(--bg-elevated)" />
      <ellipse cx="11" cy="14" rx="3" ry="7" fill="none" stroke="var(--text-tertiary)" strokeWidth="1.8" />
      <ellipse cx="21" cy="14" rx="3" ry="7" fill="none" stroke="var(--text-tertiary)" strokeWidth="1.8" />
      <line x1="8" x2="14" y1="22" y2="22" stroke="var(--text-tertiary)" strokeWidth="1.5" strokeLinecap="round" />
      <line x1="18" x2="24" y1="22" y2="22" stroke="var(--text-tertiary)" strokeWidth="1.5" strokeLinecap="round" />
    </svg>
  );
}
