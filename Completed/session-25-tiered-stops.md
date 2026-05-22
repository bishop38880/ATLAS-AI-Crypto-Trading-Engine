# SESSION 25 — Tiered Stop-Loss as Placed Bitget Orders

## Context Files
@prometheus/oms/ @prometheus/execution/bitget_execution_client.py @atlas/shared/config.py @schema.sql

## Prerequisites
- Session 22 (Kill Switch) operational.
- **Session 22B (BitgetExecutionClient) — hard prerequisite.** All order
  placement and cancellation go through this client. The single canonical
  signing module is `prometheus/shared/bitget_signing.py`.
- Session 23 (Paper Trading Fidelity Engine) — paper guard is reused.
- Session 24 (Execution Risk Model) — capital guard runs before stops are
  placed.

## Goal
Place the 33/33/34 stop-loss ladder as actual **standalone trigger orders** on
Bitget at entry time. The exit scorer detects exit conditions via signal
analysis, which is too slow for fast altcoin cascades. Pre-placed conditional
orders execute in milliseconds regardless of analysis cycle timing.

---

## NON-NEGOTIABLE INVARIANTS

1. **Pyright only.** `pyright --pythonversion 3.12`.
2. **No banned libraries.** `aioredis` → `redis.asyncio`; stdlib `json` →
   `msgspec`; all the usual banned list.
3. **`PolarisSettings` only.**
4. **40-line function limit.**
5. **Loguru POSITIONAL format only.**
6. **Decimal everywhere.** `asyncpg` takes `Decimal` directly.
7. **CRITICAL — BITGET `size` IS BASE COIN, NOT USD NOTIONAL.** Bitget USDT-M
   perpetual `size` field is denominated in the **base asset** (BTC, ETH, PEPE).
   Passing USD notional as `size` means Bitget interprets `size=10000` as
   "10,000 BTC" (≈ $700M at current price) — rejected for margin, stop never
   lives. USD-notional-to-base-coin conversion must happen BEFORE the API call,
   and must respect the contract's `size_multiplier` and `min_trade_num`.

   Use `bitget_client.usd_notional_to_base_coin_size(...)` — do NOT multiply
   inline. This is Invariant 7 and it is absolute.
8. **Standalone trigger orders → `place-plan-order`, NOT `place-tpsl-order`.**
   Stop ladder entries are standalone conditional orders. The `place-tpsl-order`
   endpoint is for position-attached TP/SL plans and requires different fields
   (`planType`, `holdSide`, etc.). Mixing `triggerPrice` and `stopLossTriggerPrice`
   in one body is a Bitget V2 400 error.

   The canonical endpoint is `place-plan-order` with `plan_type="normal_plan"`.
9. **No raw HTTP in this session.** All order placement, cancellation, and
   signing happen inside `BitgetExecutionClient`. The stop ladder calls
   injected client methods only. No `async with httpx.AsyncClient(...)`, no
   `_sign_headers()` on the ladder class, no raw JSON bodies.
10. **Paper-trading guard.** When `settings.paper_trading_enabled=True`, the
    ladder simulates placement (writes to PostgreSQL with status='paper_placed')
    but never calls the Bitget client. This invariant applies to every method
    that interacts with the exchange.
11. **ATR source is canonical.** ATR values come from the Redis key
    `atlas:atr:{asset}:{timeframe}` (written by ATLAS OHLCV ingester). Do not
    recompute ATR inside PROMETHEUS.

---

## Task 1 — Models

Create `prometheus/stops/models.py`:

```python
from decimal import Decimal
from datetime import datetime
from typing import Literal
from pydantic import BaseModel


class StopTier(BaseModel, frozen=True):
    tier: int                                    # 1, 2, or 3
    price: Decimal                               # trigger price
    size_pct: Decimal                            # fraction of position (0.33 / 0.33 / 0.34)
    size_usd: Decimal                            # USD notional for this tier
    size_base_coin: Decimal | None = None        # set after conversion at placement time
    bitget_order_id: str | None = None
    status: Literal["pending", "placed", "paper_placed", "triggered", "cancelled", "failed"] = "pending"


class TieredStopLoss(BaseModel, frozen=True):
    trade_id: str
    asset: str
    position_side: Literal["long", "short"]
    entry_price: Decimal
    position_notional_usd: Decimal
    leverage: Decimal                            # audit only — not used in stop math

    tier_1: StopTier                             # 33% @ -1× ATR (or -5% fixed)
    tier_2: StopTier                             # 33% @ -2× ATR (or -10%)
    tier_3: StopTier                             # 34% @ -3× ATR (or -15%)

    atr_value: Decimal | None = None
    used_atr_pricing: bool = False
    placed_at: datetime | None = None
    all_placed: bool = False
```

