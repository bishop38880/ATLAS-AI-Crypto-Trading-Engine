import { rollup_flat_category_scores_for_ui } from "./confluence-category-rollup";
import { CONFLUENCE_GATE_THRESHOLD_DEFAULT, CONFLUENCE_SCORE_CAP } from "./confluence-score-constants";
import { normalize_hmm_regime_token } from "./regime-badge-display";
import type {
  AgentStatus,
  ObtiSummaryLevel,
  PricePayload,
  ProviderHealth,
  ScoresPayload,
  SignalDecision,
  SystemHealth,
} from "../store/index";


/** True when the singleton WebSocket layer substitutes a JSON parse failure envelope for a malformed frame. */
export function is_ws_parse_failure_frame(raw: unknown): boolean {
  return typeof raw === "object" && raw !== null && "type" in raw && (raw as { type: unknown }).type === "parse_error";
}

export function calculate_normalized_display_score(totalRaw: number, cap = CONFLUENCE_SCORE_CAP): number {
  if (!Number.isFinite(totalRaw)) {
    return 0;
  }
  return Math.min(100, Math.max(0, Math.round((totalRaw / cap) * 100)));
}

export function normalize_backend_category(bucket: string): AgentStatus["category"] {
  const key = bucket.toLowerCase().trim();
  if (key.includes("deriv") || key === "derivatives") {
    return "DERIVATIVES";
  }
  if (key.includes("onchain") || key.includes("whale")) {
    return "ONCHAIN";
  }
  if (key.includes("sentiment") || key.includes("social")) {
    return "SENTIMENT";
  }
  if (key.includes("risk")) {
    return "RISK";
  }
  if (key.includes("correl")) {
    return "CORRELATION";
  }
  return "TECHNICAL";
}

export function normalize_agent_pulse_status(backendStatus: unknown): AgentStatus["status"] {
  const token = typeof backendStatus === "string" ? backendStatus.toUpperCase() : "";
  if (token === "GREEN" || token === "READY" || token === "WARMING_UP") {
    return "HEALTHY";
  }
  if (token === "YELLOW" || token === "DEGRADED") {
    return "DEGRADED";
  }
  if (token === "TIMEOUT" || token === "STALE") {
    return "TIMEOUT";
  }
  return "OFFLINE";
}

const PING_STALE_MS = 60_000;

/** Marks agents whose last ping exceeds the stale window as TIMEOUT. */
export function apply_agent_ping_timeout(agents: AgentStatus[]): AgentStatus[] {
  return agents.map((agent) =>
    agent.lastPingMs > PING_STALE_MS ? { ...agent, status: "TIMEOUT" as const } : agent,
  );
}

/** Pipeline stage map expects traffic-light pulses (legacy backend shape). */
export type PipelineAgentPulseUi = {
  name: string;
  status: "GREEN" | "YELLOW" | "RED";
  lastPingMs: number;
  category: string;
  lastScore?: number | null;
  maxPoints?: number | null;
  direction?: string | null;
  explanation?: string | null;
};

export function calculate_pulse_light_from_agent_status(agent: AgentStatus): PipelineAgentPulseUi["status"] {
  if (agent.veto) {
    return "RED";
  }
  if (agent.status === "HEALTHY") {
    return "GREEN";
  }
  if (agent.status === "DEGRADED" || agent.status === "TIMEOUT") {
    return "YELLOW";
  }
  return "RED";
}

export function map_agents_to_pipeline_pulse(agents: AgentStatus[]): PipelineAgentPulseUi[] {
  return agents.map((agent) => ({
    name: agent.name,
    status: calculate_pulse_light_from_agent_status(agent),
    lastPingMs: agent.lastPingMs,
    category: agent.category.toLowerCase(),
    lastScore: agent.lastScore,
    maxPoints: agent.maxPoints,
    direction: agent.veto ? "veto" : null,
    explanation: agent.veto ? "Agent veto active" : null,
  }));
}

