# SESSION 23 — Paper Trading Fidelity Engine

## Context Files
@atlas/shared/config.py @schema.sql @prometheus/oms/ @atlas/signals/outcome.py @prometheus/execution/bitget_execution_client.py

## Prerequisites
- Session 22 (Kill Switch) — operational.
- Session 22B (BitgetExecutionClient) — **hard prerequisite**. Paper engine
  reads market data (tickers, funding) through this client when provider cache
  is cold.
- The `.mdc` cursor rules (`010-atlas-identity`, `020-python-style`,
  `030-async-patterns`, `040-errors-and-testing`, `050-tech-stack`) must be the
  reconciled canonical versions — the stale pgvector / SQLAlchemy / sentence-transformers
  references have been removed.

## Goal
Build a realistic paper trading simulation layer that applies Bitget's
actual cost structure to every simulated trade. Without this, paper
trading Sharpe is inflated 20–40% and useless for phase promotion.

**PHASE 0 RULE:** Paper trading does not begin until this engine is operational.
All paper P&L is reported GROSS (raw fills) and NET (after fees, funding,
slippage). Only NET figures feed phase-gate decisions.

---

## NON-NEGOTIABLE INVARIANTS

1. **Pyright only.** `pyright --pythonversion 3.12`.
2. **No banned libraries.** `aioredis` → `redis.asyncio`; stdlib `json` →
   `msgspec`; `orjson`, `pandas`, `SQLAlchemy`, `psycopg2`, `pickle`, `joblib`,
   `requests`, `sentence-transformers`, `pgvector`, `FAISS`, `BM25`,
   `LlamaIndex` → banned.
3. **`PolarisSettings` only.** Never `os.getenv()`.
4. **40-line function limit.**
5. **Loguru POSITIONAL format only.** Loguru **silently discards kwargs at the
   call site** in our configuration. Every call must use `"{}"` placeholders and
   positional arguments:

   ```python
   # CORRECT
   logger.info("paper_trade_opened | trade_id={} | asset={} | fill_price={}",
               trade_id, asset, float(fill.simulated_fill_price))

   # WRONG — kwargs silently discarded
   logger.info("paper_trade_opened", trade_id=trade_id, asset=asset)
   ```
6. **Decimal everywhere for financial fields.** `asyncpg` accepts `Decimal`
   natively for `NUMERIC` columns — no `float()` or `str()` cast at the DB
   boundary, no exception. Any `float(some_decimal)` in this session is an
   audit failure.
7. **No CoinGlass.** CoinGlass is fully removed from POLARIS. Funding rate is
   sourced from Coinalyze only. HYDRA handles liquidation cascades (not
   relevant here).
8. **No `coinank`.** Remove speculative provider names from fallback lists.
9. **No per-call `httpx.AsyncClient(...)`.** Inject the app-wide singleton at
   construction time. `030-async-patterns.mdc` mandates this.
10. **`gross_sharpe` is diagnostic only.** Phase gate decisions read
    `net_sharpe`. Any code path that branches on `gross_sharpe` outside
    diagnostic logging is an audit failure.
11. **Fee rates are configurable.** The default `0.0002 / 0.0006` (Bitget VIP 0)
    is a **default**, not a hard-coded constant. A startup probe compares the
    configured rate against the live `get_contract_spec("BTCUSDT")` rate and
    logs a WARNING if they diverge by more than `0.00005`.

---

## Task 0 — PolarisSettings additions

Add to `atlas/shared/config.py`:

```python
class PolarisSettings(BaseSettings):
    # ... existing fields ...

    # === Paper trading — Session 23 ===
    paper_trading_enabled: bool = True
    paper_starting_capital_usd: Decimal = Decimal("10000")

    # === Bitget fee tier (VIP 0 defaults — verify on startup) ===
    bitget_maker_fee_rate: Decimal = Decimal("0.0002")   # 0.02%
    bitget_taker_fee_rate: Decimal = Decimal("0.0006")   # 0.06%
    bitget_fee_divergence_threshold: Decimal = Decimal("0.00005")
```

