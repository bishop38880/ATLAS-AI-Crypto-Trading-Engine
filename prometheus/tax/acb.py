"""CRA Adjusted Cost Base (ACB) calculator for crypto derivatives.

Implements the weighted-average ACB method required by the Canada
Revenue Agency for crypto-asset tax reporting.

Key features:
    - Weighted average ACB after multiple acquisitions
    - Disposition gain/loss calculation (proceeds - ACB)
    - USD → CAD conversion at trade-time exchange rates
    - T2125 summary aggregation for self-employment reporting
    - 50% capital gains inclusion rate

Architecture constraints:
    - ``Decimal`` for ALL financial math.
    - ``loguru`` POSITIONAL format only.
    - Functions ≤ 40 lines.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

from loguru import logger

from prometheus.tax.models import AcbLot, Disposition, T2125Summary


# Canada's capital gains inclusion rate
_INCLUSION_RATE: Decimal = Decimal("0.50")


class AcbCalculator:
    """Weighted-average ACB tracker for a single crypto asset.

    CRA requires the weighted-average method for identical
    properties — each acquisition adjusts the per-unit ACB,
    each disposition uses the current per-unit ACB.
    """

    def __init__(self, asset: str) -> None:
        self._asset = asset
        self._total_quantity: Decimal = Decimal("0")
        self._total_cost_cad: Decimal = Decimal("0")
        self._lots: list[AcbLot] = []
        self._dispositions: list[Disposition] = []

    @property
    def acb_per_unit(self) -> Decimal:
        """Current weighted-average ACB per unit in CAD."""
        if self._total_quantity <= 0:
            return Decimal("0")
        return self._total_cost_cad / self._total_quantity

    @property
    def total_quantity(self) -> Decimal:
        """Total quantity currently held."""
        return self._total_quantity

    def add_acquisition(
        self,
        quantity: Decimal,
        cost_usd: Decimal,
        exchange_rate: Decimal,
        acquired_at: datetime | None = None,
    ) -> AcbLot:
        """Record an acquisition and update the weighted-average ACB."""
        cost_cad = cost_usd * exchange_rate
        lot = AcbLot(
            lot_id=uuid.uuid4().hex[:12],
            asset=self._asset,
            quantity=quantity,
            cost_usd=cost_usd,
            cost_cad=cost_cad,
            exchange_rate=exchange_rate,
            acquired_at=acquired_at or datetime.now(tz=timezone.utc),
        )
        self._total_quantity += quantity
        self._total_cost_cad += cost_cad
        self._lots.append(lot)

        logger.debug(
            "acb_acquisition | asset={} | qty={} | cost_cad={} | "
            "new_acb_per_unit={}",
            self._asset, quantity, cost_cad, self.acb_per_unit,
        )
        return lot

    def record_disposition(
        self,
        quantity: Decimal,
        proceeds_usd: Decimal,
        exchange_rate: Decimal,
        disposed_at: datetime | None = None,
    ) -> Disposition:
        """Record a disposition and compute gain/loss."""
        if quantity > self._total_quantity:
            logger.warning(
                "disposition_exceeds_holdings | disposing={} | held={}",
                quantity, self._total_quantity,
            )
        acb_per = self.acb_per_unit
        total_acb = acb_per * quantity
        proceeds_cad = proceeds_usd * exchange_rate
        gain_loss = proceeds_cad - total_acb

        disposition = Disposition(
            disposition_id=uuid.uuid4().hex[:12],
            asset=self._asset,
            quantity=quantity,
            proceeds_usd=proceeds_usd,
            proceeds_cad=proceeds_cad,
            acb_per_unit_cad=acb_per,
            total_acb_cad=total_acb,
            gain_loss_cad=gain_loss,
            exchange_rate=exchange_rate,
            disposed_at=disposed_at or datetime.now(tz=timezone.utc),
        )
        # Reduce pool
        self._total_quantity -= quantity
        self._total_cost_cad -= total_acb
        self._dispositions.append(disposition)

        logger.debug(
            "acb_disposition | asset={} | qty={} | gain_loss_cad={}",
            self._asset, quantity, gain_loss,
        )
        return disposition

    def t2125_summary(self, tax_year: int) -> T2125Summary:
        """Aggregate dispositions into a T2125 summary for a tax year."""
        year_disps = [
            d for d in self._dispositions
            if d.disposed_at.year == tax_year
        ]
        gross_proceeds = sum(
            (d.proceeds_cad for d in year_disps), Decimal("0"),
        )
        total_acb = sum(
            (d.total_acb_cad for d in year_disps), Decimal("0"),
        )
        net_gain_loss = sum(
            (d.gain_loss_cad for d in year_disps), Decimal("0"),
        )
        taxable = _compute_taxable_income(net_gain_loss)

        return T2125Summary(
            tax_year=tax_year,
            total_dispositions=len(year_disps),
            gross_proceeds_cad=gross_proceeds,
            total_acb_cad=total_acb,
            net_gain_loss_cad=net_gain_loss,
            taxable_income_cad=taxable,
        )


def _compute_taxable_income(
    net_gain_loss: Decimal,
) -> Decimal:
    """Apply CRA 50% capital gains inclusion rate.

    Only gains are included at 50%.  Losses are deductible
    against gains but are NOT multiplied by the inclusion rate
    (they reduce the gain before inclusion).
    """
    if net_gain_loss <= 0:
        return Decimal("0")
    return net_gain_loss * _INCLUSION_RATE
