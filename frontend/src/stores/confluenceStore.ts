import { create } from "zustand";

import type { ConfluenceStreamData, ConnectionStatus } from "../types/confluence";

interface ConfluenceState {
  latest: ConfluenceStreamData | null;
  history: ConfluenceStreamData[];
  status: ConnectionStatus;
  error: string | null;
  lastUpdatedAt: number | null;
  setStatus: (status: ConnectionStatus) => void;
  setError: (error: string | null) => void;
  ingestFrame: (frame: ConfluenceStreamData) => void;
  reset: () => void;
}

const MAX_HISTORY_LENGTH = 30;

export const useConfluenceStore = create<ConfluenceState>((set) => ({
  latest: null,
  history: [],
  status: "closed",
  error: null,
  lastUpdatedAt: null,
  setStatus: (status) => set({ status }),
  setError: (error) => set({ error }),
  ingestFrame: (frame) =>
    set((state) => ({
      latest: frame,
      history: [frame, ...state.history].slice(0, MAX_HISTORY_LENGTH),
      status: "open",
      error: null,
      lastUpdatedAt: Date.now(),
    })),
  reset: () =>
    set({
      latest: null,
      history: [],
      status: "closed",
      error: null,
      lastUpdatedAt: null,
    }),
}));
