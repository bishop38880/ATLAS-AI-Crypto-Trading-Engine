import { useEffect, type ReactElement } from "react";

import { cn } from "../../lib/cn";
import { DAILY_ROTATION_STAGED_CAP } from "../../lib/daily-rotation-staged";
import { derive_pair_from_rotation_entry } from "../../lib/dashboard-symbol";
import { useAssetUniverseStore } from "../../store/assetUniverseStore";
import { Badge } from "../ui/Badge";

export function DailyRotationModal(): ReactElement | null {
  const open = useAssetUniverseStore((s) => s.isRotationModalOpen);
  const set_open = useAssetUniverseStore((s) => s.setRotationModalOpen);
  const staged = useAssetUniverseStore((s) => s.dailyRotationStaged);
  const selected = useAssetUniverseStore((s) => s.selectedUniverseAsset);
  const add_staged = useAssetUniverseStore((s) => s.addToDailyRotationStaged);
  const remove_staged = useAssetUniverseStore((s) => s.removeFromDailyRotationStaged);
  const clear_staged = useAssetUniverseStore((s) => s.clearDailyRotationStaged);

  useEffect(() => {
    if (!open) {
      return;
    }
    const on_escape = (event: KeyboardEvent): void => {
      if (event.key === "Escape") {
        set_open(false);
      }
    };
    window.addEventListener("keydown", on_escape);
    return () => window.removeEventListener("keydown", on_escape);
  }, [open, set_open]);

  if (!open) {
    return null;
  }

  const canonical_selected = selected !== null ? derive_pair_from_rotation_entry(selected) : null;
  const at_cap = staged.length >= DAILY_ROTATION_STAGED_CAP;
  const already_staged =
    canonical_selected !== null &&
    staged.some((row) => derive_pair_from_rotation_entry(row).toUpperCase() === canonical_selected.toUpperCase());
  const can_add = canonical_selected !== null && !at_cap && !already_staged;

  const close = (): void => {
    set_open(false);
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 px-4"
      role="presentation"
      onClick={close}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="daily-rotation-modal-title"
        className="w-full max-w-md rounded-2xl border border-[var(--border-accent)] bg-[var(--bg-surface)] p-5 shadow-[0_24px_80px_rgba(0,0,0,0.55)]"
        onClick={(event) => event.stopPropagation()}
      >
        <Badge variant="live">Daily rotation</Badge>
        <h2 id="daily-rotation-modal-title" className="mt-3 text-lg font-semibold text-[var(--text-primary)]">
          Stage up to {String(DAILY_ROTATION_STAGED_CAP)} pairs
        </h2>
        <p className="mt-2 text-sm leading-6 text-[var(--text-secondary)]">
          Queued locally for a future{" "}
          <span className="font-mono text-[var(--text-tertiary)]">POST /api/executive/rotation-override</span> hook.
          Select a card on the grid, then add it here.
        </p>

        <div className="mt-4 rounded-[var(--radius-md)] border border-[var(--border)] bg-[var(--bg-base)] p-3">
          <p className="text-[11px] font-semibold uppercase tracking-wide text-[var(--text-tertiary)]">
            Staged ({String(staged.length)}/{String(DAILY_ROTATION_STAGED_CAP)})
          </p>
          {staged.length === 0 ? (
            <p className="mt-2 text-sm text-[var(--text-secondary)]">No pairs queued yet.</p>
          ) : (
            <ul className="mt-3 space-y-2">
              {staged.map((row) => (
                <li
                  key={row}
                  className="flex items-center justify-between gap-2 rounded-[var(--radius-sm)] border border-white/10 bg-[var(--bg-overlay)] px-3 py-2 font-data text-sm text-[var(--text-primary)]"
                >
                  <span>{derive_pair_from_rotation_entry(row)}</span>
                  <button
                    type="button"
                    aria-label={`Remove ${row} from daily rotation queue`}
                    className="rounded px-2 text-[var(--danger)] hover:bg-[var(--danger)]/15 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent-cyan)]"
                    onClick={() => remove_staged(row)}
                  >
                    Remove
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>

        <div className="mt-5 flex flex-col gap-2 sm:flex-row sm:flex-wrap sm:justify-end">
          <button
            type="button"
            onClick={() => {
              if (canonical_selected !== null) {
                add_staged(canonical_selected);
              }
            }}
            disabled={!can_add}
            aria-disabled={!can_add}
            title={
              canonical_selected === null
                ? "Select an asset on the grid first"
                : at_cap
                  ? "Queue is full"
                  : already_staged
                    ? "Already in queue"
                    : undefined
            }
            className={cn(
              "rounded-[var(--radius-sm)] border px-4 py-2 text-sm font-bold uppercase tracking-wide focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent-cyan)]",
              can_add
                ? "border-[var(--accent-cyan)]/50 bg-[var(--accent-cyan)]/12 text-[var(--accent-cyan)] hover:bg-[var(--accent-cyan)]/20"
                : "cursor-not-allowed border-[var(--border)] text-[var(--text-tertiary)]",
            )}
          >
            Add selected
          </button>
          <button
            type="button"
            onClick={() => clear_staged()}
            disabled={staged.length === 0}
            aria-disabled={staged.length === 0}
            className="rounded-[var(--radius-sm)] border border-[var(--border)] px-4 py-2 text-sm font-semibold text-[var(--text-secondary)] hover:border-[var(--border-hover)] hover:text-[var(--text-primary)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent-cyan)] disabled:cursor-not-allowed disabled:opacity-50"
          >
            Clear all
          </button>
          <button
            type="button"
            onClick={close}
            className="rounded-[var(--radius-sm)] border border-[var(--border)] px-4 py-2 text-sm font-semibold text-[var(--text-secondary)] hover:border-[var(--border-hover)] hover:text-[var(--text-primary)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent-cyan)]"
          >
            Close
          </button>
        </div>
      </div>
    </div>
  );
}
