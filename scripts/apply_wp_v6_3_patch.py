#!/usr/bin/env python3
"""Apply White Paper v6.2 → v6.3 surgical patch spec (SENTINEL audit pass)."""

from __future__ import annotations

import re
import shutil
import sys
from copy import deepcopy
from pathlib import Path

from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.table import Table

SOURCE = Path("/home/bi/Desktop/ATLASPROMPTS/POLARIS_Engine_White_Paper_v6_2(2).docx")
OUTPUT = Path("/home/bi/Desktop/Engine 8/ATLAS/POLARIS_Engine_White_Paper_v6_3.docx")
OUTPUT_ALT = Path("/home/bi/Desktop/ATLASPROMPTS/POLARIS_Engine_White_Paper_v6_3.docx")

P513_NEW = (
    "Normalizes sentiment from three primary sources with weighted freshness decay: "
    "Alternative.me Crypto Fear & Greed Index (50% weight, broad-market composite, "
    "daily update with 24h interpolation), SentimentNewsAgent NLP over news + curated social "
    "(30%, event-gated by +2σ social volume spike, see §5.3), and funding rates as sentiment "
    "proxy (20%, skin-in-game signal from derivatives crowd positioning). Contrarian detection "
    "fires only at distribution extremes (F&G ≤ 20 for contrarian long, F&G ≥ 80 for contrarian "
    "short); mid-range readings (21–79) score zero. LunarCrush has been retired from the stack."
)

P1282_NEW = (
    "Hydra is a custom-built liquidation cascade detector. Coinalyze is accessed via REST API "
    "for funding rates and OI. Sentiment is sourced from Alternative.me F&G and SentimentNewsAgent "
    "NLP. No third-party social-engagement vendor (e.g., LunarCrush) is in the canonical stack."
)

P26_NEW_HW = (
    "Current hardware: Ryzen 9 3950X (128 GB RAM) + RTX 5060 Ti (16 GB VRAM). "
    "Local Mistral Nemo 12B (port 8080) handles routine analysis; the cloud API (DeepSeek R1) "
    "handles final generation and high-stakes reasoning; local Qwen3-Embedding 0.6B Q4 "
    "(port 8081, 1024 dims, mean pooling, query prefix required) handles embeddings via "
    "QwenEmbeddingClient."
)

P124_NEW = (
    "LLM Roadmap: The local reasoning model stack is canonical at Mistral Nemo 12B (port 8080). "
    "Future upgrade path: investigate larger reasoning models on RTX 5060 Ti once GNN inference "
    "is fully CPU-resident (per project memory). The local embedding model is canonical at "
    "Qwen3-Embedding 0.6B Q4 (port 8081, 1024 dims). Prior MistralEmbeddingClient is retired."
)

GPU_FALLBACK_NEW = (
    "cloud inference APIs (DeepSeek for reasoning; embedding fallback documented separately, "
    "but the canonical embedding stack is local Qwen3-Embedding 0.6B Q4)"
)

P1141_NEW = (
    "The routine-analysis model is Mistral Nemo 12B (port 8080). Speculative decoding with a "
    "smaller draft model remains under evaluation (candidate: smaller Mistral variant); not "
    "specified until validated on RTX 5060 Ti alongside GNN CPU-resident inference."
)

KILL_SWITCH_APPEND = """
Automatic kill switch triggers:
- Portfolio drawdown exceeds 10% (unrealized, mark-to-market) in any rolling 24-hour period.
- BAD_LOSS real-time proxy: 3 consecutive stop-loss hits on the same asset within 4 hours, regardless of Post-Trade Learning MCP classification. Proxy fires immediately; classification is reconciled later.
- 3 classified BAD_LOSS outcomes on the same asset within 4 hours (slower; redundant with proxy).
- Exchange connectivity failure beyond 30 seconds.
- Redis signal channel failure — no heartbeat within 60 seconds.
- Per-asset cooldown breach: PROMETHEUS attempted to execute on an asset in cooldown.

Kill switch activation requires explicit human reset. The system does not auto-resume.

The 10% drawdown threshold is the paper-trading initial value. After the first calibration review (30+ days of data), it may be tightened to 7% with operator approval and white paper version bump."""