## Task 2 — Stop Ladder Calculator

Create `prometheus/stops/calculator.py`:

ATR-aware stop pricing. Primary: ATR-based stops (1×, 2×, 3× ATR_14 from the
canonical Redis key). Fallback: fixed percentages (−5%, −10%, −15%). Pick the
TIGHTER of the two per tier so we never exceed the fixed ceiling.

Allocation: 33% / 33% / 34% (tier 3 absorbs rounding).

```python
import msgspec
import redis.asyncio as redis_asyncio
from decimal import Decimal
from typing import Literal


class StopLadderCalculator:
    FIXED_STOP_PCTS = [Decimal("0.05"), Decimal("0.10"), Decimal("0.15")]
    ATR_MULTIPLES   = [Decimal("1.0"), Decimal("2.0"), Decimal("3.0")]
    TIER_SIZES      = [Decimal("0.33"), Decimal("0.33"), Decimal("0.34")]
    ATR_KEY_FMT     = "atlas:atr:{asset}:{timeframe}"

    def __init__(self, redis_client: redis_asyncio.Redis) -> None:
        self._redis = redis_client

    async def calculate(
        self,
        trade_id: str,
        asset: str,
        side: Literal["long", "short"],
        entry_price: Decimal,
        position_notional_usd: Decimal,
        leverage: Decimal,
        timeframe: str = "1h",
    ) -> TieredStopLoss:
        atr = await self._fetch_atr(asset, timeframe)
        tiers = self._build_tiers(entry_price, side, position_notional_usd, atr)
        return TieredStopLoss(
            trade_id=trade_id,
            asset=asset,
            position_side=side,
            entry_price=entry_price,
            position_notional_usd=position_notional_usd,
            leverage=leverage,
            tier_1=tiers[0],
            tier_2=tiers[1],
            tier_3=tiers[2],
            atr_value=atr,
            used_atr_pricing=atr is not None,
        )

    async def _fetch_atr(self, asset: str, timeframe: str) -> Decimal | None:
        raw = await self._redis.get(self.ATR_KEY_FMT.format(asset=asset, timeframe=timeframe))
        if raw is None:
            return None
        data = msgspec.json.decode(raw)
        return Decimal(str(data.get("atr")))
```

`_build_tiers()` is a small helper: for each of the three tiers, compute both
ATR-based and fixed-pct stop prices, take the tighter, allocate `size_pct`
and `size_usd`.

## Task 3 — Stop Placer

Create `prometheus/stops/placer.py`:

**This is where the size-unit and endpoint bugs used to live.** The new
implementation delegates entirely to `BitgetExecutionClient`.

