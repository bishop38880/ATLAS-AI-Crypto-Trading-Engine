"""Pre-execution risk guards — capital ceiling, funding cost, margin mode.

Every order candidate must pass all three gates before reaching
the ``BitgetExecutionClient``.  Guards are pure-function style
(no side effects, no I/O) and return ``RiskCheckResult`` with
the verdict.

Architecture constraints:
    - ``Decimal`` for ALL financial math.
    - ``loguru`` POSITIONAL format only.
    - Functions ≤ 40 lines.
"""

from __future__ import annotations

from decimal import Decimal

from loguru import logger

from prometheus.risk.models import (
    RiskCheckInput,
    RiskCheckResult,
    RiskDecision,
)


# ── Thresholds ────────────────────────────────────────────────────────

CAPITAL_RISK_CEILING_PCT: Decimal = Decimal("0.01")    # 1 % of portfolio
HIGH_FUNDING_THRESHOLD: Decimal = Decimal("0.001")      # 0.1 % per 8h
LEVERAGE_DOWNGRADE_FACTOR: Decimal = Decimal("2")        # halve leverage


async def check_capital_risk(
    inputs: RiskCheckInput,
) -> RiskCheckResult:
    """Reject if position risk exceeds 1% of portfolio capital.

    Risk = position_notional / portfolio_capital.
    """
    if inputs.portfolio_capital_usd <= 0:
        return _reject(
            "capital_risk",
            "Portfolio capital is zero or negative",
            inputs.leverage,
        )
    risk_pct = inputs.position_notional_usd / inputs.portfolio_capital_usd
    if risk_pct > CAPITAL_RISK_CEILING_PCT:
        logger.warning(
            "capital_risk_exceeded | risk_pct={} | ceiling={}",
            risk_pct, CAPITAL_RISK_CEILING_PCT,
        )
        return _reject(
            "capital_risk",
            "Position risk {:.4f} exceeds 1% ceiling".format(risk_pct),
            inputs.leverage,
        )
    return _approve("capital_risk", inputs.leverage)


async def check_funding_cost(
    inputs: RiskCheckInput,
) -> RiskCheckResult:
    """Downgrade leverage if funding rate is high.

    High funding rate = paying significant carry cost.  Halve
    leverage to reduce exposure.
    """
    abs_funding = abs(inputs.funding_rate_8h)
    if abs_funding > HIGH_FUNDING_THRESHOLD:
        new_leverage = max(
            Decimal("1"),
            inputs.leverage / LEVERAGE_DOWNGRADE_FACTOR,
        )
        logger.warning(
            "funding_cost_high | rate={} | leverage {} -> {}",
            inputs.funding_rate_8h, inputs.leverage, new_leverage,
        )
        return RiskCheckResult(
            decision=RiskDecision.DOWNGRADED,
            rule_name="funding_cost",
            reason="Funding rate {:.6f} exceeds threshold; "
                   "leverage downgraded {} -> {}".format(
                       inputs.funding_rate_8h,
                       inputs.leverage,
                       new_leverage,
                   ),
            original_leverage=inputs.leverage,
            adjusted_leverage=new_leverage,
        )
    return _approve("funding_cost", inputs.leverage)


async def check_margin_mode(
    inputs: RiskCheckInput,
) -> RiskCheckResult:
    """Reject cross-margin orders — isolated only."""
    if inputs.margin_mode == "crossed":
        logger.error(
            "cross_margin_rejected | symbol={}", inputs.symbol,
        )
        return _reject(
            "margin_mode",
            "Cross-margin is prohibited — isolated only",
            inputs.leverage,
        )
    return _approve("margin_mode", inputs.leverage)


async def run_all_guards(
    inputs: RiskCheckInput,
) -> list[RiskCheckResult]:
    """Run all pre-execution risk checks and return all results."""
    return [
        await check_margin_mode(inputs),
        await check_capital_risk(inputs),
        await check_funding_cost(inputs),
    ]


# ── Helpers ───────────────────────────────────────────────────────────


def _approve(rule: str, leverage: Decimal) -> RiskCheckResult:
    """Build an APPROVED result."""
    return RiskCheckResult(
        decision=RiskDecision.APPROVED,
        rule_name=rule,
        reason="Check passed",
        original_leverage=leverage,
        adjusted_leverage=leverage,
    )


def _reject(
    rule: str, reason: str, leverage: Decimal,
) -> RiskCheckResult:
    """Build a REJECTED result."""
    return RiskCheckResult(
        decision=RiskDecision.REJECTED,
        rule_name=rule,
        reason=reason,
        original_leverage=leverage,
        adjusted_leverage=leverage,
    )
