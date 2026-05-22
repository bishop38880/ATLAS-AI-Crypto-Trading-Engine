import { useEffect } from "react";

import { getConfluenceRelativePath, wsManager } from "../lib/ws-manager";
import { useConfluenceStore } from "../stores/confluenceStore";
import { parse_confluence_frame } from "../types/confluence-guard";

function normalize_confluence_path(raw: string): string {
  const trimmed = raw.trim();
  return trimmed.startsWith("/") ? trimmed : `/${trimmed}`;
}

/**
 * Live confluence frames via {@link wsManager} (never a raw browser WebSocket).
 */
export function useConfluenceSocket(pathOverride?: string): void {
  const ingestFrame = useConfluenceStore((state) => state.ingestFrame);
  const setError = useConfluenceStore((state) => state.setError);
  const setStatus = useConfluenceStore((state) => state.setStatus);

  useEffect(() => {
    const relativePath = pathOverride?.trim().length
      ? normalize_confluence_path(pathOverride)
      : getConfluenceRelativePath();
    const connectionKey = `confluence:${relativePath}`;

    setStatus("connecting");

    const handler = (data: unknown) => {
      const parsed = parse_confluence_frame(data);
      if (!parsed) {
        setError("Received malformed confluence frame");
        return;
      }
      setError(null);
      ingestFrame(parsed);
    };

    const unsubscribe = wsManager.connectPath(connectionKey, relativePath, handler);

    const pollId = window.setInterval(() => {
      const state = wsManager.getState(connectionKey);
      if (state === "OPEN") {
        setStatus("open");
      } else if (state === "CONNECTING") {
        setStatus("connecting");
      } else if (state === "CLOSED" || state === "CLOSING") {
        setStatus("closed");
      }
    }, 400);

    return () => {
      window.clearInterval(pollId);
      unsubscribe();
      setStatus("closed");
    };
  }, [ingestFrame, pathOverride, setError, setStatus]);
}
