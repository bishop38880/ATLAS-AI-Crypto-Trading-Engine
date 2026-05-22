import type { SignalFeedRow } from "./signal-feed-mapper";

/** Builds RFC4180-style CSV for the signal feed export button. */
export function calculate_signal_feed_csv(rows: readonly SignalFeedRow[]): string {
  const headers = [
    "asset",
    "timestamp",
    "decision",
    "totalScore220",
    "normalizedScore",
    "confidence",
    "passesGate",
    "gateThreshold",
    "obtiSummary",
    "obtiSide",
    "price",
    "change24h",
  ];

  const escape = (cell: string): string => {
    if (/[",\n\r]/.test(cell)) {
      return `"${cell.replace(/"/g, '""')}"`;
    }
    return cell;
  };

  const lines = [headers.join(",")];

  for (const row of rows) {
    lines.push(
      [
        escape(row.asset),
        escape(row.timestamp),
        escape(row.decision),
        escape(String(row.totalScore)),
        escape(String(row.normalizedScore)),
        escape(String(row.confidence)),
        escape(row.passesGate ? "true" : "false"),
        escape(String(row.gateThreshold)),
        escape(row.obtiSummary ?? ""),
        escape(row.obtiSide ?? ""),
        escape(row.price),
        escape(row.change24h),
      ].join(","),
    );
  }

  return `${lines.join("\n")}\n`;
}