V63_REVISION = (
    "v6.3 (May 2026, paper-trading hardening pass): Reconciled Table 10 score bands with "
    "Strategy Document v1.1 (140 minimum execution threshold; 5-tier band schema; per-cluster "
    "leverage numerics). Retired LunarCrush from sentiment stack across §15.2, Table 9, Table 29; "
    "promoted Alternative.me F&G to canonical sentiment input. Reconciled local LLM stack "
    "references: routine-analysis = Mistral Nemo 12B (port 8080); embeddings = local "
    "QwenEmbeddingClient (Qwen3-Embedding 0.6B Q4, port 8081). Resolved cluster proximity drift "
    "(3% canonical vs 5% v2.3 prose). Added drawdown kill switch threshold (10% rolling 24h "
    "unrealized) to §18.6. Added per-asset cooldown rule. Resolved 33-vs-32 asset count drift in "
    "§10.4 Table 29 (canonical count = 32; prose updated). Companion to Strategy Document v1.1."
)

CONTRADICTION_BLOCK = (
    "5.4 Signal Archetypes — CONTRADICTION (Fifth Archetype)\n"
    "CONTRADICTION (Civil War State) — bull and bear archetype flags simultaneously active with "
    "confluence_total ≥ 140 derived from contradictory dimension contributions. The LLM synthesis "
    "layer MUST identify this state explicitly in the archetype field. Decision: NO_TRADE "
    "regardless of confluence_total. Examples: Derivatives says short squeeze + Whale says "
    "distribution; CASCADE_EXHAUSTION + RISK_OFF_REGIME + RETAIL_DESPAIR."
)

RISKOFF_APPEND = """
RISK_OFF_REGIME composite trigger (canonical): activates when ALL THREE are simultaneously true:
1. BTC.D > 55% (5-day slope positive)
2. DXY 5-day slope > 0
3. Stablecoin supply 7-day Δ < 0 (USDT + USDC + DAI aggregate)

Effects on active positions: HOLD; stops tightened to break-even where price permits. New entries require confluence_total > 175 (override of standard 140). Max leverage tier reduced by one step regardless of confluence. CONTRADICTION archetype never executed in RISK_OFF."""

MARKPRICE_INSERT = (
    "Stage 2 cluster touch is evaluated against the Bitget MARK price (not last-traded, not index). "
    "Rationale: (a) Bitget's liquidation engine uses mark price for forced liquidations, "
    "(b) mark price is less spoofable than last-trade on thin altcoin books, "
    "(c) mark price is the basis for funding payments."
)

FUNDING_LB_APPEND = (
    "Funding z-score lookback is 30 calendar days, Bitget-only feed. Aggregated cross-exchange "
    "z-scores are NOT used for scoring. Cross-exchange divergence is logged as a separate "
    "diagnostic (potential basis-arb signal) but does not contribute to Derivatives Intelligence "
    "scoring."
)

COINALYZE_DEGRADED = (
    "When the CoinalyzeProvider multi-key round-robin pool exhausts (all keys at rate limit) or "
    "upstream Coinalyze is otherwise degraded, DerivativesAgent emits a DATA_DEGRADED flag. While "
    "this flag is active, the Derivatives Intelligence dimension score is capped such that "
    "confluence_total ≤ 139 (WATCH band) regardless of other dimension contributions. Recovery "
    "requires ≥2 consecutive successful Coinalyze fetches before scoring resumes normal operation."
)

SLIPPAGE_BLOCK = (
    "Per-cluster slippage allowance (canonical, used in risk_usd calculation and stop placement):\n"
    "- Sovereign: 0.10%\n"
    "- Agentic: 0.20%\n"
    "- Execution: 0.20%\n"
    "- Liquidity: 0.15%\n"
    "- Speculative: 0.35%\n\n"
    "risk_usd formula (no leverage term): "
    "risk_usd = position_notional_usd × (stop_distance_pct + cluster_slippage_pct)"
)

OI_MAG_INSERT = (
    "OI divergence scores only when: ≥15% change in open interest paired with ≥5% adverse price "
    "movement over a 30-minute window. Smaller magnitudes are tracked diagnostically but do not "
    "score. This prevents basis-arbitrage rebalancing noise from generating false divergence signals."
)

