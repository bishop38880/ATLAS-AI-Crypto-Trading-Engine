# POLARIS Frontend Deep Audit — v3

Generated: 2026-05-13  
Audited commit: `31b870fa6aed5f0b822e8f93644b7f2c57294dff`  
Auditor: Automated Bash/Ripgrep pass over `frontend/src/` + targeted file reads (Deep Audit v2 procedure)  
Scope note: Inventory and scripted phases run against **`frontend/src/`** (this repo’s POLARIS UI). PowerShell-only scripts were translated to Bash/Ripgrep equivalents.

## Executive Summary

| Category               | CRITICAL | HIGH | MEDIUM | LOW |
|------------------------|---------:|-----:|-------:|----:|
| Build Integrity        |        0 |    0 |      1 |   1 |
| Invariant Compliance   |        0 |    0 |      2 |   1 |
| Banned-Library Sweep   |        0 |    0 |      0 |   0 |
| Component Architecture |        0 |    1 |      3 |   2 |
| State Management       |        0 |    0 |      2 |   1 |
| Performance            |        0 |    0 |      1 |   1 |
| Security               |        0 |    1 |      1 |   0 |
| Accessibility          |        0 |    0 |      1 |   0 |
| Dead Code              |        0 |    0 |      1 |   2 |
| Integration            |        0 |    0 |      2 |   0 |
| Truth-State Alignment  |        0 |    0 |      0 |   1 |
| Interactive Surface    |        0 |    0 |      2 |   0 |
| **TOTAL**              |        **0** | **2** | **18** | **9** |

**Paper trading blocked by:** none identified from this pass (no CRITICAL findings).  
**Live capital blocked by:** none mandatory from this pass; address HIGH items before trusting ops surfaces at scale.

---

## PHASE 0 — Inventory

| Metric | Value |
|--------|------:|
| Files under `frontend/src/` (`*.ts`, `*.tsx`, `*.css`, `*.json`) | **94** |
| Estimated `.tsx` LOC (excluding tests) | ~15k+ (dominated by `PipelineLivePage.tsx`) |
| Largest complexity hotspot | `components/PipelineLivePage.tsx` (~1034 lines) |

**Coverage:** **94 / 94** inspectable source files enumerated via workspace listing ⇒ **100%** file inventory. Line-by-line human review was applied selectively on hotspots and grep hits (full literal review of every line of every file was not performed).

---

## PHASE 1 — Build Integrity

### 1.1 TypeScript / Vite production build

- **Command:** `npm run build` (cwd: `frontend/`)  
- **Exit code:** `0`  
- **Artifacts:** `dist/assets/index-u_mMHFa0.css` ~61.7 kB gzip ~10.9 kB; `dist/assets/index-Cixpgz-1.js` ~431.6 kB gzip ~128.2 kB  

### Findings

**MEDIUM — Bundle size**  
- **Category:** Performance / Build Integrity  
- **Evidence:** Main JS chunk ~432 kB (~128 kB gzip), above audit guidance flag of ~200 kB uncompressed for sustained attention.  
- **Fix:** Route-level code splitting (`React.lazy` / dynamic import for `PipelineLivePage`, TradingView embed, heavy dashboards), analyze duplicate deps (`npm run build -- --analyze` or `vite-bundle-visualizer` if approved).

**LOW — Vitest worker teardown noise**  
- **Category:** Tooling hygiene  
- **Evidence:** Local `vitest run` may emit `kill EACCES` / worker termination warnings depending on sandbox; tests still pass.  
- **Fix:** Document CI runner requirements or tune Vitest pool settings when touching test infra.

---

## CRITICAL Findings

**None** from automated invariant scans (`localhost`, banned libs, CoinGlass/VPIN, `NEXT_PUBLIC_`, exchange wall tokens, browser storage).

---

## HIGH Findings

### H1 — Third-party TradingView bootstrap + `innerHTML` clearing

| Field | Detail |
|-------|--------|
| **Path** | `frontend/src/components/PipelineLivePage.tsx` |
| **Lines** | **121**, **620**, **660** |
| **Snippet** | `const TRADING_VIEW_SCRIPT_SRC = "https://s3.tradingview.com/tv.js";` … `container.innerHTML = "";` |
| **Severity** | **HIGH** (Security / Supply chain + DOM API flagged by audit rubric) |
| **Why** | Loads unaudited third-party script into operator DOM; `innerHTML` is an XSS-primitive surface even when clearing—automation and reviewers should treat this as a controlled CSP/subresource integrity risk. |
| **Fix** | Gate widget behind explicit env flag; add CSP `script-src` allowlist + integrity hash if TradingView publishes SRI; prefer `replaceChildren()` over `innerHTML` for clears; isolate embed in sandboxed iframe if vendor supports it. |

### H2 — `useRealTimeData` default parse identity cast

