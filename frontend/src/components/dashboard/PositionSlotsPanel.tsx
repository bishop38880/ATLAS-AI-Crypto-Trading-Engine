import { type DragEvent, type ReactElement, useEffect, useState } from "react";

import { cn } from "../../lib/cn";
import type { DashboardPositionSlot } from "../../lib/dashboard-positions";
import {
  DASHBOARD_PIN_DRAG_MIME_TYPE,
  read_dashboard_pin_drop,
} from "../../lib/dashboard-pin-dnd";
import { derive_pair_from_rotation_entry } from "../../lib/dashboard-symbol";
import { useAssetUniverseStore, type ExecutionLadderSlotNumber } from "../../store/assetUniverseStore";

function coerce_execution_ladder_slot_index(raw_slot_index: number): ExecutionLadderSlotNumber | null {
  if (!Number.isFinite(raw_slot_index) || !Number.isInteger(raw_slot_index)) {
    return null;
  }
  if (raw_slot_index < 1 || raw_slot_index > 6) {
    return null;
  }
  return raw_slot_index as ExecutionLadderSlotNumber;
}

function derive_unreal_glyph(raw: string | null): "▲" | "▼" | "→" {
  if (raw === null) {
    return "→";
  }
  const value = Number.parseFloat(raw.replace(/%/g, ""));
  if (!Number.isFinite(value) || value === 0) {
    return "→";
  }
  return value > 0 ? "▲" : "▼";
}

export interface PositionSlotsPanelProps {
  slots: DashboardPositionSlot[];
}

