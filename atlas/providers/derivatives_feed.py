"""Derivatives feed coordinator — 3-tier fallback chain.

OKX MCP (primary) → Coinalyze V3 (secondary) → CCXT OKX (tertiary).
Agents call this class exclusively — never individual providers.

Never raises. Logs individual failures per field.
"""

import asyncio
import time
from decimal import Decimal
from typing import Any, Awaitable

from loguru import logger
from pydantic import BaseModel


class DerivativesBundle(BaseModel, frozen=True):
    """All derivatives data for a single asset, from any source tier."""

    asset: str
    funding_rate: Decimal | None = None
    funding_history: list[Decimal] | None = None
    oi_current: Decimal | None = None
    oi_history: list[Decimal] | None = None
    ls_long_ratio: Decimal | None = None
    ls_short_ratio: Decimal | None = None
    liq_long_usd: Decimal | None = None
    liq_short_usd: Decimal | None = None
    data_sources: dict[str, str] = {}
    fetched_at_ms: int = 0


class DerivativesFeed:
    """Coordinates the 3-tier fallback chain for derivatives data.

    OKX MCP (primary) → Coinalyze V3 (secondary) → CCXT (tertiary).
    """

    def __init__(
        self,
        okx_provider: Any | None = None,
        cg_provider: Any | None = None,
    ) -> None:
        """Initialise with provider instances.

        Args:
            okx_provider: OKXMCPProvider instance (or None).
            cg_provider: CoinalyzeV3Provider instance (or None).
        """
        self._okx = okx_provider
        self._cg = cg_provider

    async def fetch_funding_rate(
        self, asset: str,
    ) -> tuple[Decimal | None, str]:
        """Attempt OKX MCP → Coinalyze V3 → None.

        Returns:
            Tuple of (funding_rate, source_name).
        """
        if self._okx:
            try:
                result = await self._okx.fetch_funding_rate(asset)
                if result is not None:
                    return (result.funding_rate, "okx_mcp")
            except Exception as e:
                logger.warning(
                    "okx_mcp funding_rate failed | asset={} | err={}",
                    asset, e,
                )
        if self._cg:
            try:
                result = await self._cg.fetch_funding_current(asset)
                if result is not None:
                    return (result.funding_rate, "coinalyze_v3")
            except Exception as e:
                logger.warning(
                    "coinalyze_v3 funding_rate failed | asset={} | err={}",
                    asset, e,
                )
        return (None, "none")

    async def fetch_funding_history(
        self, asset: str, periods: int = 90,
    ) -> tuple[list[Decimal] | None, str]:
        """Attempt OKX MCP → Coinalyze V3 → None.

        Returns:
            Tuple of (list_of_rates, source_name).
        """
        rates = await self._try_okx_funding_history(asset, periods)
        if rates is not None:
            return rates
        rates = await self._try_cg_funding_history(asset, periods)
        if rates is not None:
            return rates
        return (None, "none")

    async def _try_okx_funding_history(
        self, asset: str, periods: int,
    ) -> tuple[list[Decimal], str] | None:
        """Try OKX MCP for funding history."""
        if not self._okx:
            return None
        try:
            result = await self._okx.fetch_funding_history(asset, periods)
            if result and len(result.bars) >= 30:
                rates = [b.funding_rate for b in result.bars]
                return (rates, "okx_mcp")
            if result and len(result.bars) < 30:
                logger.warning(
                    "okx_mcp insufficient history | asset={} | bars={}",
                    asset, len(result.bars),
                )
        except Exception as e:
            logger.warning(
                "okx_mcp funding_history failed | asset={} | err={}",
                asset, e,
            )
        return None

    async def _try_cg_funding_history(
        self, asset: str, periods: int,
    ) -> tuple[list[Decimal], str] | None:
        """Try Coinalyze V3 for funding history."""
        if not self._cg:
            return None
        try:
            result = await self._cg.fetch_funding_history(asset, periods)
            if result and len(result.bars) >= 30:
                rates = [b.funding_rate for b in result.bars]
                return (rates, "coinalyze_v3")
            if result and len(result.bars) < 30:
                logger.warning(
                    "cg_v3 insufficient history | asset={} | bars={}",
                    asset, len(result.bars),
                )
        except Exception as e:
            logger.warning(
                "cg_v3 funding_history failed | asset={} | err={}",
                asset, e,
            )
        return None

    async def fetch_open_interest(
        self, asset: str,
    ) -> tuple[Decimal | None, str]:
        """Attempt OKX MCP → Coinalyze V3 OI → None."""
        if self._okx:
            try:
                result = await self._okx.fetch_open_interest(asset)
                if result is not None:
                    return (result.oi_ccy, "okx_mcp")
            except Exception as e:
                logger.warning(
                    "okx_mcp oi failed | asset={} | err={}", asset, e,
                )
        return (None, "none")

    async def fetch_oi_history(
        self, asset: str, bars: int = 336,
    ) -> tuple[list[Decimal] | None, str]:
        """Attempt OKX MCP → Coinalyze V3 → None."""
        if self._okx:
            try:
                result = await self._okx.fetch_oi_history(asset, bars)
                if result and len(result.bars) >= 100:
                    oi_vals = [b.oi_ccy for b in result.bars]
                    return (oi_vals, "okx_mcp")
            except Exception as e:
                logger.warning(
                    "okx_mcp oi_history failed | asset={} | err={}",
                    asset, e,
                )
        if self._cg:
            try:
                result = await self._cg.fetch_oi_history(asset, bars)
                if result and len(result.bars) >= 100:
                    oi_vals = [b.oi for b in result.bars]
                    return (oi_vals, "coinalyze_v3")
            except Exception as e:
                logger.warning(
                    "cg_v3 oi_history failed | asset={} | err={}",
                    asset, e,
                )
        return (None, "none")

    async def fetch_long_short_ratio(
        self, asset: str,
    ) -> tuple[Decimal | None, Decimal | None, str]:
        """Attempt OKX MCP → Coinalyze V3 → None.

        Returns:
            Tuple of (long_ratio, short_ratio, source_name).
        """
        if self._okx:
            try:
                result = await self._okx.fetch_long_short_ratio(asset)
                if result is not None:
                    return (
                        result.long_ratio, result.short_ratio, "okx_mcp",
                    )
            except Exception as e:
                logger.warning(
                    "okx_mcp ls_ratio failed | asset={} | err={}",
                    asset, e,
                )
        if self._cg:
            try:
                result = await self._cg.fetch_long_short_ratio(asset)
                if result is not None:
                    return (
                        result.long_ratio, result.short_ratio, "coinalyze_v3",
                    )
            except Exception as e:
                logger.warning(
                    "cg_v3 ls_ratio failed | asset={} | err={}",
                    asset, e,
                )
        return (None, None, "none")

    async def fetch_liquidations(
        self, asset: str,
    ) -> tuple[Decimal | None, Decimal | None, str]:
        """Attempt OKX MCP → Coinalyze V3 → None.

        Returns:
            Tuple of (long_liq_usd, short_liq_usd, source_name).
        """
        if self._okx:
            try:
                result = await self._okx.fetch_liquidations(asset)
                if result and result.orders:
                    return self._aggregate_okx_liqs(result)
            except Exception as e:
                logger.warning(
                    "okx_mcp liqs failed | asset={} | err={}", asset, e,
                )
        if self._cg:
            try:
                result = await self._cg.fetch_liquidation_history(asset)
                if result and result.bars:
                    bar = result.bars[0]
                    return (bar.long_liq, bar.short_liq, "coinalyze_v3")
            except Exception as e:
                logger.warning(
                    "cg_v3 liqs failed | asset={} | err={}", asset, e,
                )
        return (None, None, "none")

    def _aggregate_okx_liqs(
        self, snapshot: Any,
    ) -> tuple[Decimal, Decimal, str]:
        """Sum OKX liquidation orders by side."""
        long_total = Decimal("0")
        short_total = Decimal("0")
        for order in snapshot.orders:
            notional = order.size * order.bk_px
            if order.side == "sell":
                long_total += notional
            else:
                short_total += notional
        return (long_total, short_total, "okx_mcp")

    async def fetch_all(self, asset: str) -> DerivativesBundle:
        """Fetch all derivatives data in parallel.

        Each sub-task catches its own exceptions and returns None
        on failure, ensuring no single provider crash kills the
        entire derivatives pipeline.

        Returns:
            DerivativesBundle with all available fields.
        """
        async def _safe(label: str, coro: Awaitable[Any]) -> Any:
            try:
                return await coro
            except Exception as exc:
                logger.error(
                    "derivatives_feed task error | field={} | err={}",
                    label, exc,
                )
                return None

        fr, fh, oi, oih, ls, liq = await asyncio.gather(
            _safe("funding_rate", self.fetch_funding_rate(asset)),
            _safe("funding_history", self.fetch_funding_history(asset)),
            _safe("oi_current", self.fetch_open_interest(asset)),
            _safe("oi_history", self.fetch_oi_history(asset)),
            _safe("ls_ratio", self.fetch_long_short_ratio(asset)),
            _safe("liquidations", self.fetch_liquidations(asset)),
        )

        return self._build_bundle_from_results(
            asset, fr, fh, oi, oih, ls, liq,
        )

    def _build_bundle_from_results(
        self,
        asset: str,
        fr: Any,
        fh: Any,
        oi: Any,
        oih: Any,
        ls: Any,
        liq: Any,
    ) -> DerivativesBundle:
        """Assemble DerivativesBundle from parallel fetch results."""
        sources: dict[str, str] = {}
        if fr and fr[0] is not None:
            sources["funding_rate"] = fr[1]
        if fh and fh[0] is not None:
            sources["funding_history"] = fh[1]
        if oi and oi[0] is not None:
            sources["oi_current"] = oi[1]
        if oih and oih[0] is not None:
            sources["oi_history"] = oih[1]
        if ls and ls[0] is not None:
            sources["ls_ratio"] = ls[2]
        if liq and liq[0] is not None:
            sources["liquidations"] = liq[2]
        return DerivativesBundle(
            asset=asset,
            funding_rate=fr[0] if fr else None,
            funding_history=fh[0] if fh else None,
            oi_current=oi[0] if oi else None,
            oi_history=oih[0] if oih else None,
            ls_long_ratio=ls[0] if ls else None,
            ls_short_ratio=ls[1] if ls else None,
            liq_long_usd=liq[0] if liq else None,
            liq_short_usd=liq[1] if liq else None,
            data_sources=sources,
            fetched_at_ms=int(time.time() * 1000),
        )

