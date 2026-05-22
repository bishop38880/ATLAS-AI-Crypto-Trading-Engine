import { create } from "zustand";

export type TelemetryRailLevel = "info" | "warn" | "error";

export interface TelemetryRailEntry {
  id: string;
  createdAtMs: number;
  isoTime: string;
  level: TelemetryRailLevel;
  source: string;
  message: string;
}

const MAX_ENTRIES = 120;

function create_entry_id(): string {
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 9)}`;
}

interface TelemetryRailStore {
  entries: TelemetryRailEntry[];
  push: (level: TelemetryRailLevel, source: string, message: string) => void;
  clear: () => void;
}

export const useTelemetryRailStore = create<TelemetryRailStore>((set, get) => ({
  entries: [],
  push: (level, source, message) => {
    const text = message.trim();
    if (!text) {
      return;
    }
    const now = Date.now();
    const entry: TelemetryRailEntry = {
      id: create_entry_id(),
      createdAtMs: now,
      isoTime: new Date(now).toISOString(),
      level,
      source: source.trim().slice(0, 28),
      message: text.slice(0, 400),
    };
    const next = [...get().entries, entry].slice(-MAX_ENTRIES);
    set({ entries: next });
  },
  clear: () => set({ entries: [] }),
}));