## Task 1 — Core Models

Create `prometheus/paper/models.py`:

- `FillSimulation(frozen=True)` — order_id, asset, side, requested_size_usd,
  simulated_fill_price, slippage_bps, fill_candles, is_partial_fill,
  filled_size_usd, unfilled_size_usd, fee_usd, fee_rate, timestamp.
- `FundingAccrual(frozen=True)` — asset, position_side, funding_rate,
  position_notional_usd, funding_cost_usd, accrued_at, next_funding_at.
- `PaperTradeResult(frozen=True)` — complete P&L record with entry/exit/funding
  blocks, gross and net P&L, cost_drag_pct, is_win.
- `PaperPortfolioSnapshot(frozen=True)` — total_capital_usd, realized_pnl_usd,
  win_rate, `gross_sharpe` (DIAGNOSTIC), `net_sharpe` (PRIMARY),
  net_max_drawdown_pct, total_fees_paid_usd, total_funding_paid_usd,
  total_slippage_cost_usd, cost_as_pct_of_gross.

Every numeric financial field is `Decimal`. No floats in model definitions.

## Task 2 — Fee Calculator

Create `prometheus/paper/fee_calculator.py`:

```python
from decimal import Decimal
from atlas.shared.config import PolarisSettings


class FeeCalculator:
    """Bitget perpetual futures fee calculator — rates from PolarisSettings."""

    def __init__(self, settings: PolarisSettings) -> None:
        self._maker = settings.bitget_maker_fee_rate
        self._taker = settings.bitget_taker_fee_rate

    def entry_fee(self, notional_usd: Decimal) -> Decimal:
        """Entry is always market/taker in this system."""
        return (notional_usd * self._taker).quantize(Decimal("0.000001"))

    def exit_fee(self, notional_usd: Decimal, is_limit_exit: bool = False) -> Decimal:
        rate = self._maker if is_limit_exit else self._taker
        return (notional_usd * rate).quantize(Decimal("0.000001"))

    def round_trip_fee(self, notional_usd: Decimal) -> Decimal:
        return self.entry_fee(notional_usd) + self.exit_fee(notional_usd)

    def fee_break_even_pct(self, leverage: Decimal) -> Decimal:
        """Minimum price move to cover round-trip fees (for diagnostics)."""
        total_rate = self._taker * 2
        return (total_rate / leverage).quantize(Decimal("0.000001"))
```

### Fee-Tier Startup Probe

Add to the PROMETHEUS startup sequence (`prometheus/startup.py` or equivalent):

```python
async def verify_fee_tier(bitget: BitgetExecutionClient, settings: PolarisSettings) -> None:
    """Log a WARNING if configured fees diverge >threshold from live contract spec."""
    spec = await bitget.get_contract_spec("BTCUSDT")
    live_taker = Decimal(str(spec.taker_fee_rate))
    drift = abs(live_taker - settings.bitget_taker_fee_rate)
    if drift > settings.bitget_fee_divergence_threshold:
        logger.warning(
            "fee_tier_drift | configured_taker={} | live_taker={} | drift={}",
            settings.bitget_taker_fee_rate, live_taker, drift,
        )
```

## Task 3 — Slippage Simulator

Create `prometheus/paper/slippage_simulator.py`:

Model: `slippage = 0.5 × bid_ask_spread` for market orders. Partial fill when
`order_size_usd > 5% × trailing_1h_volume_usd`, fills over `ceil(pct / 5%)`
candles up to 5, with linear per-candle price impact.

### Redis data sourcing — msgspec only

