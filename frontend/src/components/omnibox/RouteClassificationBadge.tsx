import type { ReactElement } from "react";

import type { OmniBoxRouteKey } from "../../types/omnibox-api";
import { cn } from "../../lib/cn";
import {
  OmniboxIconBrain,
  OmniboxIconDatabase,
  OmniboxIconRadio,
  OmniboxIconZap,
} from "./OmniboxRouteIcons";

const ROUTE_STYLES: Record<
  OmniBoxRouteKey,
  { label: string; colorVar: string; icon: () => ReactElement }
> = {
  RAG_ONLY: {
    label: "Historical RAG",
    colorVar: "var(--accent-violet)",
    icon: OmniboxIconDatabase,
  },
  MCP_ONLY: {
    label: "Live Data",
    colorVar: "var(--accent-cyan)",
    icon: OmniboxIconRadio,
  },
  HYBRID: {
    label: "RAG + Live Data",
    colorVar: "var(--accent-teal)",
    icon: OmniboxIconZap,
  },
  DIRECT: {
    label: "Direct Answer",
    colorVar: "var(--accent-amber)",
    icon: OmniboxIconBrain,
  },
};

export interface RouteClassificationBadgeProps {
  pending: boolean;
  route?: OmniBoxRouteKey;
  confidence?: number;
  rationale?: string;
}

export function RouteClassificationBadge(
  props: RouteClassificationBadgeProps,
): ReactElement {
  const { pending, route, confidence, rationale } = props;

  if (pending && !route) {
    return (
      <div
        className="rounded-md border border-[var(--border)] bg-[var(--bg-surface)] px-3 py-2 text-sm text-[var(--text-secondary)]"
        aria-live="polite"
      >
        Classifying query…
      </div>
    );
  }

  if (!route) {
    return (
      <div className="text-xs text-[var(--text-tertiary)]">
        Awaiting classification…
      </div>
    );
  }

  const style = ROUTE_STYLES[route];
  const Icon = style.icon;

  return (
    <div
      className="rounded-md border border-[var(--border)] bg-[var(--bg-surface)] px-3 py-2 text-sm"
      style={{ borderLeftColor: style.colorVar, borderLeftWidth: 3 }}
      aria-live="polite"
    >
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-[var(--text-tertiary)]">→</span>
        <span
          className={cn("flex items-center gap-1.5 font-semibold")}
          style={{ color: style.colorVar }}
        >
          <Icon />
          {route}{" "}
          <span className="font-normal text-[var(--text-secondary)]">
            [{style.label}]
          </span>
        </span>
        {typeof confidence === "number" ? (
          <span className="text-[var(--text-secondary)]">
            conf: {confidence.toFixed(2)}
          </span>
        ) : null}
      </div>
      {rationale ? (
        <p className="mt-1 text-xs text-[var(--text-secondary)]">{rationale}</p>
      ) : null}
    </div>
  );
}