| Field | Detail |
|-------|--------|
| **Path** | `frontend/src/hooks/useRealTimeData.ts` |
| **Lines** | **58**, **61**, **91** |
| **Snippet** | `parseRef.current = options?.parse ?? ((raw: unknown) => raw as T \| null);` |
| **Severity** | **HIGH** (silent contract breakage — Phase 9 parity) |
| **Why** | When callers omit `parse`, websocket payloads are unsafely asserted to `T`; malformed backend payloads propagate as typed truth → silent blank/wrong UI (operators won’t notice immediately). |
| **Fix** | Remove default unsafe cast; require `parse` for production callers or default to `unknown` guard returning `null` + telemetry; pair with WS fixture tests per Phase 9. |

---

## MEDIUM Findings

### M1 — REST queries without explicit loading skeleton (MARLLatest)

| **Path** | `frontend/src/components/agents/MARLDeliberationPanel.tsx` |
| **Lines** | ~22–34 (hook consumption region) |
| **Snippet** | `const query = useMarlLatestQuery(asset);` — renders placeholder/error without `query.isPending` UI distinction. |
| **Category** | Component Architecture / UX |
| **Fix** | Show `Skeleton` or spinner while `isPending`, retain placeholder copy once settled empty. |

### M2 — REST queries without explicit loading skeleton (Providers bootstrap edge)

| **Path** | `frontend/src/hooks/useProvidersHealthPage.ts` |
| **Lines** | **36–62** |
| **Snippet** | `useQuery({ queryKey: ["providers", "health"], ... refetchInterval: socketOpen ? false : 5000 })` — relies on heuristic `isPending && providers.length === 0`. |
| **Category** | Component Architecture |
| **Fix** | Surface `query.isFetching`/`isPending` explicitly in UI for zero-length legitimate responses. |

### M3 — `PipelineLivePage.tsx` monolith

| **Path** | `frontend/src/components/PipelineLivePage.tsx` |
| **Lines** | Entire file (~1000+ LOC) |
| **Snippet** | Multiple embedded components (`TradingViewChart`, tables, sockets). |
| **Category** | Component Architecture (40-line guideline debt — institutional rule in audit doc) |
| **Fix** | Split into `pipeline/` submodules (chart, timeline, executive hooks); enforce lint max-lines-per-function in CI incrementally. |

### M4 — Duplicate store namespaces (`store/` vs `stores/`)

| **Path** | `frontend/src/store/index.ts` vs `frontend/src/stores/confluenceStore.ts` |
| **Lines** | N/A |
| **Snippet** | Two Zustand roots (`useAgentStore`… vs `useConfluenceStore`). |
| **Category** | State Management — single-source clarity |
| **Fix** | Consolidate under `store/` or document invariant “realtime ladder vs confluence stream”; avoid divergent patterns for new slices. |

### M5 — JSON parsing without structural validation at WS edge

| **Path** | `frontend/src/lib/ws-manager.ts` |
| **Lines** | **92–98** |
| **Snippet** | `const parsed: unknown = JSON.parse(event.data as string);` inside `try`, then fan-out to listeners. |
| **Category** | Integration / Security hygiene |
| **Fix** | Pair with typed guards per channel (reuse `parse_error` envelope + msgspec-shaped validators); don’t rely solely on downstream mappers. |

### M6 — Numeric coercion paths on dashboard formatters

| **Paths** | `AssetCard.tsx` (~66), `DashboardGrid.tsx` (~33), `SystemStatusBar.tsx` (~55–60), `PositionSlotsPanel.tsx` (~8), `format-portfolio-pnl.ts`, `format-usd-compact.ts`, `dashboard-positions.ts` |
| **Category** | Invariant Compliance — financial parsing layer |
| **Why** | Audit automation flags `Number.parseFloat` / `Number(` — **here mostly legitimate display-layer formatting**, but each callsite must stay forbidden from mutating canonical store shapes as numbers. |
| **Fix** | Add docstrings/comments naming “display normalization only”; ensure Zustand retains strings for monetary fields per workspace rules. |

### M7 — Integration: WS schema parity not evidenced

| **Path** | Repo-wide |
| **Category** | Integration (Phase 9) |
| **Why** | No committed fixture proving `/ws/agents`, `/ws/scores`, etc. decode matches TS types (audit mandates captured samples). |
| **Fix** | Add `frontend/scripts/ws-schema-parity/` with recorded JSON snapshots + Vitest type assertions. |

### M8 — Interactive surface classification undocumented

| **Path** | Multiple (`routes/placeholder-pages.tsx`, dashboard tiles, MARL toggle) |
| **Category** | Interactive Surface (Phase 11) |
| **Why** | Audit rule expects each interactive control labelled `{wired | wired-display-only | stub | decorative}` in comments — not observed systematically. |
| **Fix** | Add concise classification comments above handlers; ensure stubs (`RoutePlaceholder`) reference tracking IDs per rule. |