```python
import msgspec
import redis.asyncio as redis_asyncio


class SlippageSimulator:
    def __init__(self, redis_client: redis_asyncio.Redis) -> None:
        self._redis = redis_client

    async def _fetch_market_data(
        self, asset: str
    ) -> tuple[Decimal, Decimal, Decimal]:
        """Return (spread_bps, mid_price, 1h_volume_usd).

        Conservative fallbacks: (20 bps, 1.0, $100k).
        """
        try:
            ticker_raw = await self._redis.get(f"provider:bitget:{asset}:ticker")
            if not ticker_raw:
                raise ValueError("no ticker data")
            ticker = msgspec.json.decode(ticker_raw)
            bid = Decimal(str(ticker["bid"]))
            ask = Decimal(str(ticker["ask"]))
            mid = (bid + ask) / 2
            spread_bps = ((ask - bid) / mid * 10000).quantize(Decimal("0.001"))

            candle_raw = await self._redis.get(f"provider:bitget:{asset}:1h")
            volume_1h = Decimal("100000")
            if candle_raw:
                candle = msgspec.json.decode(candle_raw)
                volume_1h = Decimal(str(candle.get("quote_volume", "100000")))
            return spread_bps, mid, volume_1h
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(
                "slippage_data_fallback | asset={} | using=conservative_defaults | exc={}",
                asset, exc,
            )
            return Decimal("20"), Decimal("1"), Decimal("100000")
```

The `simulate_fill()` method itself is a thin orchestrator — ≤ 40 lines.

## Task 4 — Funding Accrual Engine

Create `prometheus/paper/funding_engine.py`:

- Settles every 8h at 00:00, 08:00, 16:00 UTC.
- Long pays when rate > 0; short receives. Sign convention:
  `funding_cost = notional × rate` (long), negated for short.

### Funding Rate Fetcher — Corrected

```python
import asyncio
import msgspec
import redis.asyncio as redis_asyncio
from decimal import Decimal
from loguru import logger


class FundingEngine:
    FUNDING_INTERVAL_HOURS = 8
    FUNDING_HOURS = [0, 8, 16]
    FALLBACK_RATE = Decimal("0.0001")   # 0.01% conservative — marked DEGRADED when used

    def __init__(self, redis_client: redis_asyncio.Redis) -> None:
        self._redis = redis_client

    async def _fetch_funding_rate(self, asset: str) -> Decimal:
        """Fetch live funding rate. Per-provider try blocks — no silent swallow.

        Providers probed in order:
          1. Coinalyze  (canonical — CoinGlass fully removed)
          2. Bitget ticker (fallback — most markets expose funding_rate field)

        If BOTH fail, set DEGRADED sentinel key and return conservative default.
        """
        rate = await self._try_coinalyze(asset)
        if rate is not None:
            return rate

        rate = await self._try_bitget_ticker(asset)
        if rate is not None:
            return rate

        # Both providers exhausted — mark DEGRADED and return fallback.
        await self._redis.setex(
            f"provider:coinalyze:{asset}:funding_rate:degraded", 300, b"1",
        )
        logger.warning(
            "funding_rate_default_used | asset={} | assuming_rate={}",
            asset, self.FALLBACK_RATE,
        )
        return self.FALLBACK_RATE

    async def _try_coinalyze(self, asset: str) -> Decimal | None:
        try:
            raw = await self._redis.get(f"provider:coinalyze:{asset}:funding_rate")
            if not raw:
                return None
            data = msgspec.json.decode(raw)
            rate_str = data.get("funding_rate") or data.get("fundingRate")
            return Decimal(str(rate_str)) if rate_str else None
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("funding_rate_coinalyze_read_failed | asset={} | exc={}", asset, exc)
            return None

    async def _try_bitget_ticker(self, asset: str) -> Decimal | None:
        try:
            raw = await self._redis.get(f"provider:bitget:{asset}:ticker")
            if not raw:
                return None
            ticker = msgspec.json.decode(raw)
            rate_str = ticker.get("funding_rate")
            return Decimal(str(rate_str)) if rate_str else None
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("funding_rate_bitget_read_failed | asset={} | exc={}", asset, exc)
            return None
```

**Critical changes versus the original draft:**
- `for provider in ["coinglass", "coinank", "coinalyze"]` loop is GONE.
  CoinGlass is fully removed; `coinank` was speculative.
- The single-try-with-bare-`except` pattern is replaced by explicit per-provider
  try blocks. `asyncio.CancelledError` is re-raised per `040-errors-and-testing.mdc`.
- DEGRADED sentinel key is set when both providers fail — alerting surfaces
  this to the operator instead of silent fallback.

