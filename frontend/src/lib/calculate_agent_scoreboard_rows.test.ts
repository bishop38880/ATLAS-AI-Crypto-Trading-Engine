import { describe, expect, it } from "vitest";

import type { AgentStatus } from "../store/index";
import {
  calculate_agent_scoreboard_rows,
  calculate_confluence_subtotal_score,
} from "./calculate_agent_scoreboard_rows";

function agent(partial: Partial<AgentStatus> & Pick<AgentStatus, "name">): AgentStatus {
  return {
    category: "TECHNICAL",
    status: "HEALTHY",
    lastScore: 0,
    maxPoints: 10,
    lastPingMs: 12,
    veto: false,
    lastUpdated: "2026-05-13T00:00:00Z",
    ...partial,
  };
}

describe("calculate_agent_scoreboard_rows", () => {
  it("maps Redis-style names into catalog slots without double-consuming rows", () => {
    const rows = calculate_agent_scoreboard_rows([
      agent({ name: "DerivativesAgent", category: "DERIVATIVES", lastScore: 64, maxPoints: 75 }),
      agent({ name: "TechnicalAgent", category: "TECHNICAL", lastScore: 38, maxPoints: 45 }),
      agent({ name: "WhaleWatcherAgent", category: "ONCHAIN", lastScore: 42, maxPoints: 50 }),
      agent({ name: "SocialAgent", category: "SENTIMENT", lastScore: 24, maxPoints: 30 }),
      agent({ name: "MacroAgent", category: "TECHNICAL", lastScore: 16, maxPoints: 20 }),
      agent({ name: "risk", category: "RISK", veto: true, explanation: "Portfolio veto armed" }),
    ]);

    expect(rows.filter((row) => row.catalog.role === "CONFLUENCE")).toHaveLength(5);

    const derivatives = rows.find((row) => row.catalog.id === "derivatives");
    expect(derivatives?.score).toBe(64);

    const risk = rows.find((row) => row.catalog.role === "VETO");
    expect(risk?.vetoBlocking).toBe(true);
  });

  it("sums only CONFLUENCE rows into the sacred ladder numerator", () => {
    const rows = calculate_agent_scoreboard_rows([
      agent({ name: "DerivativesAgent", category: "DERIVATIVES", lastScore: 10, maxPoints: 75 }),
      agent({ name: "LiquidationAgent", category: "DERIVATIVES", lastScore: 99, maxPoints: 15 }),
    ]);

    expect(calculate_confluence_subtotal_score(rows)).toBe(10);
  });
});
