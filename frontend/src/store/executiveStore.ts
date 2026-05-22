import { create } from "zustand";

const TERMINAL_LOG_CAP = 200;

export interface TerminalLogEntry {
  id: string;
  timestamp: string;
  level: string;
  agent: string;
  message: string;
}

interface ExecutiveStore {
  terminalLogs: TerminalLogEntry[];
  addTerminalLog: (log: TerminalLogEntry) => void;
  clearTerminalLogs: () => void;
}

export const useExecutiveStore = create<ExecutiveStore>((set) => ({
  terminalLogs: [],
  addTerminalLog: (log) =>
    set((state) => ({
      terminalLogs: [...state.terminalLogs, log].slice(-TERMINAL_LOG_CAP),
    })),
  clearTerminalLogs: () => set({ terminalLogs: [] }),
}));
