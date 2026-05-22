import { Link } from "@tanstack/react-router";
import type { ReactElement } from "react";

import {
  build_regime_badge_tooltip,
  detects_dashboard_regime_mask,
  format_regime_badge_label,
  summarize_regime_badge_variant,
} from "../../lib/regime-badge-display";
import { cn } from "../../lib/cn";
import type { SystemHealth } from "../../store/index";
import { Badge } from "../ui/Badge";

export interface RegimeStatusBadgeProps {
  readonly health: SystemHealth | null;
  readonly badgeClassName?: string;
}

export function RegimeStatusBadge(props: RegimeStatusBadgeProps): ReactElement {
  const regime_label = format_regime_badge_label(props.health);
  const regime_tooltip = build_regime_badge_tooltip(props.health ?? null);
  const mask_active = detects_dashboard_regime_mask(props.health);

  return (
    <div className="inline-flex flex-wrap items-center gap-1.5">
      <span
        className={regime_tooltip !== undefined ? "inline-flex cursor-help" : "inline-flex"}
        title={regime_tooltip}
      >
        <Badge
          variant={summarize_regime_badge_variant(props.health?.currentRegime ?? "UNKNOWN")}
          className={props.badgeClassName}
        >
          {regime_label}
        </Badge>
      </span>
      {mask_active ? (
        <Link
          to="/regime"
          className={cn(
            "inline-flex items-center gap-1 rounded-md border border-amber-500/40 bg-amber-950/30 px-2 py-0.5",
            "text-[10px] font-medium text-amber-200 transition-colors hover:border-amber-400/60 hover:text-amber-100",
          )}
          title="Dashboard bucket shows RANGING while HMM reads VOLATILE — open Regime Center for native sizing and gates."
        >
          <span aria-hidden>⚠</span>
          HMM VOLATILE
        </Link>
      ) : null}
    </div>
  );
}
