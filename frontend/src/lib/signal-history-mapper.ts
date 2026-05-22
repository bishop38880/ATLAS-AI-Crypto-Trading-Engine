export interface SignalHistoryTableRow {
  id: string;
  asset: string;
  timestampIso: string;
  totalScore: number;
  decision: string;
  conviction: number;
  passesGate: boolean;
}

function is_record(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/**
 * Normalises `/api/signals/history` JSON (camelCase aliases) into table rows.
 * Scores remain numeric; display rounds to integers for the confluence ladder.
 */
export function map_signal_history_payload(raw: unknown): SignalHistoryTableRow[] {
  if (!Array.isArray(raw)) {
    return [];
  }

  const rows: SignalHistoryTableRow[] = [];

  for (const item of raw) {
    if (!is_record(item)) {
      continue;
    }

    const idRaw = item.id;
    const asset = item.asset;
    const timestampIso = item.timestamp;
    const decision = item.decision;
    const conviction = item.conviction;
    const passesGate = item.passesGate;
    const totalScore = item.totalScore;

    if (typeof asset !== "string" || typeof timestampIso !== "string" || typeof decision !== "string") {
      continue;
    }

    if (typeof conviction !== "number" || typeof passesGate !== "boolean" || typeof totalScore !== "number") {
      continue;
    }

    if (typeof idRaw !== "number" && typeof idRaw !== "string") {
      continue;
    }

    rows.push({
      id: String(idRaw),
      asset,
      timestampIso,
      totalScore: Math.round(totalScore),
      decision,
      conviction,
      passesGate,
    });
  }

  return rows;
}
