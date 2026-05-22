import type { AgentResult, ConfluenceStreamData } from "./confluence";

function is_record(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function is_agent_result(value: unknown): value is AgentResult {
  return (
    is_record(value) &&
    typeof value.score === "number" &&
    typeof value.weight === "number" &&
    is_record(value.sub_signals)
  );
}

export function parse_confluence_frame(value: unknown): ConfluenceStreamData | null {
  if (!is_record(value)) {
    return null;
  }

  if (
    typeof value.asset !== "string" ||
    typeof value.timestamp !== "number" ||
    typeof value.total_score !== "number" ||
    !is_record(value.agents)
  ) {
    return null;
  }

  const agents = value.agents;
  const requiredAgents = [
    "DerivativesAgent",
    "WhaleWatcherAgent",
    "TechnicalAgent",
    "SocialAgent",
    "MacroAgent",
  ] as const;

  const valid = requiredAgents.every((agentName) => {
    return is_agent_result(agents[agentName]);
  });

  if (!valid) {
    return null;
  }

  return value as unknown as ConfluenceStreamData;
}
