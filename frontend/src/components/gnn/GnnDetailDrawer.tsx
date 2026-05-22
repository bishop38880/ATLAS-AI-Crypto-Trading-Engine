import { useEffect, useRef } from "react";
import type { ReactElement } from "react";

import type { GnnHeatmapColumn } from "../../lib/calculate_gnn_shadow_grid";

export interface GnnDetailDrawerProps {
  column: GnnHeatmapColumn | null;
  onClose: () => void;
}

export function GnnDetailDrawer(props: GnnDetailDrawerProps): ReactElement | null {
  const { column, onClose } = props;
  const close_button_ref = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    const on_key_down = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        onClose();
      }
    };
    window.addEventListener("keydown", on_key_down);
    return () => {
      window.removeEventListener("keydown", on_key_down);
    };
  }, [onClose]);

  useEffect(() => {
    if (column !== null) {
      queueMicrotask(() => {
        close_button_ref.current?.focus();
      });
    }
  }, [column]);

  if (column === null) {
    return null;
  }

  const sym = column.base;
  const detail = column.detailRow;

  return (
    <div
      className="fixed inset-0 z-50 flex justify-end bg-black/70 p-4"
      role="presentation"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) {
          onClose();
        }
      }}
    >
      <aside
        className="max-h-full w-full max-w-lg overflow-y-auto rounded-[var(--radius-lg)] border border-[var(--border)] bg-[var(--bg-surface)] shadow-xl"
        role="dialog"
        aria-modal="true"
        aria-labelledby="gnn-drawer-title"
      >
        <header className="flex items-start justify-between gap-3 border-b border-[var(--border)] px-4 py-3">
          <div className="min-w-0">
            <h2
              id="gnn-drawer-title"
              className="truncate font-[family-name:var(--font-display)] text-lg font-semibold text-[var(--text-primary)]"
            >
              GNN shadow detail — {sym}
            </h2>
            <p className="truncate text-xs text-[var(--text-tertiary)]">
              SHADOW MODE — Not affecting live confluence scores.
            </p>
          </div>
          <button
            ref={close_button_ref}
            type="button"
            className="rounded-[var(--radius-sm)] border border-[var(--border)] px-3 py-1 text-xs font-medium text-[var(--text-secondary)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent-cyan)]"
            onClick={onClose}
          >
            Close
          </button>
        </header>

        <div className="space-y-4 px-4 py-4 font-data text-xs text-[var(--text-secondary)]">
          {detail === undefined ? (
            <p>No Redis snapshot for this asset yet.</p>
          ) : (
            <dl className="space-y-2">
              <div className="flex justify-between gap-3">
                <dt>Fused shadow (0–1)</dt>
                <dd className="text-[var(--text-primary)]">{detail.fused01.toFixed(4)}</dd>
              </div>
              <div className="flex justify-between gap-3">
                <dt>Points (0–23)</dt>
                <dd className="text-[var(--text-primary)]">{detail.scorePoints}</dd>
              </div>
              <div className="flex justify-between gap-3">
                <dt>GraphSAGE</dt>
                <dd className="text-[var(--text-primary)]">{format_maybe_float(detail.raw.graphsage_score)}</dd>
              </div>
              <div className="flex justify-between gap-3">
                <dt>GAT</dt>
                <dd className="text-[var(--text-primary)]">{format_maybe_float(detail.raw.gat_score)}</dd>
              </div>
              <div className="flex justify-between gap-3">
                <dt>GIN</dt>
                <dd className="text-[var(--text-primary)]">{format_maybe_float(detail.raw.gin_score)}</dd>
              </div>
              <div className="flex justify-between gap-3">
                <dt>Mamba / LNN / Seq fused</dt>
                <dd className="text-right text-[var(--text-primary)]">
                  {format_maybe_float(detail.raw.mamba_score)} / {format_maybe_float(detail.raw.lnn_score)} /{" "}
                  {format_maybe_float(detail.raw.fused_seq_score)}
                </dd>
              </div>
              <div className="flex justify-between gap-3">
                <dt>Stress triggered</dt>
                <dd className="text-[var(--text-primary)]">{detail.stressTriggered ? "Yes ▼" : "No →"}</dd>
              </div>
              <div className="flex justify-between gap-3">
                <dt>Computed</dt>
                <dd className="text-[var(--text-primary)]">{detail.computedAtIso ?? "—"}</dd>
              </div>
            </dl>
          )}
        </div>
      </aside>
    </div>
  );
}

function format_maybe_float(value: unknown): string {
  if (typeof value === "number" && Number.isFinite(value)) {
    return value.toFixed(4);
  }
  return "—";
}