TOKEN_UNLOCK_INSERT = (
    "Token unlock exclusion (canonical):\n"
    "- >5% of circulating supply unlocking within 14 days → exclude from rotation entirely.\n"
    "- 2–5% within 14 days → position size capped at 50% of normal.\n"
    "- <2% within 14 days → standard treatment.\n\n"
    "Token Unlocks MCP enforces. Rationale: research shows price impact begins ≈30 days pre-unlock; "
    "the 14-day buffer is paper-trading conservatism (may relax to 7 days after validation)."
)

COOLDOWN_INSERT = (
    "Per-asset cooldown: 2 consecutive stop-loss hits on the same asset within a 4-hour window "
    "triggers a 4-hour block for that asset. Cooldown is reset by elapsed time OR a GOOD_WIN on a "
    "different asset. PROMETHEUS enforces; orchestrator may still emit signals for cooldown assets "
    "but execution is blocked."
)

TABLE_10_ROWS = [
    [
        "Score Range",
        "Decision",
        "Position Size",
        "Lev — Sovereign",
        "Lev — Agentic/Execution",
        "Lev — Liquidity/Speculative",
    ],
    ["190 – 220", "STRONG BUY/SELL", "5% (full)", "15×", "10×", "8×"],
    ["160 – 189", "BUY / SELL", "3 – 4%", "7×", "5×", "4×"],
    ["140 – 159", "WEAK BUY / SELL", "2 – 3%", "3×", "3×", "2×"],
    ["120 – 139", "WATCH", "0%", "—", "—", "—"],
    ["0 – 119", "NO TRADE", "0%", "—", "—", "—"],
]

PROXIMITY_RE = re.compile(
    r"within\s+5%\s+of\s+(?:the\s+)?(?:current\s+)?price",
    re.IGNORECASE,
)
PROXIMITY_REPL = "within 3% of current price (canonical proximity per HYDRA PC2 specification)"


def replace_in_paragraph(paragraph, old: str, new: str) -> bool:
    if old not in paragraph.text:
        return False
    paragraph.text = paragraph.text.replace(old, new)
    return True


def replace_regex_in_paragraph(paragraph, pattern: re.Pattern[str], repl: str) -> bool:
    new_text, count = pattern.subn(repl, paragraph.text)
    if count:
        paragraph.text = new_text
        return True
    return False


def set_paragraph_text(paragraph, text: str) -> None:
    paragraph.text = text


def append_to_paragraph(paragraph, suffix: str) -> None:
    base = paragraph.text.rstrip()
    paragraph.text = f"{base}\n\n{suffix.strip()}"


def find_paragraph_index(doc: Document, needle: str, start: int = 0) -> int | None:
    for i in range(start, len(doc.paragraphs)):
        if needle in doc.paragraphs[i].text:
            return i
    return None


def insert_paragraph_after(doc: Document, index: int, text: str, style: str | None = None) -> None:
    """Insert a new paragraph immediately after doc.paragraphs[index]."""
    anchor = doc.paragraphs[index]._element
    new_p = OxmlElement("w:p")
    anchor.addnext(new_p)
    from docx.text.paragraph import Paragraph

    new_para = Paragraph(new_p, doc.paragraphs[index]._parent)
    new_para.text = text
    if style:
        new_para.style = style


def set_cell_text(cell, text: str) -> None:
    cell.text = text


def add_table_column(table: Table) -> None:
    tbl = table._tbl
    tbl_grid = tbl.tblGrid
    grid_col = OxmlElement("w:gridCol")
    tbl_grid.append(grid_col)
    for row in table.rows:
        tr = row._tr
        tc = OxmlElement("w:tc")
        p = OxmlElement("w:p")
        tc.append(p)
        tr.append(tc)


def rewrite_table_10(table: Table) -> None:
    target_cols = 6
    while len(table.columns) < target_cols:
        add_table_column(table)
    for ri, row_data in enumerate(TABLE_10_ROWS):
        if ri >= len(table.rows):
            break
        row = table.rows[ri]
        for ci, value in enumerate(row_data):
            if ci < len(row.cells):
                set_cell_text(row.cells[ci], value)


