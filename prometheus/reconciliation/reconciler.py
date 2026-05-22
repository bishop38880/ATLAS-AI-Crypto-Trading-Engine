"""Position reconciliation loop — OMS ↔ Bitget comparison every 60 seconds.

Production execution uses Bitget USDT-margined perpetuals (REST position APIs).
Older white papers referenced Kraken Futures-style endpoints; this reconciler
matches **PostgreSQL ``oms_open_positions``** and the **Redis executor mirror**
(``positions:open`` + ``position:{asset}:state``) against live Bitget positions.

Detects discrepancies, publishes ``SystemHaltEvent`` for critical conditions,
but NEVER closes or opens positions. The kill switch consumes halt events.

Import notes:
    - ``BitgetExecutionClient`` only.
    - ``redis.asyncio`` for Redis.
    - ``msgspec`` for serialization.
    - Loguru POSITIONAL format only.
"""

from __future__ import annotations

import asyncio
import random
import time
from datetime import datetime, timezone
from decimal import ROUND_DOWN, Decimal
from typing import Literal

import asyncpg
import msgspec
import redis.asyncio as redis_asyncio
from loguru import logger

from prometheus.execution.bitget_client import BitgetExecutionClient
from prometheus.execution.models import BitgetPosition
from prometheus.kill_switch.schemas import SystemHaltEvent
from prometheus.reconciliation.models import (
    DiscrepancyType,
    ReconciliationDiscrepancy,
    ReconciliationReport,
)


SIZE_TOLERANCE_PCT = Decimal("0.02")          # 2 % — allows minor rounding
HALT_CHANNEL = "prometheus:system_halt"
PHANTOM_PERSISTENCE_RUNS = 2


