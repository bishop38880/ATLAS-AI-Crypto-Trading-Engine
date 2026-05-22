import { Link } from "@tanstack/react-router";
import type { ReactElement } from "react";

import {
  calculate_omnibox_budget_meter_tone,
  calculate_omnibox_cost_cap_percent_used,
  calculate_omnibox_minute_limit_hit,
} from "../../lib/calculate-omnibox-budget-meter-style";
import type { OmniBoxBudgetPayload } from "../../types/omnibox-api";
import { cn } from "../../lib/cn";

export interface QueryBudgetMeterProps {
  budget: OmniBoxBudgetPayload | undefined;
  load_error: boolean;
}

function tone_classes(tone: "green" | "amber" | "red"): string {
  if (tone === "green") {
    return "text-[var(--success)]";
  }
  if (tone === "amber") {
    return "text-[var(--warning)]";
  }
  return "text-[var(--danger)]";
}

export function QueryBudgetMeter(props: QueryBudgetMeterProps): ReactElement {
  const { budget, load_error } = props;

  if (load_error || !budget) {
    return (
      <div
        className="rounded-md border border-[var(--border)] bg-[var(--bg-surface)] px-3 py-2 text-xs text-[var(--warning)]"
        aria-live="polite"
      >
        Budget meter unavailable — proceeding without limits display.
      </div>
    );
  }

  const query_pct =
    budget.sessionQueryLimit > 0
      ? (budget.sessionQueriesUsed / budget.sessionQueryLimit) * 100
      : 0;
  const token_pct =
    budget.sessionTokenLimit > 0
      ? (budget.sessionTokensUsed / budget.sessionTokenLimit) * 100
      : 0;
  const cost_pct = calculate_omnibox_cost_cap_percent_used(
    budget.estimatedCostUsd,
    budget.dailyCostCapUsd,
  );

  const minute_hit = calculate_omnibox_minute_limit_hit(
    budget.queriesThisMinute,
    budget.queriesPerMinuteLimit,
  );

  return (
    <div
      className="grid gap-2 rounded-md border border-[var(--border)] bg-[var(--bg-surface)] px-3 py-2 text-xs"
      aria-live="polite"
    >
      <Row
        label="Queries (session)"
        value={`${budget.sessionQueriesUsed} / ${budget.sessionQueryLimit}`}
        tone={calculate_omnibox_budget_meter_tone(query_pct)}
      />
      <Row
        label="Tokens (session)"
        value={`${budget.sessionTokensUsed} / ${budget.sessionTokenLimit}`}
        tone={calculate_omnibox_budget_meter_tone(token_pct)}
      />
      <Row
        label="Est. cost today"
        value={`$${budget.estimatedCostUsd} / $${budget.dailyCostCapUsd}`}
        tone={calculate_omnibox_budget_meter_tone(cost_pct)}
      />
      <div className="flex flex-wrap items-center gap-2 text-[var(--text-tertiary)]">
        <span>
          Rate (minute): {budget.queriesThisMinute} /{" "}
          {budget.queriesPerMinuteLimit}
        </span>
        <span className={minute_hit ? "text-[var(--danger)]" : "text-[var(--success)]"}>
          {minute_hit ? "▲ limit" : "→ OK"}
        </span>
      </div>
      {budget.dailyCapReached ? (
        <div className="text-[var(--danger)]">
          Daily cost cap reached. Reset in{" "}
          {budget.costCapResetHours ?? "—"}h.
          {budget.operatorOverridePath ? (
            <>
              {" "}
              <Link
                to={budget.operatorOverridePath}
                className="underline focus-visible:rounded-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent-cyan)]"
              >
                Operator override
              </Link>
            </>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

function Row(props: {
  label: string;
  value: string;
  tone: "green" | "amber" | "red";
}): ReactElement {
  return (
    <div className="flex flex-wrap justify-between gap-2">
      <span className="text-[var(--text-secondary)]">{props.label}</span>
      <span className={cn("font-data", tone_classes(props.tone))}>
        {props.value}
      </span>
    </div>
  );
}