const DB_DECISION_MAP: Record<string, SignalDecision> = {
  LONG: "Buy",
  SHORT: "Sell",
  FLAT: "Hold",
  REJECT: "No Position",
  HOLD: "Hold",
  BUY: "Buy",
  SELL: "Sell",
  "STRONG BUY": "Strong Buy",
  "STRONG SELL": "Strong Sell",
  "NO POSITION": "No Position",
};

export function coerce_signal_decision_label(raw: string, totalFallback: number): SignalDecision {
  const ladder = derive_signal_decision_from_total_score(totalFallback);
  const token = raw.trim().toUpperCase();

  /* Backend often emits FLAT/HOLD as a pipeline posture while totalScore still reflects the
   * confluence ladder. Align dashboard badges with the score bar by deriving from points. */
  if (token === "FLAT" || token === "HOLD") {
    return ladder;
  }

  if (token in DB_DECISION_MAP) {
    return DB_DECISION_MAP[token];
  }
  const spaced = raw.trim();
  if (
    spaced === "Strong Buy" ||
    spaced === "Buy" ||
    spaced === "Hold" ||
    spaced === "Sell" ||
    spaced === "Strong Sell" ||
    spaced === "No Position"
  ) {
    return spaced;
  }
  return ladder;
}

export function derive_signal_decision_from_total_score(total: number): SignalDecision {
  if (total >= 190) {
    return "Strong Buy";
  }
  if (total >= 160) {
    return "Buy";
  }
  if (total >= 130) {
    return "Hold";
  }
  if (total >= 95) {
    return "Sell";
  }
  if (total >= 40) {
    return "Strong Sell";
  }
  return "No Position";
}

function read_number(value: unknown, fallback = 0): number {
  if (typeof value === "number" && Number.isFinite(value)) {
    return value;
  }
  if (typeof value === "string" && value.trim().length > 0) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : fallback;
  }
  return fallback;
}