def patch_document(doc: Document) -> list[str]:
    log: list[str] = []

    # P-VERSION
    set_paragraph_text(doc.paragraphs[6], "Technical White Paper v6.3")
    log.append("P-VERSION-1")
    set_paragraph_text(doc.paragraphs[1327], "END OF POLARIS ENGINE WHITE PAPER v6.3")
    log.append("P-VERSION-2")
    insert_paragraph_after(doc, 1321, V63_REVISION)
    log.append("P-VERSION-3")

    # P-LUNAR
    for idx in (100, 127, 410, 123):
        p = doc.paragraphs[idx]
        if "LunarCrush" in p.text:
            p.text = p.text.replace("LunarCrush", "Alternative.me F&G")
            p.text = p.text.replace("Alternative.me F&G Galaxy Score", "SentimentNewsAgent social-volume z-score")
            log.append(f"P-LUNAR para {idx}")

    p417 = doc.paragraphs[417]
    p417.text = p417.text.replace(
        "LunarCrush Galaxy Score > 40 (indicating sufficient social signal)",
        "social-volume z-score from SentimentNewsAgent above inclusion threshold (event-gated, see §5.3)",
    )
    log.append("P-LUNAR-4")

    p513_idx = find_paragraph_index(doc, "Normalizes sentiment from four primary sources")
    if p513_idx is None:
        p513_idx = find_paragraph_index(doc, "15.2 Sentiment Synthesis MCP")
    if p513_idx is not None:
        set_paragraph_text(doc.paragraphs[p513_idx], P513_NEW)
    log.append("P-LUNAR-5")

    set_paragraph_text(doc.paragraphs[1282], P1282_NEW)
    log.append("P-LUNAR-6")

    t9 = doc.tables[9]
    set_cell_text(t9.rows[2].cells[3], "Alternative.me F&G, SentimentNewsAgent, News APIs")
    set_cell_text(
        t9.rows[2].cells[4],
        "Fear & Greed composite, news sentiment, social volume z-score (event-gated)",
    )
    log.append("P-LUNAR-7/8")

    t29 = doc.tables[29]
    for row in t29.rows:
        for cell in row.cells:
            if "LunarCrush" in cell.text:
                cell.text = cell.text.replace(
                    "High social signal · LunarCrush tier 1",
                    "High social signal · F&G beta strong",
                )
    log.append("P-LUNAR-9")

    # Table 38 MCP list
    t38 = doc.tables[38]
    for row in t38.rows:
        for cell in row.cells:
            if "LunarCrush" in cell.text:
                cell.text = cell.text.replace("LunarCrush", "Alternative.me F&G, SentimentNewsAgent")
    log.append("P-LUNAR extra table38")

    # P-MODEL
    p26 = doc.paragraphs[26]
    if "Local Qwen 2.5-14B" in p26.text or "Mistral provides embeddings" in p26.text:
        tail = ""
        if "The architecture degrades gracefully" in p26.text:
            tail = " " + p26.text.split("The architecture degrades gracefully", 1)[-1]
            tail = "The architecture degrades gracefully" + tail
        p26.text = P26_NEW_HW + tail
    log.append("P-MODEL-1")

    replace_in_paragraph(
        doc.paragraphs[68],
        "local Qwen 2.5-14B model",
        "local Mistral Nemo 12B model (port 8080)",
    )
    replace_in_paragraph(
        doc.paragraphs[123],
        "Qwen 2.5-14B (local, via RTX 5060 Ti)",
        "Mistral Nemo 12B (local, port 8080, via RTX 5060 Ti)",
    )
    log.append("P-MODEL extra 68/123")

    set_paragraph_text(doc.paragraphs[124], P124_NEW)
    log.append("P-MODEL-2")

    for idx in (699, 714):
        replace_in_paragraph(
            doc.paragraphs[idx],
            "cloud inference APIs (DeepSeek for reasoning, Mistral for embeddings)",
            GPU_FALLBACK_NEW,
        )
    log.append("P-MODEL-3")

    p765 = doc.paragraphs[765]
    p765.text = p765.text.replace(
        "Deploy 4-bit Qwen + speculative decoding",
        "Deploy Mistral Nemo 12B tuning + speculative decoding (draft model TBD); "
        "embedding stack remains Qwen3-Embedding 0.6B Q4 on port 8081",
    )
    log.append("P-MODEL-4 (P-MODEL-5 VLM unchanged per spec)")

    if "Qwen 2.5-14B" in doc.paragraphs[1141].text:
        set_paragraph_text(doc.paragraphs[1141], P1141_NEW)
    log.append("P-MODEL-6")

    t56 = doc.tables[56]
    set_cell_text(t56.rows[1].cells[2], "Mistral Nemo 12B for routine analysis (think-block stripped pre-JSON parse)")
    set_cell_text(
        t56.rows[2].cells[2],
        "QwenEmbeddingClient (local, Qwen3-Embedding 0.6B Q4, port 8081, 1024-dim, mean pooling, query prefix required)",
    )
    log.append("P-MODEL-7/8")

    t57 = doc.tables[57]
    set_cell_text(
        t57.rows[2].cells[3],
        "Full local pipeline: Mistral Nemo 12B on 5060 Ti (port 8080); embeddings local via "
        "Qwen3-Embedding 0.6B Q4 (port 8081)",
    )
    set_cell_text(
        t57.rows[3].cells[3],
        "Larger Mistral or successor reasoning model for enhanced tool calling/reranking; "
        "local GNN inference; full embedding stack",
    )
    log.append("P-MODEL-9/10")

    # P-BANDS
    rewrite_table_10(doc.tables[10])
    log.append("P-BANDS-1")

    # P-PROXIM
    for idx in (156, 191, 524, 527):
        if idx < len(doc.paragraphs):
            if replace_regex_in_paragraph(doc.paragraphs[idx], PROXIMITY_RE, PROXIMITY_REPL):
                log.append(f"P-PROXIM para {idx}")
    for i, p in enumerate(doc.paragraphs):
        if replace_regex_in_paragraph(p, PROXIMITY_RE, "within 3% of current price"):
            if i not in (156, 191, 524, 527):
                log.append(f"P-PROXIM sweep [{i}]")

    # P-KILL — manual kill switch paragraph (content-anchored)
    kill_idx = find_paragraph_index(
        doc, "single-button emergency stop is accessible via the dashboard"
    )
    if kill_idx is not None and "Portfolio drawdown exceeds 10%" not in doc.paragraphs[kill_idx].text:
        append_to_paragraph(doc.paragraphs[kill_idx], KILL_SWITCH_APPEND)
    log.append("P-KILL-1")

    # P-COOLDOWN — re-entry policy body (not heading)
    reentry_idx = find_paragraph_index(doc, "Re-entry is unlimited with no cooldown period")
    if reentry_idx is not None:
        if COOLDOWN_INSERT not in doc.paragraphs[reentry_idx].text:
            append_to_paragraph(doc.paragraphs[reentry_idx], COOLDOWN_INSERT)
    log.append("P-COOLDOWN-1")

    # P-CONTRADICTION — insert after Event-Driven Sentiment Gate block (~159)
    insert_paragraph_after(doc, 159, CONTRADICTION_BLOCK)
    log.append("P-CONTRADICTION-1")

    # P-RISKOFF
    append_to_paragraph(doc.paragraphs[143], RISKOFF_APPEND)
    log.append("P-RISKOFF-1")

    # P-MARKPRICE — attach to Stage 2 commitment body, not Stage 1 probe
    stage2_idx = find_paragraph_index(
        doc, "Stage 2 commits the remaining 75% of intended position size"
    )
    if stage2_idx is not None:
        append_to_paragraph(doc.paragraphs[stage2_idx], MARKPRICE_INSERT)
        # Remove misplaced block if prior run appended to Stage 1 probe paragraph
        probe_idx = find_paragraph_index(doc, "The Stage 1 probe uses a tight stop-loss")
        if probe_idx is not None and "Bitget MARK price" in doc.paragraphs[probe_idx].text:
            doc.paragraphs[probe_idx].text = doc.paragraphs[probe_idx].text.split(
                "\n\nStage 2 cluster touch is evaluated", 1
            )[0].rstrip()
    log.append("P-MARKPRICE-1")

    # P-FUNDING-LB + P-COINALYZE on §15.3 derivatives MCP
    deriv_idx = find_paragraph_index(doc, "Hydra is used for all spatial liquidation analysis")
    if deriv_idx is not None:
        body = doc.paragraphs[deriv_idx].text
        if FUNDING_LB_APPEND not in body:
            append_to_paragraph(doc.paragraphs[deriv_idx], FUNDING_LB_APPEND)
        if COINALYZE_DEGRADED not in body:
            append_to_paragraph(doc.paragraphs[deriv_idx], COINALYZE_DEGRADED)
    log.append("P-FUNDING-LB-1 + P-COINALYZE-1")

    # P-SLIPPAGE — after heading 453, content likely in 454+
    insert_paragraph_after(doc, 453, SLIPPAGE_BLOCK)
    log.append("P-SLIPPAGE-1")

    # P-OI-MAG
    insert_paragraph_after(doc, 482, OI_MAG_INSERT)
    log.append("P-OI-MAG-1")

    # P-TOKENUNLOCK
    incl_idx = find_paragraph_index(doc, "An asset qualifies for cluster active rotation")
    if incl_idx is not None:
        append_to_paragraph(doc.paragraphs[incl_idx], TOKEN_UNLOCK_INSERT)
    log.append("P-TOKENUNLOCK-1")

    stage1_idx = find_paragraph_index(doc, "Stage 1 (Data Aggregation):")
    if stage1_idx is not None:
        doc.paragraphs[stage1_idx].text = doc.paragraphs[stage1_idx].text.replace(
            "Alternative.me F&G social sentiment",
            "Alternative.me F&G and SentimentNewsAgent NLP sentiment",
        )

    # P-COUNT
    for i, p in enumerate(doc.paragraphs):
        if "33 assets" in p.text:
            p.text = p.text.replace(
                "33 assets across 5 strategic clusters",
                "32 assets across 5 strategic clusters (per Table 29)",
            )
        if "33-asset" in p.text and not p.text.strip().startswith("v6."):
            p.text = p.text.replace("33-asset", "32-asset")
        if "33 assets" in p.text and not p.text.strip().startswith("v6."):
            p.text = p.text.replace("33 assets", "32 assets")
    log.append("P-COUNT")

    # Remaining LunarCrush sweep
    for i, p in enumerate(doc.paragraphs):
        if "LunarCrush" in p.text:
            p.text = p.text.replace("LunarCrush", "Alternative.me F&G (retired vendor)")
            log.append(f"LunarCrush sweep [{i}]")

    return log