export function PositionSlotsPanel({ slots }: PositionSlotsPanelProps): ReactElement {
  const ladder_focus_by_slot_number = useAssetUniverseStore((state) => state.ladderFocusBySlot);
  const assign_ladder_focus_from_pin_drop_action = useAssetUniverseStore(
    (state) => state.assignLadderFocusFromPinDrop,
  );
  const clear_ladder_focus_assignment_action = useAssetUniverseStore(
    (state) => state.clearLadderFocusAssignment,
  );

  const [drag_hover_slot_raw_index, set_drag_hover_slot_raw_index] = useState<number | null>(null);

  useEffect(() => {
    const clear_drag_highlight_state = (): void => {
      set_drag_hover_slot_raw_index(null);
    };

    window.addEventListener("dragend", clear_drag_highlight_state);
    return (): void => {
      window.removeEventListener("dragend", clear_drag_highlight_state);
    };
  }, []);

  const clipboard_accepts_dashboard_pin_drag = (
    clipboard_event: DragEvent<HTMLDivElement>,
  ): boolean => {
    return (
      clipboard_event.dataTransfer.types.includes(DASHBOARD_PIN_DRAG_MIME_TYPE)
      || clipboard_event.dataTransfer.types.includes("text/plain")
    );
  };

  const handle_drag_enter_vacancy = (
    vacancy_event: DragEvent<HTMLDivElement>,
    raw_slot_row_index: number,
  ): void => {
    if (!clipboard_accepts_dashboard_pin_drag(vacancy_event)) {
      return;
    }
    set_drag_hover_slot_raw_index(raw_slot_row_index);
  };

  const handle_drag_leave_outer = (
    leave_event: DragEvent<HTMLDivElement>,
    raw_slot_row_index: number,
  ): void => {
    if (!(leave_event.currentTarget instanceof HTMLElement)) {
      return;
    }

    const next_related = leave_event.relatedTarget;

    if (next_related instanceof Node && leave_event.currentTarget.contains(next_related)) {
      return;
    }

    set_drag_hover_slot_raw_index((previous_drag_slot_raw_index): number | null => {
      if (previous_drag_slot_raw_index !== raw_slot_row_index) {
        return previous_drag_slot_raw_index;
      }
      return null;
    });
  };

  return (
    <section
      aria-label="Portfolio position slots display"
      className="command-card command-card-accent-teal mb-4 grid gap-3 p-4 md:grid-cols-6"
    >
      <p className="command-card-subtitle col-span-full">
        Empty ladder slots reserve execution capacity. Drag an asset card from <span className="font-semibold text-[var(--text-primary)]">All assets</span> into any
        open slot to pin primary focus for that PROMETHEUS seat — PROMETHEUS still owns live fills when positions open.
      </p>

      {slots.map((slot) => {
        const typed_execution_slot_coercion = coerce_execution_ladder_slot_index(slot.slot_index);
        const has_live_exchange_row =
          typeof slot.asset === "string" && slot.asset.trim().length > 0;
        const focus_pair_optional: string | undefined =
          typed_execution_slot_coercion !== null && !has_live_exchange_row
            ? ladder_focus_by_slot_number[typed_execution_slot_coercion]
            : undefined;

        const vacant_exchange_row_without_intent_assignment = !has_live_exchange_row && focus_pair_optional === undefined;
        const direction_voice_legacy = `${
          has_live_exchange_row ? `${slot.direction ?? "—"}`.toUpperCase() : ""
        }`;
        const droppable_vacancy = !has_live_exchange_row && typed_execution_slot_coercion !== null;

        const handle_drag_over_exchange_vacancy = (over_event: DragEvent<HTMLDivElement>): void => {
          if (!droppable_vacancy || !clipboard_accepts_dashboard_pin_drag(over_event)) {
            return;
          }

          over_event.preventDefault();
          over_event.dataTransfer.dropEffect = "copy";
          set_drag_hover_slot_raw_index(slot.slot_index);
        };

        const handle_drop_on_exchange_slot = (drop_event: DragEvent<HTMLDivElement>): void => {
          set_drag_hover_slot_raw_index(null);
          if (!droppable_vacancy || typed_execution_slot_coercion === null) {
            return;
          }

          drop_event.preventDefault();

          const raw_pair_drag_payload = read_dashboard_pin_drop(drop_event.dataTransfer);

          if (raw_pair_drag_payload === null || raw_pair_drag_payload.trim().length === 0) {
            return;
          }

          assign_ladder_focus_from_pin_drop_action(typed_execution_slot_coercion, raw_pair_drag_payload.trim());
        };

        const vacancy_drag_highlight_matches =
          droppable_vacancy && drag_hover_slot_raw_index === slot.slot_index;

        let primary_copy_line: ReactElement | string;

        let secondary_copy_fragment: ReactElement | null;

        if (has_live_exchange_row) {
          const glyph_live = derive_unreal_glyph(slot.unrealized_pnl_percent);
          primary_copy_line = `${slot.asset!.trim()} ● ${direction_voice_legacy}`;
          secondary_copy_fragment = (
            <div className="mt-1 flex items-center gap-1 font-data text-[12px] tabular-nums">
              <span
                aria-hidden
                className={
                  glyph_live === "▼"
                    ? "text-[var(--danger)]"
                    : glyph_live === "▲"
                      ? "text-[var(--success)]"
                      : "text-[var(--text-secondary)]"
                }
              >
                {glyph_live}
              </span>
              <span
                className={
                  glyph_live === "▼"
                    ? "metric-glow text-[var(--danger)]"
                    : glyph_live === "▲"
                      ? "metric-glow text-[var(--success)]"
                      : "text-[var(--text-secondary)]"
                }
              >
                {slot.unrealized_pnl_percent ?? "—"}
              </span>
            </div>
          );
        } else if (focus_pair_optional !== undefined && typed_execution_slot_coercion !== null) {
          const friendly_focus_label = derive_pair_from_rotation_entry(focus_pair_optional);
          primary_copy_line = (
            <span className="flex flex-wrap items-center gap-1 normal-case tracking-normal">
              <span>{friendly_focus_label}</span>
              <span className="rounded border border-[var(--accent-teal)]/40 bg-[var(--accent-teal)]/10 px-1.5 py-0.5 font-data text-[9px] font-semibold text-[var(--accent-teal)]">
                INTENT
              </span>
              <button
                type="button"
                aria-label={`Clear ladder focus reservation for slot ${slot.slot_index}`}
                title="Clear focus reservation"
                className="ml-auto rounded px-1 text-[10px] text-[var(--text-tertiary)] hover:text-[var(--danger)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent-cyan)]"
                onClick={(): void => {
                  clear_ladder_focus_assignment_action(typed_execution_slot_coercion);
                }}
              >
                ×
              </button>
            </span>
          );
          secondary_copy_fragment = (
            <p className="mt-2 text-[10px] text-[var(--text-tertiary)]">Reserve capacity • drop another asset to replace</p>
          );
        } else {
          primary_copy_line = "";
          secondary_copy_fragment = (
            <div className="mt-auto flex flex-1 flex-col items-center justify-center gap-1 py-4 text-[var(--text-tertiary)]">
              <span
                aria-hidden
                className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full border border-dashed border-current/35 text-xl font-semibold opacity-65"
              >
                +
              </span>
              <span className="text-center font-data text-[10px] font-semibold leading-tight uppercase tracking-wide">
                Drop asset here
              </span>
            </div>
          );
        }

        return (
          <div
            key={slot.slot_index}
            aria-label={
              vacant_exchange_row_without_intent_assignment
                ? `PROMETHEUS ladder slot ${slot.slot_index} vacancy — drop an asset card here`
                : `PROMETHEUS ladder slot ${slot.slot_index}`
            }
            onDragLeave={(leave_drag_event): void => {
              handle_drag_leave_outer(leave_drag_event, slot.slot_index);
            }}
            {...(droppable_vacancy
              ? {
                  onDragOver: handle_drag_over_exchange_vacancy,
                  onDrop: handle_drop_on_exchange_slot,
                  onDragEnter: (drag_enter_event: DragEvent<HTMLDivElement>): void => {
                    handle_drag_enter_vacancy(drag_enter_event, slot.slot_index);
                  },
                }
              : {})}
            className={cn(
              "flex min-h-[7.75rem] flex-col rounded-md border px-2 py-2 text-[11px] uppercase tracking-wide shadow-[inset_0_1px_0_rgba(255,255,255,0.03)]",
              has_live_exchange_row
                ? "border-cyan-500/45 bg-cyan-950/30 text-slate-100"
                : focus_pair_optional !== undefined
                  ? "border-teal-500/45 bg-teal-950/25 text-slate-100"
                  : cn(
                      "border-dashed border-slate-700 bg-slate-950/40 text-slate-500",
                      vacancy_drag_highlight_matches &&
                        "border-cyan-500/55 bg-cyan-950/35 shadow-[inset_0_0_0_1px_rgba(34,211,238,0.25)]",
                    ),
            )}
          >
            <div className="flex items-center justify-between text-[10px] font-semibold text-[var(--text-secondary)]">
              <span>{`Slot ${slot.slot_index}`}</span>
              {has_live_exchange_row ? (
                <span aria-hidden className="text-[var(--accent-teal)]">
                  ●
                </span>
              ) : focus_pair_optional !== undefined ? (
                <span aria-hidden className="text-[var(--accent-teal)]/80">
                  ◆
                </span>
              ) : (
                <span aria-hidden className="text-[var(--text-tertiary)] opacity-65">
                  +
                </span>
              )}
            </div>

            {has_live_exchange_row || focus_pair_optional !== undefined ? (
              <div className="mt-2 font-semibold normal-case">{primary_copy_line}</div>
            ) : (
              <div className="sr-only">{`Vacant PROMETHEUS ladder slot ${slot.slot_index}; drag an asset here to reserve.`}</div>
            )}

            {secondary_copy_fragment}
          </div>
        );
      })}
    </section>
  );
}