function read_string(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

function derive_risk_veto_flag(item: Record<string, unknown>): boolean {
  if (typeof item.veto === "boolean") {
    return item.veto;
  }
  const nameLower = read_string(item.name).toLowerCase();
  if (nameLower !== "risk") {
    return false;
  }
  const directionLower = read_string(item.direction).toLowerCase();
  const explanationLower = read_string(item.explanation).toLowerCase();
  return (
    directionLower === "veto" ||
    explanationLower.includes("veto") ||
    explanationLower.includes("blocked") ||
    explanationLower.includes("block ")
  );
}

function is_record(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function coerce_regime_label(input: string): SystemHealth["currentRegime"] {
  const token = input.toUpperCase();
  const bullHints = ["BULL", "TREND_UP", "TRENDING_UP", "BREAKOUT"];
  const bearHints = ["BEAR", "TREND_DOWN", "TRENDING_DOWN", "BREAKDOWN"];

  if (bullHints.some((hint) => token.includes(hint))) {
    return "BULL";
  }

  if (bearHints.some((hint) => token.includes(hint))) {
    return "BEAR";
  }

  if (token.includes("RANGE") || token.includes("SIDEWAYS")) {
    return "RANGING";
  }

  if (token.length === 0 || token === "UNKNOWN") {
    return "UNKNOWN";
  }

  return "RANGING";
}

function coerce_provider_state(raw: string): ProviderHealth["state"] {
  const v = raw.toUpperCase().replace(/\s+/g, "_");
  if (v === "DEGRADED") {
    return "DEGRADED";
  }
  if (v === "HALF_OPEN" || v === "HALF-OPEN") {
    return "HALF_OPEN";
  }
  if (v === "OPEN") {
    return "OPEN";
  }
  return "CLOSED";
}

/** Maps one provider health record from REST or WebSocket (camelCase or snake_case). */
export function map_provider_row(item: Record<string, unknown>): ProviderHealth | null {
  const name = read_string(item.name ?? item.providerName ?? item.provider ?? "");
  if (name.length === 0) {
    return null;
  }

  const p99 = Math.round(
    read_number(item.p99Ms ?? item.p99_ms ?? item.p99LatencyMs ?? item.p99_latency_ms),
  );
  const p50 = Math.round(read_number(item.p50Ms ?? item.p50_ms, p99));
  const p95 = Math.round(read_number(item.p95Ms ?? item.p95_ms, p99));

  const lastFailureRaw = item.lastFailureTs ?? item.last_failure_ts;
  const lastFailureTs =
    typeof lastFailureRaw === "string" && lastFailureRaw.length > 0 ? lastFailureRaw : null;

  return {
    name,
    tier: Math.max(1, Math.round(read_number(item.tier, 1))),
    trustRank: Math.max(1, Math.round(read_number(item.trustRank ?? item.trust_rank, 1))),
    state: coerce_provider_state(read_string(item.state ?? item.breakerState ?? item.breaker_state, "CLOSED")),
    healthScore: Math.min(1, Math.max(0, read_number(item.healthScore ?? item.health_score ?? 1, 1))),
    failureRate: Math.min(1, Math.max(0, read_number(item.failureRate ?? item.failure_rate, 0))),
    slowCallRate: Math.min(1, Math.max(0, read_number(item.slowCallRate ?? item.slow_call_rate, 0))),
    avgLatencyMs: Math.round(read_number(item.avgLatencyMs ?? item.avg_latency_ms)),
    p50Ms: p50,
    p95Ms: p95,
    p99Ms: p99,
    requestsPerMin: read_number(item.requestsPerMin ?? item.requests_per_min),
    rateLimitMax: Math.max(0, read_number(item.rateLimitMax ?? item.rate_limit_max)),
    lastFetchMs: read_number(item.lastFetchMs ?? item.last_fetch_ms),
    cacheStatus: read_string(item.cacheStatus ?? item.cache_status, "—"),
    windowSize: Math.max(0, Math.round(read_number(item.windowSize ?? item.window_size))),
    windowFailures: Math.max(0, Math.round(read_number(item.windowFailures ?? item.window_failures))),
    windowSlowCalls: Math.max(0, Math.round(read_number(item.windowSlowCalls ?? item.window_slow_calls))),
    lastFailureTs,
  };
}

export function map_agents_ws_payload(raw: unknown): AgentStatus[] {
  if (!Array.isArray(raw)) {
    return [];
  }

  const now = new Date().toISOString();
  const agents: AgentStatus[] = [];

  for (const item of raw) {
    if (!is_record(item)) {
      continue;
    }

    const name = read_string(item.name);
    if (name.length === 0) {
      continue;
    }

    const explanation = read_string(item.explanation ?? item.reason ?? "");
    const direction = read_string(item.direction ?? "");

    agents.push({
      name,
      category: normalize_backend_category(read_string(item.category, "TECHNICAL")),
      status: normalize_agent_pulse_status(item.status),
      lastScore: Math.round(read_number(item.lastScore ?? item.last_score)),
      maxPoints: Math.round(read_number(item.maxPoints ?? item.max_points, 75)),
      lastPingMs: Math.round(read_number(item.lastPingMs ?? item.last_ping_ms)),
      veto: derive_risk_veto_flag(item),
      lastUpdated: read_string(item.lastUpdated ?? item.last_updated, now),
      ...(explanation.length > 0 ? { explanation } : {}),
      ...(direction.length > 0 ? { direction } : {}),
    });
  }

  agents.sort((a, b) => a.name.localeCompare(b.name));

  return agents;
}

function read_obti_summary_level(record: Record<string, unknown>): ObtiSummaryLevel | null {
  const merged =
    record.obtiSummary ??
    record.obti_summary ??
    record.obtiSummaryFlat ??
    record.obti_summary_flat;
  if (merged === null || merged === undefined) {
    return null;
  }
  const token = typeof merged === "string" ? merged.toLowerCase().trim() : "";
  if (token === "balanced") {
    return null;
  }
  if (token === "low" || token === "moderate" || token === "extreme") {
    return token;
  }
  return null;
}

function read_veto_active(record: Record<string, unknown>): boolean {
  if (typeof record.vetoActive === "boolean") {
    return record.vetoActive;
  }
  if (typeof record.veto_active === "boolean") {
    return record.veto_active;
  }
  if (typeof record.executionVeto === "boolean") {
    return record.executionVeto;
  }
  if (typeof record.execution_veto === "boolean") {
    return record.execution_veto;
  }
  return false;
}

export function map_scores_ws_payload(raw: unknown): ScoresPayload {
  const now = new Date().toISOString();
  const record = is_record(raw) ? raw : {};

  const categoryRow = is_record(record.categoryScores) ? record.categoryScores : record.category_scores;
  const extracted: Record<string, number> = {};
  if (is_record(categoryRow)) {
    for (const [key, cell] of Object.entries(categoryRow)) {
      extracted[key] = read_number(cell);
    }
  }

  const asset = read_string(record.asset ?? record.symbol, "__aggregate__");

  const total_score = Math.round(read_number(record.totalScore ?? record.total_score));

  const marl_candidate = record.marlRevision ?? record.marl_revision;
  const marlRevision =
    marl_candidate === null || marl_candidate === undefined
      ? null
      : (() => {
          const n = read_number(marl_candidate, NaN);
          return Number.isFinite(n) ? Math.round(n) : null;
        })();

  const gate_threshold_rounded = Math.round(
    read_number(record.gateThreshold ?? record.gate_threshold, CONFLUENCE_GATE_THRESHOLD_DEFAULT),
  );

  let r1_llm_invoked: boolean | null = null;
  if (typeof record.r1LlmInvoked === "boolean") {
    r1_llm_invoked = record.r1LlmInvoked;
  } else if (typeof record.r1_llm_invoked === "boolean") {
    r1_llm_invoked = record.r1_llm_invoked;
  }

  const llm_latency_ms = read_number(record.llmLatencyMs ?? record.llm_latency_ms ?? record.r1LatencyMs ?? record.r1_latency_ms);
  const llm_latency_seconds =
    llm_latency_ms > 0
      ? Math.max(0, Math.round(llm_latency_ms / 100) / 10)
      : read_number(record.llmLatencySeconds ?? record.llm_latency_seconds ?? record.r1LatencySeconds ?? record.r1_latency_seconds);

  const llm_latency_seconds_normalized =
    llm_latency_seconds > 0 && Number.isFinite(llm_latency_seconds)
      ? Math.round(llm_latency_seconds * 10) / 10
      : null;

  const llm_model_label = read_string(
    record.llmTierLabel ??
      record.llm_tier_label ??
      record.llmModelLabel ??
      record.llm_model_label ??
      record.llmModel ??
      record.llm_model ??
      record.deepseekModel ??
      record.deepseek_model,
    "",
  );
  const cycle_timestamp = read_string(record.cycleTimestamp ?? record.cycle_timestamp ?? record.timestamp ?? record.cycleTs ?? record.cycle_ts, now);
  const cycle_candidate = record.cycleNumber ?? record.cycle_number;
  const cycle_number =
    cycle_candidate === null || cycle_candidate === undefined
      ? null
      : (() => {
          const n = read_number(cycle_candidate, NaN);
          return Number.isFinite(n) ? Math.round(n) : null;
        })();

  return {
    asset,
    totalScore: total_score,
    normalizedScore:
      typeof record.normalizedScore === "number"
        ? Math.round(record.normalizedScore)
        : calculate_normalized_display_score(total_score),
    decision:
      typeof record.decision === "string"
        ? coerce_signal_decision_label(record.decision, total_score)
        : derive_signal_decision_from_total_score(total_score),
    confidence: Math.min(
      1,
      Math.max(0, read_number(record.confidence ?? record.signalConfidence ?? record.signal_confidence, 0)),
    ),
    categoryScores: rollup_flat_category_scores_for_ui(extracted),
    passesGate: typeof record.passesGate === "boolean"
      ? record.passesGate
      : typeof record.passes_gate === "boolean"
        ? record.passes_gate
        : false,
    gateThreshold: gate_threshold_rounded,
    r1LlmInvoked: r1_llm_invoked,
    llmModelLabel: llm_model_label,
    llmTierLabel: llm_model_label,
    llmLatencySeconds: llm_latency_seconds_normalized,
    vetoActive: read_veto_active(record),
    obtiSummary: read_obti_summary_level(record),
    obtiSide:
      record.obtiSide === null || record.obti_side === null
        ? null
        : read_string(record.obtiSide ?? record.obti_side, "") || null,
    marlRevision,
    cycleNumber: cycle_number,
    cycleTimestamp: cycle_timestamp,
    cycleTs: cycle_timestamp,
    price: read_string(record.price, "0"),
    fundingRate: read_string(record.fundingRate ?? record.funding_rate, "0"),
    openInterest: read_string(record.openInterest ?? record.open_interest, "0"),
    reasoningSummary: read_string(
      record.reasoningSummary ?? record.reasoning_summary ?? record.entryThesis ?? record.entry_thesis,
    ),
  };
}

export function map_provider_ws_payload(raw: unknown): ProviderHealth[] {
  if (!Array.isArray(raw)) {
    return [];
  }

  const rows: ProviderHealth[] = [];

  for (const item of raw) {
    if (!is_record(item)) {
      continue;
    }

    const row = map_provider_row(item);
    if (row !== null) {
      rows.push(row);
    }
  }

  rows.sort((a, b) => a.name.localeCompare(b.name));

  return rows;
}

export function map_system_ws_payload(raw: unknown): SystemHealth {
  const record = is_record(raw) ? raw : {};
  const overall = read_string(record.overallStatus ?? record.overall_status, "DEGRADED");
  let overallNormalized: SystemHealth["overallStatus"] = "DEGRADED";
  const compact = overall.toUpperCase().replace(/\s+/g, "_");
  if (compact.includes("HALT")) {
    overallNormalized = "HALTED";
  } else if (compact.includes("HEALTH")) {
    overallNormalized = "HEALTHY";
  }

  const pnl_blob = record.portfolioPnl24h ?? record.portfolio_pnl_24h ?? "0";
  const pnlString =
    typeof pnl_blob === "number" && Number.isFinite(pnl_blob) ? String(pnl_blob) : read_string(pnl_blob, "0");

  const raw_regime = read_string(record.currentRegime ?? record.current_regime, "UNKNOWN");
  const native_hmm = normalize_hmm_regime_token(raw_regime);

  const cycleRaw =
    record.cycleCount ?? record.cycle_count ?? record.cycles ?? record.cycle_count_raw;
  const btcPriceRaw = read_string(record.btcPrice ?? record.btc_price, "");
  const btcChangeRaw = read_string(record.btcChange24h ?? record.btc_change_24h ?? record.btcChange, "");

  const autonomous_raw = record.autonomousEngineActive ?? record.autonomous_engine_active;

  const regime_asset = read_string(record.regimeContextAsset ?? record.regime_context_asset, "");
  const regime_tf = read_string(record.regimeContextTimeframe ?? record.regime_context_timeframe, "");
  const regime_as_of = read_string(record.regimeContextAsOf ?? record.regime_context_as_of, "");
  const regime_runner = read_string(record.regimeContextRunnerUp ?? record.regime_context_runner_up, "");
  const regime_explain = read_string(record.regimeContextExplanation ?? record.regime_context_explanation, "");
  const regime_hint = read_string(record.regimeContextTransitionHint ?? record.regime_context_transition_hint, "");
  const duration_bars_raw = record.regimeContextDurationBars ?? record.regime_context_duration_bars;

  return {
    overallStatus: overallNormalized,
    currentRegime: coerce_regime_label(raw_regime),
    regimeConfidence: read_number(record.regimeConfidence ?? record.regime_confidence),
    activeCycle: Boolean(record.activeCycle ?? record.active_cycle),
    nextCycleSeconds: Math.max(0, Math.round(read_number(record.nextCycleSeconds ?? record.next_cycle_seconds))),
    openPositions: Math.min(24, Math.max(0, Math.round(read_number(record.openPositions ?? record.open_positions)))),
    portfolioPnl24h: pnlString,
    ...(cycleRaw !== undefined && cycleRaw !== null
      ? { cycleCount: Math.round(read_number(cycleRaw)) }
      : {}),
    ...(btcPriceRaw.length > 0 ? { btcPrice: btcPriceRaw } : {}),
    ...(btcChangeRaw.length > 0 ? { btcChange24h: btcChangeRaw } : {}),
    ...(typeof autonomous_raw === "boolean" ? { autonomousEngineActive: autonomous_raw } : {}),
    ...(regime_asset.length > 0 ? { regimeContextAsset: regime_asset } : {}),
    ...(regime_tf.length > 0 ? { regimeContextTimeframe: regime_tf } : {}),
    ...(regime_as_of.length > 0 ? { regimeContextAsOf: regime_as_of } : {}),
    ...(regime_runner.length > 0 ? { regimeContextRunnerUp: regime_runner } : {}),
    ...(regime_explain.length > 0 ? { regimeContextExplanation: regime_explain } : {}),
    ...(regime_hint.length > 0 ? { regimeContextTransitionHint: regime_hint } : {}),
    ...(duration_bars_raw !== undefined && duration_bars_raw !== null && Number.isFinite(Number(duration_bars_raw))
      ? { regimeContextDurationBars: Math.max(0, Math.round(Number(duration_bars_raw))) }
      : {}),
    ...(native_hmm !== null ? { hmmRegimeNative: native_hmm } : {}),
  };
}

export function format_decimal_like_string_for_display(raw: unknown, fallback = "0"): string {
  if (typeof raw === "number" && Number.isFinite(raw)) {
    return `${raw}`;
  }
  const stringValue = typeof raw === "string" ? raw : fallback;
  return stringValue.length > 0 ? stringValue : fallback;
}

function normalize_legacy_price_payload(value: unknown): PricePayload | null {
  const record = is_record(value) ? value : null;
  if (!record || typeof record.symbol !== "string") {
    return null;
  }

  return {
    symbol: record.symbol,
    price: read_string(record.price, "0"),
    change24h: format_decimal_like_string_for_display(record.change_24h, "0"),
    volume24h: read_string(record.volume_24h ?? record.volume24h, "0"),
    fundingRate: read_string(record.funding_rate ?? record.fundingRate, "—"),
    openInterest: read_string(record.open_interest ?? record.openInterest, "—"),
    lastUpdated: read_string(record.timestamp ?? record.lastUpdated, new Date().toISOString()),
  };
}

function normalize_single_price_payload(value: unknown): PricePayload | null {
  const record = is_record(value) ? value : null;
  if (!record || typeof record.symbol !== "string") {
    return null;
  }

  return {
    symbol: record.symbol,
    price: read_string(record.price, "0"),
    change24h: format_decimal_like_string_for_display(record.change24h ?? record.change_24h, "0"),
    volume24h: read_string(record.volume24h ?? record.volume_24h, "0"),
    fundingRate: read_string(record.funding_rate ?? record.fundingRate, "—"),
    openInterest: read_string(record.open_interest ?? record.openInterest, "—"),
    lastUpdated: read_string(record.timestamp ?? record.lastUpdated, new Date().toISOString()),
  };
}

export function map_prices_ws_payload(raw: unknown): PricePayload[] {
  if (Array.isArray(raw)) {
    return raw.map(normalize_single_price_payload).filter((cell): cell is PricePayload => cell !== null);
  }

  const record = is_record(raw) ? raw : null;
  if (record !== null && Array.isArray(record.updates)) {
    return record.updates.map(normalize_legacy_price_payload).filter((cell): cell is PricePayload => cell !== null);
  }

  return [];
}
