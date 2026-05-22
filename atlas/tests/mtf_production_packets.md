# MTF LM Studio Paste Packets — Multi-Timeframe Confluence

Load `MTF_CONFLUENCE_SYSTEM_PROMPT` from `atlas/prompts/mtf_confluence_prompt.py` as the **system / instructions** side. Paste each payload below as **user payload** JSON (often one line).

---

## PACKET MTF-1 — BUILDING, avg 161 (expect STRONG / BUY, LONG leaning)

Weighted base: \(128×0.5 + 151×0.3 + 181×0.2\) = **145.5** → **146** (`ROUND_HALF_UP` to whole points).

BUILDING × **1.10** → \(146 × 1.10 = 160.6\) → **161** quantized to an integer. (Comments that used 160 rounded the base down before multiplying — the engine multiplies the **quantized** base.)

```json
{"v":"2.1","id":"demo-mtf-1","sym":"BTC/USDT","tf":"15m","ts":"2030-01-01T12:00:00Z","px":"65000","chg":1.2,"sc":{"total":181},"mtf":{"sc_4h":128,"sc_30m":151,"sc_15m":181,"base_avg":146,"alignment":"BUILDING","multiplier":"1.10","avg":161,"is_stale":false},"mode":"CONFLUENCE"}
```

**Expected behaviour:** STRONG/BUY tier from `avg`; direction from bullish 15m agents.

---

## PACKET MTF-2 — FADING, avg 140 (expect NO_TRADE or WEAK-only after prompts)

\(181×0.5 + 151×0.3 + 120×0.2\) = 159 × 0.88 = **140** (rounded). Below normal BUY tier (150+) after penalty.

```json
{"v":"2.1","id":"demo-mtf-2","sym":"BTC/USDT","tf":"15m","ts":"2030-01-01T12:00:00Z","mtf":{"sc_4h":181,"sc_30m":151,"sc_15m":120,"base_avg":159,"alignment":"FADING","multiplier":"0.88","avg":140,"is_stale":false},"mode":"CONFLUENCE","sc":{"total":120}}
```

**Expected behaviour:** NO_TRADE or tightly risk-reduced read — late-entry penalty already baked into `avg`.

---

## PACKET MTF-3 — FLAT envelope, avg 162

\(165×0.5 + 158×0.3 + 161×0.2\) = 82.5 + 47.4 + 32.2 = **162.1** → **162**.

```json
{"v":"2.1","id":"demo-mtf-3","sym":"ETH/USDT","tf":"15m","ts":"2030-01-01T12:00:00Z","mtf":{"sc_4h":165,"sc_30m":158,"sc_15m":161,"base_avg":162,"alignment":"FLAT","multiplier":"1.00","avg":162,"is_stale":false},"mode":"CONFLUENCE","sc":{"total":161}}
```

**Expected behaviour:** BUY / SELL tier from thresholds; resolve direction purely from agents on 15m.

---

## PACKET MTF-4 — STALE 30m (simulate API short-circuit)

Hand this to reasoning only if exercising **staleness narration** inside the model; the REST layer returns before LLM call when timestamps fail gates.

```json
{"v":"2.1","id":"demo-mtf-4","sym":"SOL/USDT","tf":"15m","ts":"2030-01-01T12:00:00Z","mtf":{"sc_4h":170,"sc_30m":160,"sc_15m":172,"base_avg":0,"alignment":"FLAT","multiplier":"1.00","avg":0,"is_stale":true,"stale_tf":"30m"},"mode":"CONFLUENCE","sc":{"total":172}}
```

**Expected:** NO_TRADE, `signal_type=STALE_SIGNAL`, no reliance on numeric edge.

---

## PACKET MTF-5 — BUILDING but weak backbone, avg 108

Base \(88×0.5 + 101×0.3 + 124×0.2\) = 44 + 30.3 + 24.8 = **98.1** → **98**; ×1.10 = **107.9** → **108**.

```json
{"v":"2.1","id":"demo-mtf-5","sym":"BTC/USDT","tf":"15m","ts":"2030-01-01T12:00:00Z","mtf":{"sc_4h":88,"sc_30m":101,"sc_15m":124,"base_avg":98,"alignment":"BUILDING","multiplier":"1.10","avg":108,"is_stale":false},"mode":"CONFLUENCE","sc":{"total":124}}
```

**Expected behaviour:** NO_TRADE — multiplier cannot lift a fundamentally weak fused score into actionable territory.

---

## PACKET MTF-6 — RECOVERING, avg 152

Example shaping: \(150×0.5 + 135×0.3 + 170×0.2\) → base **150**; multiplier 1.00 → **avg 152** (rounded if needed).

```json
{"v":"2.1","id":"demo-mtf-6","sym":"BTC/USDT","tf":"15m","ts":"2030-01-01T12:00:00Z","mtf":{"sc_4h":150,"sc_30m":135,"sc_15m":170,"base_avg":150,"alignment":"RECOVERING","multiplier":"1.00","avg":152,"is_stale":false},"mode":"CONFLUENCE","sc":{"total":170}}
```

**Expected behaviour:** BUY tier without BUILDING uplift; ambiguity acknowledged but threshold cleared.

---

## PACKET MTF-7 — RA.veto (avg nominally strong)

Synthetic — pair a strong `mtf.avg` with risk veto semantics from compact schema shorthand:

```json
{"v":"2.1","id":"demo-mtf-7","sym":"BTC/USDT","tf":"15m","ts":"2030-01-01T12:00:00Z","ag":{"RA":{"s":92,"veto":true}},"rs":{"ks":false},"mtf":{"sc_4h":190,"sc_30m":180,"sc_15m":170,"base_avg":180,"alignment":"BUILDING","multiplier":"1.10","avg":178,"is_stale":false},"mode":"CONFLUENCE","sc":{"total":170},"dp":[]}
```

**Expected behaviour:** Immediate NO_TRADE, `signal_type=VETO_ACTIVE`, veto detail in `skip_reason`.

---

## PACKET MTF-8 — Triple degraded dimension

Minimal stand-in (`dp[]` illustrative — expand to whatever your compact envelope uses downstream):

```json
{"v":"2.1","id":"demo-mtf-8","sym":"BTC/USDT","tf":"15m","ts":"2030-01-01T12:00:00Z","dp":["nansen","santiment","lunarcrush"],"mtf":{"sc_4h":165,"sc_30m":158,"sc_15m":162,"base_avg":162,"alignment":"FLAT","multiplier":"1.00","avg":161,"is_stale":false},"mode":"CONFLUENCE","sc":{"total":162},"ag":{"OA":{"s":0},"WA":{"s":5},"SYN":{"s":140}}}
```

**Expected behaviour:** NO_TRADE plus `signal_type=INSUFFICIENT_DATA` citing ≥3 degraded provider-driven dimensions.