### Accrual Computation

`compute_accrual()` is a pure function ≤ 40 lines. `get_funding_settlements_in_window()`
iterates hour-by-hour between entry and exit and returns the list of UTC
settlement timestamps.

## Task 5 — Paper Trade Ledger

Create `prometheus/paper/ledger.py`:

`PaperTradeLedger` owns the trade lifecycle. Key surface:

- `open_trade(asset, side, size_usd, leverage, signal_id) -> trade_id`
- `close_trade(trade_id, exit_reason) -> PaperTradeResult`
- `record_funding(trade_id) -> None`  (called by the funding scheduler)
- `portfolio_snapshot(capital_usd) -> PaperPortfolioSnapshot`

### asyncpg Decimal Discipline

`asyncpg` accepts `Decimal` directly for `NUMERIC` columns. The old pattern:

```python
# WRONG — loses precision
await self._pg.execute(
    "INSERT INTO paper_trades (entry_price, entry_fee_usd) VALUES ($1, $2)",
    float(fill.simulated_fill_price),
    float(fill.fee_usd),
)
```

becomes:

```python
# CORRECT — asyncpg handles Decimal natively
await self._pg.execute(
    "INSERT INTO paper_trades (entry_price, entry_fee_usd) VALUES ($1, $2)",
    fill.simulated_fill_price,
    fill.fee_usd,
)
```

Every `INSERT` and `UPDATE` in this session passes `Decimal` directly. Zero
`float()` casts at the DB boundary.

### Logging

All logger calls use positional format:

```python
logger.info(
    "paper_trade_opened | trade_id={} | asset={} | side={} | "
    "fill_price={} | slippage_bps={} | entry_fee_usd={}",
    trade_id, asset, side,
    fill.simulated_fill_price, fill.slippage_bps, fill.fee_usd,
)
```

### Sharpe / Drawdown

`portfolio_snapshot()` computes both `gross_sharpe` and `net_sharpe`. The
annualisation factor assumes ~20 trades/day → `sqrt(20 × 365)`. `gross_sharpe`
is stored for diagnostics only — **no phase-gate logic reads it**.

## Task 6 — PostgreSQL Schema

Append to `schema.sql`:

```sql
CREATE TABLE IF NOT EXISTS paper_trades (
    trade_id                 TEXT PRIMARY KEY,
    asset                    TEXT NOT NULL,
    side                     TEXT NOT NULL,          -- 'long' | 'short'
    leverage                 NUMERIC(6,2) NOT NULL,
    signal_id                TEXT,
    entry_price              NUMERIC(20,8) NOT NULL,
    entry_fill_price         NUMERIC(20,8) NOT NULL,
    entry_fee_usd            NUMERIC(20,8) NOT NULL,
    entry_slippage_usd       NUMERIC(20,8) NOT NULL DEFAULT 0,
    position_size_usd        NUMERIC(20,8) NOT NULL,
    entry_timestamp          TIMESTAMPTZ NOT NULL,
    exit_fill_price          NUMERIC(20,8),
    exit_fee_usd             NUMERIC(20,8),
    exit_slippage_usd        NUMERIC(20,8),
    exit_timestamp           TIMESTAMPTZ,
    exit_reason              TEXT,
    total_funding_cost_usd   NUMERIC(20,8),
    gross_pnl_usd            NUMERIC(20,8),
    net_pnl_usd              NUMERIC(20,8),          -- PRIMARY metric
    gross_pnl_pct            NUMERIC(10,4),
    net_pnl_pct              NUMERIC(10,4),          -- PRIMARY metric
    total_cost_usd           NUMERIC(20,8),
    cost_drag_pct            NUMERIC(10,4),
    is_win                   BOOLEAN,
    status                   TEXT NOT NULL DEFAULT 'open',
    created_at               TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_paper_trades_asset ON paper_trades(asset);
CREATE INDEX IF NOT EXISTS idx_paper_trades_status ON paper_trades(status);
CREATE INDEX IF NOT EXISTS idx_paper_trades_exit_ts ON paper_trades(exit_timestamp DESC);

CREATE OR REPLACE VIEW paper_trading_phase_gate AS
SELECT
    COUNT(*) AS total_trades,
    SUM(CASE WHEN is_win THEN 1 ELSE 0 END)::FLOAT / NULLIF(COUNT(*), 0) AS win_rate,
    SUM(net_pnl_usd) AS total_net_pnl_usd,
    AVG(net_pnl_pct) AS avg_net_pnl_pct,
    MAX(cost_drag_pct) AS max_cost_drag_pct,
    MIN(net_pnl_usd) AS worst_trade_net_usd
FROM paper_trades
WHERE status = 'closed';
```

