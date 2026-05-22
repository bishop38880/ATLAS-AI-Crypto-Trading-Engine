import type { ReactElement, ReactNode } from "react";

const iconClass = "size-[18px] shrink-0 text-current";

function wrapSvg(children: ReactNode): ReactElement {
  return (
    <svg
      className={iconClass}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
    >
      {children}
    </svg>
  );
}

export function IconDashboard(): ReactElement {
  return wrapSvg(
    <>
      <rect x="3" y="3" width="7" height="7" rx="1" />
      <rect x="14" y="3" width="7" height="7" rx="1" />
      <rect x="3" y="14" width="7" height="7" rx="1" />
      <rect x="14" y="14" width="7" height="7" rx="1" />
    </>,
  );
}

/** Mini heatmap tiles — correlation matrix nav affordance */
export function IconHeatmap(): ReactElement {
  return wrapSvg(
    <>
      <rect x="3" y="3" width="7" height="7" rx="1.2" fill="currentColor" opacity="0.92" stroke="none" />
      <rect x="14" y="3" width="7" height="7" rx="1.2" fill="currentColor" opacity="0.72" stroke="none" />
      <rect x="3" y="14" width="7" height="7" rx="1.2" fill="currentColor" opacity="0.62" stroke="none" />
      <rect x="14" y="14" width="7" height="7" rx="1.2" fill="currentColor" opacity="0.42" stroke="none" />
    </>,
  );
}

export function IconZap(): ReactElement {
  return wrapSvg(<path d="M13 2 3 14h9l-1 8 10-12h-9l1-8z" />);
}

export function IconGlobe(): ReactElement {
  return wrapSvg(
    <>
      <circle cx="12" cy="12" r="10" />
      <path d="M2 12h20" />
      <path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z" />
    </>,
  );
}

export function IconCpu(): ReactElement {
  return wrapSvg(
    <>
      <rect x="4" y="4" width="16" height="16" rx="2" />
      <rect x="9" y="9" width="6" height="6" />
      <path d="M9 1v3M15 1v3M9 20v3M15 20v3M20 9h3M20 14h3M1 9h3M1 14h3" />
    </>,
  );
}

export function IconActivity(): ReactElement {
  return wrapSvg(<path d="M22 12h-4l-3 9L9 3l-3 9H2" />);
}

export function IconNetwork(): ReactElement {
  return wrapSvg(
    <>
      <circle cx="5" cy="6" r="3" />
      <circle cx="19" cy="18" r="3" />
      <circle cx="19" cy="6" r="3" />
      <path d="M7.5 7.5 12 12m0 0 4.5 4.5M16.5 7.5 12 12m0 0-4.5 4.5" />
    </>,
  );
}

export function IconDatabase(): ReactElement {
  return wrapSvg(
    <>
      <ellipse cx="12" cy="5" rx="9" ry="3" />
      <path d="M21 12c0 1.66-4 3-9 3s-9-1.34-9-3" />
      <path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5" />
    </>,
  );
}

export function IconMessage(): ReactElement {
  return wrapSvg(<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />);
}

export function IconBarChart(): ReactElement {
  return wrapSvg(
    <>
      <path d="M3 3v18h18" />
      <path d="M7 16v-5" />
      <path d="M12 16v-9" />
      <path d="M17 16v-3" />
    </>,
  );
}

/** Mini line-chart stroke for paper-validation / replay views */
export function IconLineChart(): ReactElement {
  return wrapSvg(
    <>
      <path d="M3 19h18M3 3v18" opacity="0.35" />
      <path d="M4 16 9 11l4 7 9-13" />
    </>,
  );
}

export function IconShield(): ReactElement {
  return wrapSvg(
    <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />,
  );
}

export function IconPipeline(): ReactElement {
  return wrapSvg(
    <>
      <circle cx="6" cy="6" r="2.5" />
      <circle cx="18" cy="6" r="2.5" />
      <circle cx="12" cy="18" r="2.5" />
      <path d="M8.2 7.5 10.5 16M15.8 7.5 13.5 16M8.5 6h7" />
    </>,
  );
}

export function IconSettings(): ReactElement {
  return wrapSvg(
    <>
      <path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.09a2 2 0 0 1-1-1.74v-.47a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z" />
      <circle cx="12" cy="12" r="3" />
    </>,
  );
}