```python
import redis.asyncio as redis_asyncio
import asyncpg
from decimal import Decimal
from datetime import datetime, timezone
from loguru import logger

from prometheus.execution.bitget_execution_client import (
    BitgetExecutionClient,
    PlacePlanOrderRequest,
)
from prometheus.stops.models import StopTier, TieredStopLoss


class StopLadderPlacer:
    """Places the three stop tiers as standalone plan orders on Bitget."""

    def __init__(
        self,
        redis_client: redis_asyncio.Redis,
        pg_pool: asyncpg.Pool,
        bitget_client: BitgetExecutionClient,   # injected at startup
        paper_trading: bool = True,
    ) -> None:
        self._redis = redis_client
        self._pg = pg_pool
        self._bitget = bitget_client
        self._paper = paper_trading

    async def place_all(self, ladder: TieredStopLoss) -> TieredStopLoss:
        """Place all three tiers. Returns updated ladder with order IDs or paper status."""
        updated_tiers = []
        for tier in (ladder.tier_1, ladder.tier_2, ladder.tier_3):
            result = await self._place_tier(ladder, tier)
            updated_tiers.append(result)
            await self._persist_tier(ladder.trade_id, result)

        return ladder.model_copy(update={
            "tier_1": updated_tiers[0],
            "tier_2": updated_tiers[1],
            "tier_3": updated_tiers[2],
            "all_placed": all(t.status in ("placed", "paper_placed") for t in updated_tiers),
            "placed_at": datetime.now(tz=timezone.utc),
        })

    async def _place_tier(self, ladder: TieredStopLoss, tier: StopTier) -> StopTier:
        """Single-tier placement — paper-aware."""
        if self._paper:
            return tier.model_copy(update={
                "status": "paper_placed",
                "bitget_order_id": f"paper-{ladder.trade_id}-{tier.tier}",
            })

        symbol = f"{ladder.asset}USDT"
        close_side = "sell" if ladder.position_side == "long" else "buy"

        # CRITICAL: convert USD notional to base-coin size via the execution
        # client. Never pass USD directly as `size` — Bitget interprets
        # `size` in base coin.
        size_base = await self._bitget.usd_notional_to_base_coin_size(
            symbol=symbol,
            notional_usd=tier.size_usd,
            reference_price=tier.price,
        )

        result = await self._bitget.place_plan_order(PlacePlanOrderRequest(
            symbol=symbol,
            product_type="USDT-FUTURES",
            margin_mode="isolated",
            margin_coin="USDT",
            size=size_base,
            side=close_side,
            trade_side="close",
            trigger_price=tier.price,
            trigger_type="mark_price",
            order_type="market",
            plan_type="normal_plan",           # required by Bitget V2 — was missing in old draft
        ))

        if result.success and result.order_id:
            return tier.model_copy(update={
                "bitget_order_id": result.order_id,
                "size_base_coin": size_base,
                "status": "placed",
            })

        logger.error(
            "stop_placement_failed | tier={} | trade_id={} | errors={} | "
            "size_base={} | trigger={}",
            tier.tier, ladder.trade_id, result.errors, size_base, tier.price,
        )
        return tier.model_copy(update={"status": "failed"})

    async def cancel_tier(self, order_id: str) -> bool:
        """Cancel a placed plan order via the execution client."""
        return await self._bitget.cancel_plan_order(
            order_id=order_id, product_type="USDT-FUTURES"
        )

    async def _persist_tier(self, trade_id: str, tier: StopTier) -> None:
        """Write tier to stop_ladder_tiers. asyncpg takes Decimal directly."""
        await self._pg.execute("""
            INSERT INTO stop_ladder_tiers
                (trade_id, tier, price, size_pct, size_usd, size_base_coin,
                 bitget_order_id, status)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            ON CONFLICT (trade_id, tier) DO UPDATE SET
                bitget_order_id = EXCLUDED.bitget_order_id,
                size_base_coin  = EXCLUDED.size_base_coin,
                status          = EXCLUDED.status
        """,
            trade_id, tier.tier,
            tier.price, tier.size_pct, tier.size_usd, tier.size_base_coin,
            tier.bitget_order_id, tier.status,
        )
```

**What's explicitly removed:**
- `_sign_headers()` method on this class — signing is the execution client's
  concern, handled in `prometheus/shared/bitget_signing.py`.
- `async with httpx.AsyncClient(timeout=self.TIMEOUT_S) as client:` — the
  client is a long-lived singleton injected through the execution client.
- Raw `json.dumps(...)` body construction — the execution client builds and
  signs the request.
- `place-tpsl-order` endpoint — replaced by `place-plan-order` via
  `bitget_client.place_plan_order(PlacePlanOrderRequest(...))`.
- `size=str(tier.size_usd)` — replaced by `size=size_base` after
  `usd_notional_to_base_coin_size()`.
- `"stopLossTriggerPrice": str(tier.price)` — not a field on `place-plan-order`.

## Task 4 — Schema

Append to `schema.sql`:

