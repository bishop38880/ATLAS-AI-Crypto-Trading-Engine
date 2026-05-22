/**
 * Singleton WebSocket manager — multiplexed subscriptions per logical channel path.
 * Components NEVER create raw WebSocket connections outside this module.
 *
 * Channels (path resolution):
 *   /ws/agents, /ws/scores, /ws/prices, /ws/providers, /ws/system, /ws/confluence, …
 *
 * Messages are forwarded as decoded JSON (`unknown`).
 */

import { wsUrl } from "./url";

const RECONNECT_DELAY_MS = 2_500;

export type ManagedWsChannel =
  | "agents"
  | "scores"
  | "prices"
  | "providers"
  | "activity"
  | "positions"
  | "system"
  | "confluence";

/** Resolved WS path for the confluence channel (Vite env override supported). */
export function getConfluenceRelativePath(): string {
  const fromEnv = import.meta.env.VITE_CONFLUENCE_WS_PATH?.trim();
  return fromEnv && fromEnv.length > 0 ? fromEnv : "/ws/confluence";
}

const CHANNEL_PATHS: Record<ManagedWsChannel, () => string> = {
  agents: () => "/ws/agents",
  scores: () => "/ws/scores?asset=ALL",
  prices: () => "/ws/prices",
  providers: () => "/ws/providers",
  activity: () => "/ws/activity",
  positions: () => "/ws/positions",
  system: () => "/ws/system",
  confluence: () => getConfluenceRelativePath(),
};

type Listener = (data: unknown) => void;

interface ChannelBinding {
  relativePath: string;
  socket: WebSocket | null;
  listeners: Map<number, Listener>;
  /** Invoked when the socket reaches OPEN (and immediately if subscribe while already OPEN). */
  onOpenCallbacks: Set<() => void>;
  reconnectTimer: ReturnType<typeof setTimeout> | null;
  shouldReconnect: boolean;
  nextListenerId: number;
}

export class WebSocketManager {
  private readonly bindings = new Map<string, ChannelBinding>();

  private ensureBinding(channelKey: string, relativePath: string): ChannelBinding {
    let binding = this.bindings.get(channelKey);
    if (!binding) {
      binding = {
        relativePath,
        socket: null,
        listeners: new Map(),
        onOpenCallbacks: new Set(),
        reconnectTimer: null,
        shouldReconnect: true,
        nextListenerId: 0,
      };
      this.bindings.set(channelKey, binding);
    }
    return binding;
  }

  private clearReconnectTimer(binding: ChannelBinding): void {
    if (binding.reconnectTimer !== null) {
      clearTimeout(binding.reconnectTimer);
      binding.reconnectTimer = null;
    }
  }

  private fanOut(binding: ChannelBinding, data: unknown): void {
    for (const listener of binding.listeners.values()) {
      listener(data);
    }
  }

  private invokeOpenCallbacks(binding: ChannelBinding): void {
    for (const callback of binding.onOpenCallbacks) {
      callback();
    }
  }

  private openSocket(channelKey: string, binding: ChannelBinding): void {
    this.clearReconnectTimer(binding);
    const normalized = binding.relativePath.startsWith("/")
      ? binding.relativePath
      : `/${binding.relativePath}`;
    const url = wsUrl(normalized);

    try {
      const socket = new WebSocket(url);
      binding.socket = socket;

      socket.addEventListener("open", () => {
        this.invokeOpenCallbacks(binding);
      });

      socket.addEventListener("message", (event) => {
        try {
          const parsed: unknown = JSON.parse(event.data as string);
          this.fanOut(binding, parsed);
        } catch {
          this.fanOut(binding, { type: "parse_error", raw: event.data });
        }
      });

      socket.addEventListener("close", () => {
        binding.socket = null;
        if (binding.shouldReconnect && binding.listeners.size > 0) {
          binding.reconnectTimer = setTimeout(() => {
            this.openSocket(channelKey, binding);
          }, RECONNECT_DELAY_MS);
        }
      });
    } catch {
      if (binding.shouldReconnect && binding.listeners.size > 0) {
        binding.reconnectTimer = setTimeout(() => {
          this.openSocket(channelKey, binding);
        }, RECONNECT_DELAY_MS);
      }
    }
  }

