/**
 * POLARIS URL resolution — all API and WebSocket URLs go through these helpers.
 * HARD RULE: No hardcoded WebSocket schemes or undisclosed API hosts in components.
 * These helpers read from import.meta.env so Vite injects bases for dev proxies and production ingress.
 */

function devUsesAbsoluteApiWs(): boolean {
  const flag = import.meta.env.VITE_DEV_ABSOLUTE_API?.trim().toLowerCase() ?? "";
  return flag === "true" || flag === "1" || flag === "on";
}

/** In dev, default to same-origin `/api` + `/ws` (Vite proxy) unless explicitly opted out. */
export function resolveApiBase(): string {
  if (import.meta.env.DEV && !devUsesAbsoluteApiWs()) {
    return "";
  }
  return import.meta.env.VITE_API_BASE_URL ?? "";
}

export function resolveWsBase(): string {
  if (import.meta.env.DEV && !devUsesAbsoluteApiWs()) {
    const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
    return `${proto}//${window.location.host}`;
  }
  const envWs = import.meta.env.VITE_WS_BASE_URL?.trim() ?? "";
  if (envWs.length > 0) {
    return envWs;
  }
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${window.location.host}`;
}

export function apiUrl(path: string): string {
  return `${resolveApiBase()}${path}`;
}

export function wsUrl(path: string): string {
  return `${resolveWsBase()}${path}`;
}
