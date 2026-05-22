import type { ReactElement } from "react";

import {
  format_memory_integer,
  format_memory_percent,
} from "../../lib/format-memory-dashboard";
import type { VerifierStatsWire } from "../../types/memory-api";
import { Card } from "../ui/Card";
import { Skeleton } from "../ui/Skeleton";

export interface VerifierStatsProps {
  data: VerifierStatsWire | undefined;
  is_loading: boolean;
  is_error: boolean;
}

export function VerifierStats(props: VerifierStatsProps): ReactElement {
  const { data, is_loading, is_error } = props;

  if (is_loading && data === undefined) {
    return (
      <Card title="Document Verifier" accent="amber">
        <div className="space-y-3" aria-busy="true">
          <Skeleton height={96} rounded={false} className="rounded-[var(--radius-sm)]" />
        </div>
      </Card>
    );
  }

  if (is_error && data === undefined) {
    return (
      <Card title="Document Verifier" accent="amber">
        <p className="text-xs text-[var(--sell)]" role="status">
          Could not load verifier statistics.
        </p>
      </Card>
    );
  }

  if (data === undefined) {
    return (
      <Card title="Document Verifier" accent="amber">
        <p className="text-xs text-[var(--text-secondary)]">No verifier data.</p>
      </Card>
    );
  }

  const show_detail = data.documentsVerified > 0;

  return (
    <Card title="Document Verifier" accent="amber">
      <div className="space-y-4 font-data text-xs">
        <div>
          <span className="text-[var(--text-secondary)]">Documents verified this cycle</span>
          <div className="mt-1 tabular-nums text-lg font-semibold text-[var(--text-primary)]">
            {format_memory_integer(data.documentsVerified)}
          </div>
        </div>

        {show_detail ? (
          <ul className="space-y-1 border-b border-[var(--border)] pb-3">
            <VerifierLine
              label="Passed"
              count={data.passed}
              pct={data.passedPercent}
              value_class="text-[var(--success)]"
            />
            <VerifierLine
              label="Repaired"
              count={data.repaired}
              pct={data.repairedPercent}
              value_class="text-[var(--hold)]"
              detail={data.repairedDetail}
            />
            <VerifierLine
              label="Flagged"
              count={data.flagged}
              pct={data.flaggedPercent}
              value_class="text-[var(--hold)]"
              detail={data.flaggedDetail}
            />
            <VerifierLine
              label="Rejected"
              count={data.rejected}
              pct={data.rejectedPercent}
              value_class="text-[var(--sell)]"
              detail={data.rejectedDetail}
            />
          </ul>
        ) : (
          <p className="text-[var(--text-tertiary)]">No documents in this verifier cycle yet.</p>
        )}

        {data.contradictions.length > 0 && (
          <div className="space-y-3">
            <p className="font-body text-[11px] font-semibold uppercase tracking-wide text-[var(--text-secondary)]">
              Contradiction detected
            </p>
            <ul className="space-y-3">
              {data.contradictions.map((c) => (
                <li
                  key={`${c.asset}-${c.statementA}-${c.statementB}`}
                  className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[var(--bg-elevated)]/60 p-3"
                >
                  <p className="font-semibold text-[var(--text-primary)]">{c.asset}</p>
                  <p className="mt-2 text-[var(--text-secondary)]">
                    <span className="text-[var(--text-primary)]">&quot;{c.statementA}&quot;</span>
                    <span className="mx-1 text-[var(--text-tertiary)]">↔</span>
                    <span className="text-[var(--text-primary)]">&quot;{c.statementB}&quot;</span>
                  </p>
                  <p className="mt-1 text-[10px] text-[var(--text-tertiary)]">
                    ({c.statementAAge} · {c.statementBAge})
                  </p>
                  <p className="mt-2 text-[var(--accent-teal)]">→ {c.resolution}</p>
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </Card>
  );
}

function VerifierLine(props: {
  label: string;
  count: number;
  pct: number;
  value_class: string;
  detail?: string | null;
}): ReactElement {
  return (
    <li className="flex flex-wrap items-baseline justify-between gap-2">
      <span className="text-[var(--text-secondary)]">{props.label}:</span>
      <span className="text-right">
        <span className={`tabular-nums ${props.value_class}`}>{format_memory_integer(props.count)}</span>
        <span className="ml-2 tabular-nums text-[var(--text-tertiary)]">
          ({format_memory_percent(props.pct, 1)})
        </span>
        {props.detail !== undefined && props.detail !== null && props.detail.length > 0 ? (
          <span className="ml-2 text-[var(--text-tertiary)]">— {props.detail}</span>
        ) : null}
      </span>
    </li>
  );
}