class PositionReconciler:
    """60-second OMS ↔ Bitget reconciliation loop.

    Detects discrepancies.  Publishes SystemHaltEvent for critical
    conditions.  Never closes or opens positions.
    """

    RECONCILE_INTERVAL_S = 60
    JITTER_S = 5

    def __init__(
        self,
        redis_client: redis_asyncio.Redis,       # type: ignore[type-arg]
        pg_pool: asyncpg.Pool,
        bitget_client: BitgetExecutionClient,
        paper_trading: bool = True,
    ) -> None:
        self._redis = redis_client
        self._pg = pg_pool
        self._bitget = bitget_client
        self._paper = paper_trading
        self._phantom_tracker: dict[str, int] = {}  # asset → consecutive runs

    # ── Main Loop ─────────────────────────────────────────────────────

    async def run_forever(self) -> None:
        """Main loop — cancelled via ``asyncio.CancelledError``."""
        logger.info(
            "reconciliation_loop_started | interval_s={}",
            self.RECONCILE_INTERVAL_S,
        )
        while True:
            try:
                report = await self.reconcile_once()
                await self._persist_report(report)
                if report.halt_triggered:
                    logger.critical(
                        "RECONCILIATION_HALT_TRIGGERED | discrepancies={}",
                        len(report.discrepancies),
                    )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("reconciliation_loop_error | exc={}", exc)
            await self._sleep_with_jitter()

    async def _sleep_with_jitter(self) -> None:
        """Jittered sleep — ± 5 s to avoid thundering herd."""
        jitter = random.uniform(-self.JITTER_S, self.JITTER_S)
        await asyncio.sleep(self.RECONCILE_INTERVAL_S + jitter)

    # ── Per-Run Logic ─────────────────────────────────────────────────

    async def reconcile_once(self) -> ReconciliationReport:
        """Run a single reconciliation pass."""
        t0 = time.monotonic()
        cooldown_report = await self._maybe_skip_cooldown(t0)
        if cooldown_report is not None:
            return cooldown_report

        oms_positions, bitget_positions = await asyncio.gather(
            self._load_oms_positions(),
            self._bitget.list_open_positions(),
        )
        discrepancies: list[ReconciliationDiscrepancy] = []
        halt_needed = await self._compare_pg_oms_to_redis_mirror(
            oms_positions, discrepancies,
        )
        halt_needed = (
            await self._compare_oms_to_bitget(
                oms_positions, bitget_positions, discrepancies,
            )
        ) or halt_needed
        phantom_halt = self._detect_phantoms(
            oms_positions, bitget_positions, discrepancies,
        )
        halt_needed = halt_needed or phantom_halt
        await self._check_stops(discrepancies)

        if halt_needed:
            await self._publish_halt(
                "RECONCILER_MISMATCH",
                {"discrepancy_count": len(discrepancies)},
            )

        return self._build_report(t0, oms_positions, bitget_positions,
                                  discrepancies, halt_needed)

    async def _maybe_skip_cooldown(
        self, t0: float,
    ) -> ReconciliationReport | None:
        """Return an empty report if in-flight orders detected, else None."""
        if not await self._has_inflight_orders():
            return None
        logger.info(
            "reconciliation_cooldown_skip | in_flight_orders_detected",
        )
        return self._build_report(t0, [], [], [], False)

    def _build_report(
        self,
        t0: float,
        oms_positions: list[dict[str, object]],
        bitget_positions: list[BitgetPosition],
        discrepancies: list[ReconciliationDiscrepancy],
        halt_needed: bool,
    ) -> ReconciliationReport:
        """Construct final ReconciliationReport."""
        duration_ms = int((time.monotonic() - t0) * 1000)
        return ReconciliationReport(
            run_at=datetime.now(tz=timezone.utc),
            duration_ms=duration_ms,
            oms_positions_checked=len(oms_positions),
            bitget_positions_found=len(bitget_positions),
            discrepancies=discrepancies,
            all_clear=len(discrepancies) == 0,
            halt_triggered=halt_needed,
        )

    # ── In-flight order cooldown ────────────────────────────────────────

    async def _has_inflight_orders(self) -> bool:
        """Check if any orders are currently in-flight on Bitget.

        Returns True if plan orders or regular orders are in a
        pending/live state, signalling the reconciler should skip this
        run to avoid false SIZE_DIVERGENCE alerts.
        """
        plan_orders = await self._bitget.list_open_plan_orders()
        if plan_orders:
            return True
        open_orders = await self._bitget.list_open_orders()
        if open_orders:
            return True
        return False

    # ── OMS → Bitget comparison ───────────────────────────────────────

    async def _compare_oms_to_bitget(
        self,
        oms_positions: list[dict[str, object]],
        bitget_positions: list[BitgetPosition],
        discrepancies: list[ReconciliationDiscrepancy],
    ) -> bool:
        """Compare each OMS position against Bitget. Returns True if halt needed."""
        halt_needed = False
        bitget_by_symbol = _index_bitget_positions(bitget_positions)

        for oms_pos in oms_positions:
            asset = str(oms_pos.get("asset", ""))
            symbol = _asset_to_symbol(asset)
            oms_side = str(oms_pos.get("position_side", "long"))
            bitget_pos = bitget_by_symbol.get(symbol)

            if bitget_pos is None:
                discrepancies.append(_missing_position(asset, oms_pos))
                continue

            halt = self._check_margin_mode(asset, bitget_pos, discrepancies)
            halt_needed = halt_needed or halt

            halt = self._check_side(asset, oms_side, bitget_pos, discrepancies)
            halt_needed = halt_needed or halt

            size_disc = await self._check_size(asset, oms_pos, bitget_pos)
            if size_disc is not None:
                discrepancies.append(size_disc)

        return halt_needed

    # ── Size divergence (entry notionals) ─────────────────────────────

    async def _check_size(
        self,
        asset: str,
        oms_pos: dict[str, object],
        bitget_pos: BitgetPosition,
    ) -> ReconciliationDiscrepancy | None:
        """Size divergence check using ENTRY notionals."""
        oms_notional = Decimal(str(oms_pos.get("position_notional_usd", "0")))
        bitget_notional_at_entry = (
            bitget_pos.total * bitget_pos.average_open_price
        )
        if oms_notional <= 0:
            return None

        spec = await self._bitget.get_contract_spec(
            _asset_to_symbol(asset),
        )
        divergence = _compute_divergence(
            oms_notional, bitget_notional_at_entry, spec.price_end_step,
        )
        if divergence <= SIZE_TOLERANCE_PCT:
            return None

        severity: str = "high" if divergence > Decimal("0.10") else "medium"
        return self._size_discrepancy(
            asset, oms_notional, bitget_notional_at_entry,
            bitget_pos, severity,
        )

    def _size_discrepancy(
        self,
        asset: str,
        oms_notional: Decimal,
        bitget_notional: Decimal,
        bitget_pos: BitgetPosition,
        severity: Literal["low", "medium", "high", "critical"],
    ) -> ReconciliationDiscrepancy:
        """Build SIZE_DIVERGENCE discrepancy record."""
        return ReconciliationDiscrepancy(
            asset=asset,
            discrepancy_type=DiscrepancyType.SIZE_DIVERGENCE,
            oms_value="{:.2f} USD".format(oms_notional),
            bitget_value=(
                "{:.2f} USD (total={:.6f} @ avg_open={:.2f})".format(
                    bitget_notional,
                    bitget_pos.total,
                    bitget_pos.average_open_price,
                )
            ),
            severity=severity,
            auto_action_taken=None,
            requires_human=True,
            detected_at=datetime.now(tz=timezone.utc),
        )

    # ── Side mismatch ─────────────────────────────────────────────────

    def _check_side(
        self,
        asset: str,
        oms_side: str,
        bitget_pos: BitgetPosition,
        discrepancies: list[ReconciliationDiscrepancy],
    ) -> bool:
        """Returns True if halt needed (side mismatch = immediate halt)."""
        if oms_side == bitget_pos.side:
            return False

        discrepancies.append(
            ReconciliationDiscrepancy(
                asset=asset,
                discrepancy_type=DiscrepancyType.SIDE_MISMATCH,
                oms_value=oms_side,
                bitget_value=bitget_pos.side,
                severity="critical",
                auto_action_taken="halt_published",
                requires_human=True,
                detected_at=datetime.now(tz=timezone.utc),
            ),
        )
        return True

    # ── Margin mode check ─────────────────────────────────────────────

    def _check_margin_mode(
        self,
        asset: str,
        bitget_pos: BitgetPosition,
        discrepancies: list[ReconciliationDiscrepancy],
    ) -> bool:
        """Cross margin → critical, immediate halt."""
        if bitget_pos.margin_mode == "isolated":
            return False

        discrepancies.append(
            ReconciliationDiscrepancy(
                asset=asset,
                discrepancy_type=DiscrepancyType.MARGIN_MODE_WRONG,
                oms_value="isolated",
                bitget_value=bitget_pos.margin_mode,
                severity="critical",
                auto_action_taken="halt_published",
                requires_human=True,
                detected_at=datetime.now(tz=timezone.utc),
            ),
        )
        return True

    # ── Phantom detection ─────────────────────────────────────────────

    def _detect_phantoms(
        self,
        oms_positions: list[dict[str, object]],
        bitget_positions: list[BitgetPosition],
        discrepancies: list[ReconciliationDiscrepancy],
    ) -> bool:
        """Detect Bitget positions with no OMS row. Returns True if halt needed."""
        oms_symbols = {
            _asset_to_symbol(str(p.get("asset", "")))
            for p in oms_positions
        }
        halt_needed = False
        seen_phantoms: set[str] = set()

        for bp in bitget_positions:
            if bp.symbol not in oms_symbols:
                seen_phantoms.add(bp.symbol)
                should_halt = self._track_phantom(bp.symbol, True)
                discrepancies.append(
                    _phantom_discrepancy(bp, should_halt),
                )
                if should_halt:
                    halt_needed = True

        self._clear_resolved_phantoms(seen_phantoms)
        return halt_needed

    def _track_phantom(self, asset: str, is_phantom_this_run: bool) -> bool:
        """Returns True if phantom has persisted long enough to halt."""
        if is_phantom_this_run:
            self._phantom_tracker[asset] = (
                self._phantom_tracker.get(asset, 0) + 1
            )
        else:
            self._phantom_tracker.pop(asset, None)
        return self._phantom_tracker.get(asset, 0) >= PHANTOM_PERSISTENCE_RUNS

    def _clear_resolved_phantoms(self, seen: set[str]) -> None:
        """Clear phantom counter for assets resolved this run."""
        for key in list(self._phantom_tracker):
            if key not in seen:
                self._phantom_tracker.pop(key, None)

    # ── Stop ladder check ─────────────────────────────────────────────

    async def _check_stops(
        self,
        discrepancies: list[ReconciliationDiscrepancy],
    ) -> None:
        """Verify placed stop ladder tiers still exist on Bitget."""
        placed_tiers = await self._load_placed_stops()
        if not placed_tiers:
            return

        plan_orders = await self._bitget.list_open_plan_orders()
        bitget_order_ids = {po.order_id for po in plan_orders}

        for tier in placed_tiers:
            order_id = str(tier.get("bitget_order_id", ""))
            if order_id and order_id not in bitget_order_ids:
                discrepancies.append(
                    _stop_missing_discrepancy(tier),
                )

    async def _load_placed_stops(self) -> list[dict[str, object]]:
        """Load stop tiers with status = 'placed' from PostgreSQL."""
        query = (
            "SELECT trade_id, tier, price, bitget_order_id "
            "FROM stop_ladder_tiers WHERE status = 'placed'"
        )
        async with self._pg.acquire() as conn:
            rows = await conn.fetch(query)
        return [dict(r) for r in rows]

    # ── OMS Data Access ───────────────────────────────────────────────

    async def _load_oms_positions(self) -> list[dict[str, object]]:
        """Load open OMS positions from PostgreSQL."""
        query = (
            "SELECT asset, position_side, position_notional_usd "
            "FROM oms_open_positions WHERE status = 'open'"
        )
        async with self._pg.acquire() as conn:
            rows = await conn.fetch(query)
        return [dict(r) for r in rows]

    # ── Redis executor mirror vs Postgres OMS ─────────────────────────

    async def _compare_pg_oms_to_redis_mirror(
        self,
        oms_positions: list[dict[str, object]],
        discrepancies: list[ReconciliationDiscrepancy],
    ) -> bool:
        """Ensure Redis ``positions:open`` matches Postgres OMS assets and sides."""
        redis_symbols = await self._redis_open_symbols()
        pg_by_asset = {
            _normalize_internal_asset(str(row.get("asset", ""))): str(
                row.get("position_side", ""),
            ).lower()
            for row in oms_positions
        }
        pg_set = set(pg_by_asset.keys()) - {""}
        redis_set = {_normalize_internal_asset(sym) for sym in redis_symbols} - {""}

        if pg_set != redis_set:
            discrepancies.append(
                ReconciliationDiscrepancy(
                    asset="*",
                    discrepancy_type=DiscrepancyType.OMS_REDIS_DIVERGENCE,
                    oms_value="open_assets={}".format(sorted(pg_set)),
                    bitget_value="redis_open={}".format(sorted(redis_set)),
                    severity="critical",
                    auto_action_taken="halt_published",
                    requires_human=True,
                    detected_at=datetime.now(tz=timezone.utc),
                ),
            )
            return True

        if not pg_set:
            return False

        redis_sides = await self._redis_position_sides(list(redis_symbols))
        halt = False
        for asset_base, pg_side in pg_by_asset.items():
            if not asset_base:
                continue
            rs = redis_sides.get(asset_base, "")
            if not rs:
                continue
            if pg_side and rs and pg_side != rs:
                discrepancies.append(
                    ReconciliationDiscrepancy(
                        asset=asset_base,
                        discrepancy_type=DiscrepancyType.OMS_REDIS_DIVERGENCE,
                        oms_value="side={}".format(pg_side),
                        bitget_value="redis_side={}".format(rs),
                        severity="critical",
                        auto_action_taken="halt_published",
                        requires_human=True,
                        detected_at=datetime.now(tz=timezone.utc),
                    ),
                )
                halt = True
        return halt

    async def _redis_open_symbols(self) -> list[str]:
        """Read ``positions:open`` as a list of wire symbols."""
        try:
            raw = await asyncio.wait_for(
                self._redis.get("positions:open"),
                timeout=5.0,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(
                "reconciler_redis_positions_open_failed | err={}",
                str(exc),
            )
            return []
        if raw is None:
            return []
        try:
            if isinstance(raw, str):
                payload = raw.encode("utf-8")
            else:
                payload = bytes(raw)
            decoded = msgspec.json.decode(payload)
        except Exception as exc:
            logger.warning(
                "reconciler_redis_positions_open_decode_failed | err={}",
                str(exc),
            )
            return []
        if not isinstance(decoded, list):
            return []
        return [str(x) for x in decoded if str(x).strip() != ""]

    async def _redis_position_sides(self, symbols: list[str]) -> dict[str, str]:
        """Map normalised asset base → side from ``position:{sym}:state``."""
        if not symbols:
            return {}
        keys = ["position:{}:state".format(sym) for sym in symbols]
        try:
            raw_vals = await asyncio.wait_for(self._redis.mget(keys), timeout=5.0)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("reconciler_redis_state_mget_failed | err={}", str(exc))
            return {}

        sides: dict[str, str] = {}
        for sym, rv in zip(symbols, raw_vals, strict=True):
            if rv is None:
                continue
            try:
                if isinstance(rv, str):
                    rv_b = rv.encode("utf-8")
                else:
                    rv_b = rv
                st = msgspec.json.decode(rv_b, type=dict)
            except Exception:
                continue
            side_raw = st.get("side")
            if side_raw is None:
                continue
            base = _normalize_internal_asset(sym)
            sides[base] = str(side_raw).lower()
        return sides

    # ── Halt Publication ──────────────────────────────────────────────

    async def _publish_halt(
        self, reason: str, details: dict[str, object],
    ) -> None:
        """Publish a SystemHaltEvent to Redis channel + persistent key."""
        event = SystemHaltEvent(
            event_type="HALT",
            reason=reason,                       # type: ignore[arg-type]
            triggered_by="reconciler",
            timestamp_iso=datetime.now(tz=timezone.utc).isoformat(),
            details={k: str(v) for k, v in details.items()},
        )
        encoded = msgspec.json.encode(event)
        await self._redis.publish(HALT_CHANNEL, encoded)
        # Persistent halt key — kill switch reads this on startup
        await self._redis.set("prometheus:trading_halted", encoded)
        logger.critical(
            "reconciler_halt_published | reason={} | details={}",
            reason,
            details,
        )

    # ── Report Persistence ────────────────────────────────────────────

    async def _persist_report(self, report: ReconciliationReport) -> None:
        """Write reconciliation report to PostgreSQL."""
        discrepancies_payload = msgspec.json.encode(
            [d.model_dump(mode="json") for d in report.discrepancies],
        )
        query = (
            "INSERT INTO reconciliation_reports "
            "(run_at, duration_ms, oms_positions_checked, "
            "bitget_positions_found, discrepancy_count, all_clear, "
            "halt_triggered, discrepancies) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7, $8)"
        )
        async with self._pg.acquire() as conn:
            await conn.execute(
                query,
                report.run_at,
                report.duration_ms,
                report.oms_positions_checked,
                report.bitget_positions_found,
                len(report.discrepancies),
                report.all_clear,
                report.halt_triggered,
                discrepancies_payload.decode(),
            )


# ── Pure helpers (outside class) ──────────────────────────────────────


def _index_bitget_positions(
    positions: list[BitgetPosition],
) -> dict[str, BitgetPosition]:
    """Index Bitget positions by symbol for O(1) lookup."""
    return {p.symbol: p for p in positions}


def _normalize_internal_asset(symbol: str) -> str:
    """Normalise BTC, BTC/USDT, BTCUSDT → ``BTC`` for OMS comparisons."""
    s = symbol.upper().strip().replace("-", "").replace("_", "").replace("/", "")
    if s.endswith("USDT") and len(s) > 4:
        return s[:-4]
    return s


def _asset_to_symbol(asset: str) -> str:
    """Convert OMS asset name to Bitget symbol (e.g. BTC → BTCUSDT)."""
    asset = asset.upper().replace("USDT", "")
    return "{}USDT".format(asset)


def _missing_position(
    asset: str, oms_pos: dict[str, object],
) -> ReconciliationDiscrepancy:
    """Build discrepancy for OMS position missing from Bitget."""
    return ReconciliationDiscrepancy(
        asset=asset,
        discrepancy_type=DiscrepancyType.POSITION_MISMATCH,
        oms_value="open ({})".format(oms_pos.get("position_side", "unknown")),
        bitget_value="no position",
        severity="high",
        auto_action_taken=None,
        requires_human=True,
        detected_at=datetime.now(tz=timezone.utc),
    )


def _phantom_discrepancy(
    bp: BitgetPosition, should_halt: bool,
) -> ReconciliationDiscrepancy:
    """Build discrepancy for Bitget position not in OMS."""
    return ReconciliationDiscrepancy(
        asset=bp.symbol,
        discrepancy_type=DiscrepancyType.PHANTOM_POSITION,
        oms_value="no record",
        bitget_value="{} {} total={:.6f}".format(
            bp.symbol, bp.side, bp.total,
        ),
        severity="critical" if should_halt else "high",
        auto_action_taken="halt_published" if should_halt else None,
        requires_human=True,
        detected_at=datetime.now(tz=timezone.utc),
    )


def _stop_missing_discrepancy(
    tier: dict[str, object],
) -> ReconciliationDiscrepancy:
    """Build discrepancy for a placed stop tier missing on Bitget."""
    return ReconciliationDiscrepancy(
        asset=str(tier.get("trade_id", "")),
        discrepancy_type=DiscrepancyType.STOP_MISSING,
        oms_value="tier {} placed (order={})".format(
            tier.get("tier", "?"), tier.get("bitget_order_id", "?"),
        ),
        bitget_value="order not found in open plan orders",
        severity="medium",
        auto_action_taken=None,
        requires_human=True,
        detected_at=datetime.now(tz=timezone.utc),
    )


def _compute_divergence(
    oms_notional: Decimal,
    bitget_notional: Decimal,
    price_step: Decimal,
) -> Decimal:
    """Quantize notionals by price step, then compute % divergence."""
    if price_step > 0:
        oms_q = _quantize_notional(oms_notional, price_step)
        bitget_q = _quantize_notional(bitget_notional, price_step)
    else:
        oms_q = oms_notional
        bitget_q = bitget_notional
    if oms_q <= 0:
        return Decimal("0")
    return abs(oms_q - bitget_q) / oms_q


def _quantize_notional(value: Decimal, step: Decimal) -> Decimal:
    """Round notional value DOWN to nearest step — Decimal only, no float."""
    if step <= 0:
        return value
    return (value / step).to_integral_value(rounding=ROUND_DOWN) * step

