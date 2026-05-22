import type { ReactElement, ReactNode } from "react";

const iconClass = "size-4 shrink-0 text-current";

function wrap(children: ReactNode): ReactElement {
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

export function OmniboxIconDatabase(): ReactElement {
  return wrap(
    <>
      <ellipse cx="12" cy="5" rx="9" ry="3" />
      <path d="M21 12c0 1.66-4 3-9 3s-9-1.34-9-3" />
      <path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5" />
    </>,
  );
}

export function OmniboxIconRadio(): ReactElement {
  return wrap(
    <>
      <path d="M4.9 19.1C3 17.2 2 14.7 2 12s1-5.2 2.9-7.1" />
      <path d="M7.8 16.2c-1.2-1.2-2-2.9-2-4.8s.8-3.6 2-4.8" />
      <circle cx="12" cy="12" r="3" />
      <path d="M16.2 7.8c1.2 1.2 2 2.9 2 4.8s-.8 3.6-2 4.8" />
      <path d="M19.1 4.9C21 6.8 22 9.3 22 12s-1 5.2-2.9 7.1" />
    </>,
  );
}

export function OmniboxIconZap(): ReactElement {
  return wrap(<path d="M13 2 3 14h9l-1 8 10-12h-9l1-8z" />);
}

export function OmniboxIconBrain(): ReactElement {
  return wrap(
    <>
      <path d="M12 18V5" />
      <path d="M15 13a4 4 0 0 1 2 5M9 13a4 4 0 0 0-2 5" />
      <path d="M16 7a4 4 0 0 0-8 0v11a3 3 0 0 0 6 0V7z" />
      <path d="M12 7a4 4 0 0 1 4 4v8" />
    </>,
  );
}
