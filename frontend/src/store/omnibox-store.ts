import { create } from "zustand";

import type {
  OmniBoxRouteKey,
  SourceCitationPayload,
} from "../types/omnibox-api";

export interface OmniBoxUserMessage {
  id: string;
  role: "user";
  body: string;
  asset: string;
  routeLabel: string;
  ts: string;
}

export interface OmniBoxAssistantMessage {
  id: string;
  role: "assistant";
  body: string;
  ts: string;
  streaming: boolean;
  ariaLive: "off" | "polite";
  route?: OmniBoxRouteKey;
  classificationConfidence?: number;
  classificationRationale?: string;
  sources: SourceCitationPayload[];
  llmTier?: string;
  latencyMs?: number;
  liveDataSummary?: string | null;
}

export type OmniBoxMessage = OmniBoxUserMessage | OmniBoxAssistantMessage;

interface OmniBoxChatStore {
  messages: OmniBoxMessage[];
  clearMessages: () => void;
  appendUserMessage: (
    body: string,
    asset: string,
    routeLabel: string,
  ) => void;
  appendAssistantShell: () => string;
  patchAssistantMessage: (
    id: string,
    patch: Partial<Omit<OmniBoxAssistantMessage, "id" | "role">>,
  ) => void;
}

function new_iso_timestamp(): string {
  return new Date().toISOString();
}

export const useOmniBoxStore = create<OmniBoxChatStore>((set) => ({
  messages: [],
  clearMessages: () => set({ messages: [] }),
  appendUserMessage: (body, asset, routeLabel) =>
    set((state) => ({
      messages: [
        ...state.messages,
        {
          id: crypto.randomUUID(),
          role: "user",
          body,
          asset,
          routeLabel,
          ts: new_iso_timestamp(),
        },
      ],
    })),
  appendAssistantShell: () => {
    const id = crypto.randomUUID();
    set((state) => ({
      messages: [
        ...state.messages,
        {
          id,
          role: "assistant",
          body: "",
          ts: new_iso_timestamp(),
          streaming: true,
          ariaLive: "off",
          sources: [],
        },
      ],
    }));
    return id;
  },
  patchAssistantMessage: (id, patch) =>
    set((state) => ({
      messages: state.messages.map((message) => {
        if (message.id !== id || message.role !== "assistant") {
          return message;
        }
        return { ...message, ...patch };
      }),
    })),
}));
