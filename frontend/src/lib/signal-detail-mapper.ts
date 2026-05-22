import { rollup_flat_category_scores_for_ui } from "./confluence-category-rollup";

export interface DetailCategoryScores {
  derivatives: number;
  onchain: number;
  technical: number;
  sentiment: number;
  marketContext: number;
}

export interface ObtiDetailView {
  side: string | null;
  level: string;
  obtiBid: number;
  obtiAsk: number;
  moderateThreshold: number;
  extremeThreshold: number;
  samples: number;
  minSamples: number;
  isWarmingUp: boolean;
  history: number[];
  lastBookUpdateMs: number;
}

export interface FracDiffView {
  optimalOrder: number;
  memoryPreserved: number;
  adfPValue: number;
  isStationary: boolean;
  lastCalibrated: string;
}

export interface OptionsFlowView {
  provider: Record<string, string>;
  dvol: number | null;
  atmIv: number | null;
  skew25d: number | null;
  vrp: number | null;
  callPutOiRatio: string | null;
  regime: string | null;
  unusualActivity: string | null;
}

export interface ExchangeFlowView {
  provider: Record<string, string>;
  netFlow24h: string | null;
  flowZScore: number | null;
  flowTrend: string | null;
  stablecoinReserves: string | null;
  signal: string | null;
}

export interface AgentBreakdownCellView {
  score: number;
  maxScore: number;
  weight: number;
  direction: string | null;
  explanation: string;
  convergences: string[];
  risks: string[];
  veto: boolean;
  subSignals: Record<string, Record<string, unknown>>;
}

export interface SignalDetailViewModel {
  asset: string;
  price: string;
  change24h: string;
  cycleNumber: number;
  cycleTs: string;
  totalScore: number;
  normalizedScore: number;
  decision: string;
  confidence: number;
  passesGate: boolean;
  gateThreshold: number;
  llmTierLabel: string | null;
  categoryScores: DetailCategoryScores;
  categoryMaxScores: Record<string, number>;
  agentBreakdown: Record<string, AgentBreakdownCellView>;
  obtiDetail: ObtiDetailView | null;
  fracDiff: FracDiffView | null;
  optionsFlow: OptionsFlowView | null;
  exchangeFlow: ExchangeFlowView | null;
}