## Task 7 — Tests (minimum 13)

Create `prometheus/paper/test_paper_trading.py`:

1. Taker fee on $10k notional = $6.00 (at default rates).
2. Maker fee on $10k notional = $2.00.
3. `fee_break_even_pct(5×) == 0.00024`.
4. `FeeCalculator` reads rates from `PolarisSettings` (override rate in test, verify).
5. Slippage on small order = 0.5 × spread; 10-bps spread → 5-bps slippage.
6. Partial fill: size at 10% of 1h volume → 2-candle fill.
7. Funding accrual: long, rate=0.0001, $10k notional → $1.00 per 8h window.
8. Funding settlements in 24h = 3 (00/08/16 UTC).
9. Short receives positive funding when rate > 0.
10. Net P&L < gross P&L for any profitable trade (costs always deducted).
11. Portfolio Sharpe uses net returns only.
12. `gross_sharpe > net_sharpe` at typical Bitget cost levels.
13. Max drawdown on net P&L curve — known sequence.
14. Funding fallback sets DEGRADED sentinel key when both providers empty.
15. `asyncpg` INSERT receives `Decimal` (mock pool, assert arg types).
16. No `coinglass`, no `coinank` in provider list — grep-based.
17. Fee-tier startup probe logs warning when live rate diverges.

## Quality Gates

```bash
# Tests
pytest prometheus/paper/test_paper_trading.py -v

# Type safety
pyright --pythonversion 3.12 prometheus/paper/

# Banned libraries
grep -rEn "aioredis|^import json\b|json\.loads|json\.dumps|CoinGlass|coinglass|coinank" \
    prometheus/paper/ --include="*.py"
# Must return 0 results.

# Loguru kwargs (must be 0)
grep -rEn 'logger\.(info|warning|error|debug|critical|exception)\([^)]*=[^)]*\)' \
    prometheus/paper/ --include="*.py"
# Must return 0 results.

# No float(Decimal) at DB boundary
grep -rEn "float\((self\.)?(_)?[a-z_]+\.(price|fee|size|pnl|cost|slippage|funding)" \
    prometheus/paper/ --include="*.py" | grep -v test_
# Must return 0 results.

# Per-call httpx
grep -rn "async with httpx.AsyncClient" prometheus/paper/
# Must return 0 results.
```

## Anti-Pattern Checklist
- [ ] No `import aioredis` — `import redis.asyncio as redis_asyncio`
- [ ] No `import json` — `import msgspec`
- [ ] No `json.loads` / `json.dumps` — `msgspec.json.decode` / `encode`
- [ ] No `CoinGlass` / `coinglass` / `coinank` in any string, variable, or provider list
- [ ] No Loguru keyword-argument calls — positional `"{}"` format only
- [ ] No `float(decimal_value)` at DB boundary — asyncpg takes Decimal directly
- [ ] Fees sourced from `PolarisSettings`, not module-level constants
- [ ] Startup probe compares configured vs. live fees
- [ ] Funding engine sets `:degraded` sentinel key when both providers fail
- [ ] Per-provider try blocks, no nested bare-except funnel
- [ ] `asyncio.CancelledError` re-raised in every except block
- [ ] `gross_sharpe` used only for diagnostic logging
- [ ] `net_sharpe` is the phase-gate metric
- [ ] All functions ≤ 40 lines
- [ ] No per-call `httpx.AsyncClient` — singleton injection