def verify(doc: Document) -> list[str]:
    issues: list[str] = []
    body_paras = [
        p.text
        for i, p in enumerate(doc.paragraphs)
        if not (i > 1320 and p.text.strip().startswith("v6."))
    ]
    text_blob = "\n".join(body_paras)
    if "LunarCrush" in text_blob:
        issues.append("LunarCrush still present in body paragraphs")
    if "Technical White Paper v6.2" in doc.paragraphs[6].text:
        issues.append("Version not updated on cover")
    if "Qwen 2.5-14B" in text_blob:
        issues.append("Qwen 2.5-14B still present in body (VLM 2.5-VL is allowed)")
    t10 = doc.tables[10]
    if t10.rows[1].cells[0].text.strip() != "190 – 220":
        issues.append(f"Table 10 row1 mismatch: {t10.rows[1].cells[0].text!r}")
    if re.search(r"within\s+5%\s+of", text_blob, re.I):
        issues.append("5% cluster proximity still present in body")
    if re.search(r"\b33[- ]asset", text_blob, re.I):
        issues.append("33-asset count still present in body prose")
    return issues


def main() -> int:
    if not SOURCE.exists():
        print(f"Source not found: {SOURCE}", file=sys.stderr)
        return 1

    doc = Document(str(SOURCE))
    log = patch_document(doc)
    issues = verify(doc)

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(OUTPUT))
    shutil.copy2(OUTPUT, OUTPUT_ALT)

    print(f"Saved: {OUTPUT}")
    print(f"Copy:  {OUTPUT_ALT}")
    print(f"Patches applied: {len(log)}")
    if issues:
        print("VERIFY WARNINGS:")
        for issue in issues:
            print(f"  - {issue}")
        return 2
    print("VERIFY: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