function is_record(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
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

function read_optional_string(value: unknown): string | null {
  if (value === null || value === undefined) {
    return null;
  }
  const s = typeof value === "string" ? value.trim() : String(value).trim();
  return s.length > 0 ? s : null;
}

function normalize_category_scores(raw: Record<string, unknown>): DetailCategoryScores {
  const extracted: Record<string, number> = {};
  for (const [key, cell] of Object.entries(raw)) {
    extracted[key] = read_number(cell);
  }

  return rollup_flat_category_scores_for_ui(extracted);
}

function parse_obti_detail(raw: unknown): ObtiDetailView | null {
  if (!is_record(raw)) {
    return null;
  }
  const histRaw = raw.history;
  const history: number[] = [];
  if (Array.isArray(histRaw)) {
    for (const cell of histRaw) {
      history.push(read_number(cell));
    }
  }

  const sideRaw = raw.side;
  const sideToken =
    typeof sideRaw === "string" && (sideRaw === "bid" || sideRaw === "ask") ? sideRaw : null;

  return {
    side: sideToken,
    level: read_string(raw.level, "balanced").toLowerCase(),
    obtiBid: read_number(raw.obtiBid ?? raw.obti_bid),
    obtiAsk: read_number(raw.obtiAsk ?? raw.obti_ask),
    moderateThreshold: read_number(raw.moderateThreshold ?? raw.moderate_threshold),
    extremeThreshold: read_number(raw.extremeThreshold ?? raw.extreme_threshold),
    samples: Math.round(read_number(raw.samples)),
    minSamples: Math.max(1, Math.round(read_number(raw.minSamples ?? raw.min_samples, 30))),
    isWarmingUp: Boolean(raw.isWarmingUp ?? raw.is_warming_up),
    history,
    lastBookUpdateMs: Math.round(read_number(raw.lastBookUpdateMs ?? raw.last_book_update_ms)),
  };
}

function parse_frac_diff(raw: unknown): FracDiffView | null {
  if (!is_record(raw)) {
    return null;
  }
  return {
    optimalOrder: read_number(raw.optimalOrder ?? raw.optimal_order),
    memoryPreserved: read_number(raw.memoryPreserved ?? raw.memory_preserved),
    adfPValue: read_number(raw.adfPValue ?? raw.adf_p_value),
    isStationary: Boolean(raw.isStationary ?? raw.is_stationary),
    lastCalibrated: read_string(raw.lastCalibrated ?? raw.last_calibrated),
  };
}

function parse_str_dict(raw: unknown): Record<string, string> | null {
  if (!is_record(raw)) {
    return null;
  }
  const out: Record<string, string> = {};
  for (const [k, v] of Object.entries(raw)) {
    out[String(k)] = String(v);
  }
  return out;
}

function parse_options_flow(raw: unknown): OptionsFlowView | null {
  if (!is_record(raw)) {
    return null;
  }
  const provider = parse_str_dict(raw.provider);
  if (provider === null) {
    return null;
  }
  const cpRaw = raw.callPutOiRatio ?? raw.call_put_oi_ratio;
  return {
    provider,
    dvol: raw.dvol === null || raw.dvol === undefined ? null : read_number(raw.dvol),
    atmIv:
      raw.atmIv === null || raw.atmIv === undefined
        ? raw.atm_iv === null || raw.atm_iv === undefined
          ? null
          : read_number(raw.atm_iv)
        : read_number(raw.atmIv),
    skew25d:
      raw.skew25d === null || raw.skew25d === undefined
        ? raw.skew_25d === null || raw.skew_25d === undefined
          ? null
          : read_number(raw.skew_25d)
        : read_number(raw.skew25d),
    vrp: raw.vrp === null || raw.vrp === undefined ? null : read_number(raw.vrp),
    callPutOiRatio: cpRaw === null || cpRaw === undefined ? null : String(cpRaw),
    regime: raw.regime === null || raw.regime === undefined ? null : String(raw.regime),
    unusualActivity:
      raw.unusualActivity === null || raw.unusualActivity === undefined
        ? raw.unusual_activity === null || raw.unusual_activity === undefined
          ? null
          : String(raw.unusual_activity)
        : String(raw.unusualActivity),
  };
}

function parse_exchange_flow(raw: unknown): ExchangeFlowView | null {
  if (!is_record(raw)) {
    return null;
  }
  const provider = parse_str_dict(raw.provider);
  if (provider === null) {
    return null;
  }
  const nf = raw.netFlow24h ?? raw.net_flow_24h;
  return {
    provider,
    netFlow24h: nf === null || nf === undefined ? null : String(nf),
    flowZScore:
      raw.flowZScore === null || raw.flowZScore === undefined
        ? raw.flow_z_score === null || raw.flow_z_score === undefined
          ? null
          : read_number(raw.flow_z_score)
        : read_number(raw.flowZScore),
    flowTrend:
      raw.flowTrend === null || raw.flowTrend === undefined
        ? raw.flow_trend === null || raw.flow_trend === undefined
          ? null
          : String(raw.flow_trend)
        : String(raw.flowTrend),
    stablecoinReserves:
      raw.stablecoinReserves === null || raw.stablecoinReserves === undefined
        ? raw.stablecoin_reserves === null || raw.stablecoin_reserves === undefined
          ? null
          : String(raw.stablecoin_reserves)
        : String(raw.stablecoinReserves),
    signal: raw.signal === null || raw.signal === undefined ? null : String(raw.signal),
  };
}

/** True when options intelligence should render live widgets (provider breaker closed). */
export function calculate_options_flow_section_open(flow: OptionsFlowView | null): boolean {
  if (flow === null) {
    return false;
  }
  const state =
    flow.provider.state ??
    flow.provider.breakerState ??
    flow.provider.breaker_state ??
    "";
  return state === "CLOSED";
}

function parse_agent_breakdown(raw: unknown): Record<string, AgentBreakdownCellView> {
  if (!is_record(raw)) {
    return {};
  }
  const out: Record<string, AgentBreakdownCellView> = {};
  for (const [agentName, cell] of Object.entries(raw)) {
    if (!is_record(cell)) {
      continue;
    }
    const dirRaw = cell.direction;
    const subsRaw = cell.subSignals ?? cell.sub_signals;
    const subSignals: Record<string, Record<string, unknown>> = {};
    if (is_record(subsRaw)) {
      for (const [sk, sv] of Object.entries(subsRaw)) {
        if (is_record(sv)) {
          subSignals[sk] = { ...sv };
        } else {
          subSignals[sk] = { value: sv as unknown };
        }
      }
    }
    const convRaw = cell.convergences;
    const convergences = Array.isArray(convRaw) ? convRaw.map((x) => String(x)) : [];
    const risksRaw = cell.risks;
    const risks = Array.isArray(risksRaw) ? risksRaw.map((x) => String(x)) : [];
    out[agentName] = {
      score: read_number(cell.score),
      maxScore: read_number(cell.maxScore ?? cell.max_score),
      weight: read_number(cell.weight, 1),
      direction:
        typeof dirRaw === "string" && dirRaw.trim().length > 0 ? dirRaw.trim() : null,
      explanation: read_string(cell.explanation, ""),
      convergences,
      risks,
      veto: Boolean(cell.veto),
      subSignals,
    };
  }
  return out;
}

/** True when exchange-flow intelligence should render live widgets. */
export function calculate_exchange_flow_section_open(flow: ExchangeFlowView | null): boolean {
  if (flow === null) {
    return false;
  }
  const state =
    flow.provider.state ??
    flow.provider.breakerState ??
    flow.provider.breaker_state ??
    "";
  return state === "CLOSED";
}

export function calculate_obti_interpretation(detail: ObtiDetailView): string {
  if (detail.isWarmingUp) {
    return "Accumulating samples, no OBTI verdict yet";
  }
  const level = detail.level.toLowerCase();
  const side = detail.side === "bid" || detail.side === "ask" ? detail.side : null;
  if (level === "balanced" || level === "low") {
    return "Two-sided flow — no directional pressure";
  }
  if (level === "moderate" && side !== null) {
    return `Directional pressure on the ${side}-side — emerging imbalance`;
  }
  if (level === "extreme" && side !== null) {
    return `Strong directional pressure on the ${side}-side — position risk elevated ⚠`;
  }
  if (level === "moderate") {
    return "Directional pressure — emerging imbalance";
  }
  if (level === "extreme") {
    return "Strong directional pressure — position risk elevated ⚠";
  }
  return "Two-sided flow — no directional pressure";
}

export function map_signal_detail_payload(raw: unknown): SignalDetailViewModel | null {
  if (!is_record(raw)) {
    return null;
  }

  const asset = read_string(raw.asset);
  if (asset.length === 0) {
    return null;
  }

  const catRaw = raw.categoryScores ?? raw.category_scores;
  const categoryScores = normalize_category_scores(is_record(catRaw) ? catRaw : {});

  const maxRaw = raw.categoryMaxScores ?? raw.category_max_scores;
  const categoryMaxScores: Record<string, number> = {};
  if (is_record(maxRaw)) {
    for (const [k, v] of Object.entries(maxRaw)) {
      categoryMaxScores[String(k)] = read_number(v, 40);
    }
  }

  const gateThreshold = Math.round(read_number(raw.gateThreshold ?? raw.gate_threshold));

  return {
    asset,
    price: read_string(raw.price, "0"),
    change24h: read_string(raw.change24h ?? raw.change_24h, "0"),
    cycleNumber: Math.round(read_number(raw.cycleNumber ?? raw.cycle_number)),
    cycleTs: read_string(raw.cycleTs ?? raw.cycle_ts),
    totalScore: Math.round(read_number(raw.totalScore ?? raw.total_score)),
    normalizedScore: Math.round(read_number(raw.normalizedScore ?? raw.normalized_score)),
    decision: read_string(raw.decision, "Hold"),
    confidence: read_number(raw.confidence),
    passesGate:
      typeof raw.passesGate === "boolean"
        ? raw.passesGate
        : typeof raw.passes_gate === "boolean"
          ? raw.passes_gate
          : false,
    gateThreshold: Number.isFinite(gateThreshold) ? gateThreshold : 0,
    llmTierLabel: read_optional_string(raw.llmTierLabel ?? raw.llm_tier_label),
    categoryScores,
    categoryMaxScores,
    agentBreakdown: parse_agent_breakdown(raw.agentBreakdown ?? raw.agent_breakdown),
    obtiDetail: parse_obti_detail(raw.obtiDetail ?? raw.obti_detail),
    fracDiff: parse_frac_diff(raw.fracDiff ?? raw.frac_diff),
    optionsFlow: parse_options_flow(raw.optionsFlow ?? raw.options_flow),
    exchangeFlow: parse_exchange_flow(raw.exchangeFlow ?? raw.exchange_flow),
  };
}