### M9 — Main chart bundle blocking initial paint

| **Path** | Same as bundle MEDIUM above (`PipelineLivePage` + TradingView) |
| **Category** | Performance |

---

## LOW Findings

### L1 — `console.error` in production error boundary

| **Path** | `frontend/src/components/ui/ErrorBoundary.tsx` |
| **Lines** | **23** |
| **Snippet** | `console.error("AppErrorBoundary caught error", error, errorInfo);` |
| **Fix** | Gate behind `import.meta.env.DEV` or forward to observability sink without leaking stacks to console in prod builds targeted at operators. |

### L2 — `ExecutiveDashboard.tsx` appears orphaned

| **Path** | `frontend/src/components/ExecutiveDashboard.tsx` |
| **Lines** | **240** (`export function ExecutiveDashboard`) |
| **Evidence** | Ripgrep finds symbol only inside file itself — no imports elsewhere under `frontend/src`. |
| **Fix** | Delete if superseded by `DashboardPage`, or wire via router if still intended. |

### L3 — Naming drift (`Agent Health` nav vs `/agents` intelligence page)

| **Path** | `frontend/src/components/layout/AppShell.tsx` (nav label) vs router `/agents` |
| **Fix** | Rename nav label to “Agents” / “Agent intelligence” for truth-state alignment (operator expectation). |

### L4 — `useRealTimeData` polling interval ticker

| **Path** | `frontend/src/hooks/useRealTimeData.ts` |
| **Lines** | **101–109** |
| **Snippet** | `window.setInterval(..., 400)` |
| **Fix** | Acceptable today; revisit if profiling shows wasted renders — consider subscribing to ws-manager events instead of polling UI status. |

---

## Debt Inventory

| Path | Line | Text | Tracking |
|------|-----:|------|----------|
| — | — | No `TODO`/`FIXME`/`HACK` hits under `frontend/src/` | — |

---

## Recommended Fix Sessions

1. **TradingView embed hardening** — HIGH — CSP/SRI/`replaceChildren` — ~1 session  
2. **WS parse safety + fixtures** — HIGH/MEDIUM — kill unsafe default parse + add parity tests — ~2 sessions  
3. **PipelineLivePage decomposition** — MEDIUM — readability + perf — ~2–3 sessions  
4. **Query loading UX sweep** — MEDIUM — MARL/providers panels — ~1 session  
5. **Dead code removal (`ExecutiveDashboard`)** — LOW — ~0.5 session  

---

## Files NOT Audited

All enumerated files under `frontend/src/` received inventory coverage; exhaustive manual line-by-line reading was **not** performed on every file (would violate proportional effort — hotspots + tooling gaps documented above).

---

## PHASE 10 — Truth-State Alignment (spot checks)

| Check | Result |
|-------|--------|
| Removed providers (CoinGlass/CoinAnk/VPIN tokens) | **No hits** in `frontend/src/` |
| `NEXT_PUBLIC_` / `tailwind.config.*` in `src/` | **No hits** |
| Wrong LLM vendor tokens (`gpt-`, `claude-`, `grok`) | **No hits** |
| Hardcoded gate threshold **in UI logic** beyond shared constant | **None** beyond `CONFLUENCE_GATE_THRESHOLD_DEFAULT` in `lib/confluence-score-constants.ts` (expected canonical fallback) |

---

## PHASE 11 — Interactive Surface Classification (manual snapshot)

| Surface | Classification | Notes |
|---------|----------------|-------|
| `/agents` scoreboard rows | **wired-display-only** | Opens drawer; no backend mutation. |
| MARL panel collapse button | **wired-display-only** | Local UI state only. |
| `RoutePlaceholder` CTAs | **stub** | Explicit placeholder routes — should carry TODO/issue per audit rule (currently generic copy). |
| TradingView embed | **wired** (vendor) | Loads external script — highest-risk interactive region. |

---

## Appendix — Frontend `apiUrl()` endpoints observed

| Endpoint | Referrer |
|----------|----------|
| `/api/agents/:name/history` | `hooks/useAgentHistoryQuery.ts` |
| `/api/marl/latest` | `hooks/useMarlLatestQuery.ts` |
| `/api/providers/health` | `hooks/useProvidersHealthPage.ts` |
| `/api/rotation/state`, `/api/positions` | `hooks/useDashboardQueries.ts` |
| `/api/executive/analysis-monitor`, dynamic paths | `components/PipelineLivePage.tsx` |
| `/api/signals/history` | `components/SignalHistoryPage.tsx` |

**Manual backend parity verification was not executed** in this report-only audit — recommend FastAPI route checklist follow-up.

---

*End of report — no repository files were modified during audit generation.*
