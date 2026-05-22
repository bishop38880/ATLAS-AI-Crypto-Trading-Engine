import { useEffect, useRef } from "react";

import { apiUrl } from "../../lib/url";
import { type TerminalLogEntry, useExecutiveStore } from "../../store/executiveStore";

function levelClass(level: string): string {
  switch (level.toUpperCase()) {
    case "INFO":
      return "text-blue-400";
    case "WARN":
      return "text-orange-400";
    case "ERROR":
      return "text-red-500";
    case "SUCCESS":
      return "text-green-400";
    default:
      return "text-gray-400";
  }
}

function parseTelemetryPayload(raw: string): Omit<TerminalLogEntry, "id"> | null {
  try {
    const value: unknown = JSON.parse(raw);
    if (
      typeof value !== "object" ||
      value === null ||
      !("timestamp" in value) ||
      !("level" in value) ||
      !("agent" in value) ||
      !("message" in value)
    ) {
      return null;
    }
    const record = value as Record<string, unknown>;
    const timestamp = record.timestamp;
    const level = record.level;
    const agent = record.agent;
    const message = record.message;
    if (
      typeof timestamp !== "string" ||
      typeof level !== "string" ||
      typeof agent !== "string" ||
      typeof message !== "string"
    ) {
      return null;
    }
    return { timestamp, level, agent, message };
  } catch {
    return null;
  }
}

export function AITerminal() {
  const terminalLogs = useExecutiveStore((s) => s.terminalLogs);
  const addTerminalLog = useExecutiveStore((s) => s.addTerminalLog);
  const clearTerminalLogs = useExecutiveStore((s) => s.clearTerminalLogs);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [terminalLogs]);

  useEffect(() => {
    const streamUrl = apiUrl("/api/telemetry/stream");
    const es = new EventSource(streamUrl);

    es.onmessage = (event: MessageEvent<string>) => {
      const parsed = parseTelemetryPayload(event.data);
      if (parsed === null) {
        return;
      }
      const entry: TerminalLogEntry = {
        id:
          typeof crypto !== "undefined" && typeof crypto.randomUUID === "function"
            ? crypto.randomUUID()
            : `${parsed.timestamp}-${parsed.agent}-${parsed.message.slice(0, 24)}`,
        ...parsed,
      };
      addTerminalLog(entry);
    };

    return () => {
      es.close();
    };
  }, [addTerminalLog]);

  return (
    <section
      className="overflow-hidden rounded-xl border border-[color-mix(in_oklab,var(--border)_85%,transparent)] bg-black"
      aria-label="ATLAS live telemetry terminal"
    >
      <header className="sticky top-0 z-10 flex items-center justify-between border-b border-white/10 bg-gray-950 px-4 py-2">
        <h2 className="font-mono text-xs font-semibold tracking-wide text-emerald-400/90">
          ATLAS-CORE // LIVE TELEMETRY
        </h2>
        <button
          type="button"
          className="font-mono text-[10px] uppercase tracking-wide text-slate-500 hover:text-slate-300"
          onClick={() => clearTerminalLogs()}
        >
          Clear
        </button>
      </header>
      <div className="max-h-72 overflow-y-auto px-3 py-2 font-mono text-xs leading-relaxed">
        {terminalLogs.length === 0 ? (
          <p className="text-slate-600">Awaiting telemetry stream…</p>
        ) : (
          terminalLogs.map((log) => (
            <div key={log.id} className={`whitespace-pre-wrap break-words ${levelClass(log.level)}`}>
              [{log.timestamp}] [{log.agent}] {log.message}
            </div>
          ))
        )}
        <div ref={bottomRef} aria-hidden />
      </div>
    </section>
  );
}