  /**
   * Subscribe to a managed channel. Returns unsubscribe — call on unmount.
   */
  connect(
    channel: ManagedWsChannel,
    onMessage: (data: unknown) => void,
    onOpen?: () => void,
  ): () => void {
    const relativePath = CHANNEL_PATHS[channel]();
    const binding = this.ensureBinding(channel, relativePath);
    binding.relativePath = relativePath;
    binding.shouldReconnect = true;

    const listenerId = binding.nextListenerId;
    binding.nextListenerId += 1;
    binding.listeners.set(listenerId, onMessage);

    if (onOpen) {
      binding.onOpenCallbacks.add(onOpen);
    }

    const ready = binding.socket?.readyState;
    if (ready === WebSocket.OPEN) {
      if (onOpen) {
        queueMicrotask(() => onOpen());
      }
    } else if (binding.socket === null || ready === WebSocket.CLOSED) {
      this.openSocket(channel, binding);
    }

    return () => {
      binding.listeners.delete(listenerId);
      if (onOpen) {
        binding.onOpenCallbacks.delete(onOpen);
      }
      if (binding.listeners.size === 0) {
        binding.shouldReconnect = false;
        this.clearReconnectTimer(binding);
        binding.socket?.close();
        binding.socket = null;
        this.bindings.delete(channel);
      }
    };
  }

  subscribe(
    channel: ManagedWsChannel,
    onMessage: (data: unknown) => void,
    onOpen?: () => void,
  ): () => void {
    return this.connect(channel, onMessage, onOpen);
  }

  unsubscribe(channel: string): void {
    this.disconnect(channel);
  }

  /**
   * Subscribe to an arbitrary path (one socket per `connectionKey`).
   */
  connectPath(
    connectionKey: string,
    relativePath: string,
    onMessage: (data: unknown) => void,
    onOpen?: () => void,
  ): () => void {
    const binding = this.ensureBinding(connectionKey, relativePath);
    binding.relativePath = relativePath;
    binding.shouldReconnect = true;

    const listenerId = binding.nextListenerId;
    binding.nextListenerId += 1;
    binding.listeners.set(listenerId, onMessage);

    if (onOpen) {
      binding.onOpenCallbacks.add(onOpen);
    }

    const ready = binding.socket?.readyState;
    if (ready === WebSocket.OPEN) {
      if (onOpen) {
        queueMicrotask(() => onOpen());
      }
    } else if (binding.socket === null || ready === WebSocket.CLOSED) {
      this.openSocket(connectionKey, binding);
    }

    return () => {
      binding.listeners.delete(listenerId);
      if (onOpen) {
        binding.onOpenCallbacks.delete(onOpen);
      }
      if (binding.listeners.size === 0) {
        binding.shouldReconnect = false;
        this.clearReconnectTimer(binding);
        binding.socket?.close();
        binding.socket = null;
        this.bindings.delete(connectionKey);
      }
    };
  }

  disconnect(channel: string): void {
    const binding = this.bindings.get(channel);
    if (!binding) {
      return;
    }
    binding.shouldReconnect = false;
    this.clearReconnectTimer(binding);
    binding.listeners.clear();
    binding.onOpenCallbacks.clear();
    binding.socket?.close();
    binding.socket = null;
    this.bindings.delete(channel);
  }

  disconnectAll(): void {
    for (const key of [...this.bindings.keys()]) {
      this.disconnect(key);
    }
  }

  getState(channel: string): "CONNECTING" | "OPEN" | "CLOSING" | "CLOSED" | "NONE" {
    const binding = this.bindings.get(channel);
    if (!binding || binding.socket === null) {
      return "NONE";
    }
    switch (binding.socket.readyState) {
      case WebSocket.CONNECTING:
        return "CONNECTING";
      case WebSocket.OPEN:
        return "OPEN";
      case WebSocket.CLOSING:
        return "CLOSING";
      case WebSocket.CLOSED:
        return "CLOSED";
      default:
        return "NONE";
    }
  }

  getStatus(channel: string): "CONNECTING" | "OPEN" | "CLOSING" | "CLOSED" | "NONE" {
    return this.getState(channel);
  }
}

export const wsManager = new WebSocketManager();
