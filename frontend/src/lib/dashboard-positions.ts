export interface DashboardPositionSlot {
  slot_index: number;
  asset: string | null;
  direction: "LONG" | "SHORT" | null;
  unrealized_pnl_percent: string | null;
}

function create_empty_dashboard_slot(slot_index: number): DashboardPositionSlot {
  return {
    slot_index,
    asset: null,
    direction: null,
    unrealized_pnl_percent: null,
  };
}

function is_record(input: unknown): input is Record<string, unknown> {
  return typeof input === "object" && input !== null && !Array.isArray(input);
}

function read_string(value: unknown): string | null {
  return typeof value === "string" && value.trim().length > 0 ? value : null;
}

function read_optional_direction(value: unknown): DashboardPositionSlot["direction"] {
  if (typeof value !== "string") {
    return null;
  }
  const token = value.trim().toUpperCase();
  if (token === "LONG" || token === "SHORT") {
    return token;
  }
  return null;
}

export function coerce_dashboard_position_row(raw: Record<string, unknown>): DashboardPositionSlot | null {
  const slot_raw = raw.slot_index ?? raw.slotIndex;
  const numeric = typeof slot_raw === "number" ? slot_raw : Number(slot_raw);
  if (!Number.isFinite(numeric) || numeric <= 0) {
    return null;
  }

  return {
    slot_index: Math.round(numeric),
    asset: read_string(raw.asset ?? raw.symbol),
    direction: read_optional_direction(raw.direction ?? raw.side),
    unrealized_pnl_percent:
      typeof raw.unrealized_pnl_percent === "string"
        ? raw.unrealized_pnl_percent
        : typeof raw.unrealizedPnlPercent === "string"
          ? raw.unrealizedPnlPercent
          : null,
  };
}

const SLOT_COUNT = 6;

/** Always returns exactly six PROMETHEUS ladder slots sorted slot_index ascending. */
export function calculate_dashboard_position_slots_from_payload(payload: unknown): DashboardPositionSlot[] {
  const rows: DashboardPositionSlot[] = [];
  if (Array.isArray(payload)) {
    for (const row of payload) {
      if (!is_record(row)) {
        continue;
      }
      const parsed = coerce_dashboard_position_row(row);
      if (parsed !== null) {
        rows.push(parsed);
      }
    }
  }

  const by_slot = new Map<number, DashboardPositionSlot>();
  for (const slot of rows) {
    const clamped = Math.min(Math.max(slot.slot_index, 1), SLOT_COUNT);
    by_slot.set(clamped, {
      slot_index: clamped,
      asset: slot.asset,
      direction: slot.direction,
      unrealized_pnl_percent: slot.unrealized_pnl_percent,
    });
  }

  const merged: DashboardPositionSlot[] = [];
  for (let slot_index = 1; slot_index <= SLOT_COUNT; slot_index += 1) {
    merged.push(by_slot.get(slot_index) ?? create_empty_dashboard_slot(slot_index));
  }

  return merged;
}