```sql
CREATE TABLE IF NOT EXISTS stop_ladder_tiers (
    trade_id         TEXT NOT NULL,
    tier             INT NOT NULL CHECK (tier IN (1, 2, 3)),
    price            NUMERIC(20,8) NOT NULL,
    size_pct         NUMERIC(10,6) NOT NULL,
    size_usd         NUMERIC(20,8) NOT NULL,
    size_base_coin   NUMERIC(30,10),            -- populated at placement time
    bitget_order_id  TEXT,
    status           TEXT NOT NULL DEFAULT 'pending',
    placed_at        TIMESTAMPTZ,
    triggered_at     TIMESTAMPTZ,
    PRIMARY KEY (trade_id, tier)
);
CREATE INDEX IF NOT EXISTS idx_stop_ladder_status ON stop_ladder_tiers(status);
```

## Task 5 — Tests (minimum 9)

Create `prometheus/stops/test_stop_ladder.py`:

1. Tier size fractions sum to 1.0 exactly (0.33 + 0.33 + 0.34).
2. ATR tighter than fixed → ATR prices used; `used_atr_pricing=True`.
3. Fixed tighter than ATR → fixed prices used; `used_atr_pricing=False`.
4. No ATR in Redis → fixed prices used; no crash.
5. Paper trading ON → `_place_tier` returns `paper_placed`, never calls Bitget
   client (mock + assert_not_called).
6. Paper trading OFF → mock Bitget client, verify `place_plan_order` called
   with `plan_type="normal_plan"`, `trade_side="close"`, `size` matching
   `usd_notional_to_base_coin_size` return.
7. `size_base_coin` is populated and persisted — NOT USD.
8. Cancel tier → `bitget_client.cancel_plan_order` called with correct args.
9. Failed placement → tier status='failed', error logged with positional format.
10. **Grep test:** no `"place-tpsl-order"` literal in the session files.
11. **Grep test:** no `str(tier.size_usd)` as an order-body value.
12. **Grep test:** no `_sign_headers` method on `StopLadderPlacer`.
13. ATR fetch uses canonical key format `atlas:atr:{asset}:{timeframe}`.

## Quality Gates

```bash
pytest prometheus/stops/test_stop_ladder.py -v
pyright --pythonversion 3.12 prometheus/stops/

# No raw HTTP / signing in this session
grep -rn "async with httpx.AsyncClient\|_sign_headers\|place-tpsl-order" \
    prometheus/stops/ --include="*.py"
# Must return 0.

# No USD-as-Bitget-size bug
grep -rEn '"size":\s*str\([^)]*usd\)|size=str\([^)]*usd\)' \
    prometheus/stops/ --include="*.py"
# Must return 0.

# plan_type present (positive check)
grep -rn "plan_type" prometheus/stops/placer.py
# Must return at least 1.

# Banned libraries
grep -rEn "aioredis|^import json\b|json\.loads|json\.dumps|CoinGlass" \
    prometheus/stops/ --include="*.py"
# Must return 0.

# Loguru kwargs
grep -rEn 'logger\.(info|warning|error|debug|critical|exception)\([^)]*=[^)]*\)' \
    prometheus/stops/ --include="*.py"
# Must return 0.
```

## Anti-Pattern Checklist
- [ ] No `place-tpsl-order` — `place-plan-order` via `BitgetExecutionClient`
- [ ] `plan_type="normal_plan"` present in request construction
- [ ] `size` is base coin, populated via `usd_notional_to_base_coin_size()`
- [ ] `size_base_coin` field on `StopTier` populated and persisted
- [ ] No `_sign_headers` on placer class — signing in execution client
- [ ] No `async with httpx.AsyncClient(...)` in this session
- [ ] Constructor takes injected `BitgetExecutionClient` — no API-key fields
- [ ] ATR sourced from `atlas:atr:{asset}:{timeframe}` canonical Redis key
- [ ] Paper-trading guard wraps every exchange call
- [ ] No `import aioredis` — `redis.asyncio`
- [ ] No `import json` — `msgspec`
- [ ] No Loguru kwargs — positional `"{}"` format only
- [ ] `asyncpg` takes `Decimal` directly
- [ ] All functions ≤ 40 lines
