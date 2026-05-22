"""Synthetic USD paper-validation replay anchored to Redis spot snapshots.

Historical ``signals`` rows lack persisted marks — per-asset series use score
delta + elapsed time between observations to retrofit a deterministic path tied
to the latest spot anchor. Figures are illustrative for comparative validation.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from statistics import mean, pstdev
from typing import Literal, Mapping, MutableMapping, Sequence

ScoreTier = Literal["under_150", "150_179", "180_plus"]
TradeSide = Literal["long", "short", "flat"]

TAKER_BPS = Decimal("0.0006")
FIXED_NOTIONAL_USD = Decimal("2500")
INITIAL_CASH = Decimal("100000")


def classify_score_tier(total_score: float) -> ScoreTier:
    slab = math.floor(float(total_score) + 1e-9)
    if slab >= 180:
        return "180_plus"
    if slab >= 150:
        return "150_179"
    return "under_150"


def normalize_trade_side(decision: str) -> TradeSide:
    compact = "".join(decision.strip().lower().split())
    if compact in ("long", "strongbuy", "buy"):
        return "long"
    if compact in ("short", "strongsell", "sell"):
        return "short"
    return "flat"


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def compute_forward_log_return(
    score_prev: float,
    score_next: float,
    delta_seconds: float,
) -> float:
    if delta_seconds <= 0:
        delta_seconds = 60.0
    dt_days = max(delta_seconds / 86400.0, timedelta(minutes=1).total_seconds() / 86400.0)
    delta_score = score_next - score_prev
    body = math.tanh(delta_score / 55.0) * min(0.045, 0.65 * math.sqrt(dt_days))
    return _clamp(body, -0.06, 0.06)


def _exp_decimal(rate: float) -> Decimal:
    return Decimal(str(math.exp(rate)))


def build_backward_anchored_prices(
    chronological_ids: Sequence[int],
    chronological_scores: Sequence[float],
    chronological_ts: Sequence[datetime],
    anchor_px: Decimal,
) -> dict[int, Decimal]:
    n = len(chronological_ids)
    if n == 0:
        return {}
    if n == 1:
        return {int(chronological_ids[0]): anchor_px}

    log_returns: list[float] = []
    step = 0
    while step < n - 1:
        log_returns.append(
            compute_forward_log_return(
                chronological_scores[step],
                chronological_scores[step + 1],
                (chronological_ts[step + 1] - chronological_ts[step]).total_seconds(),
            ),
        )
        step += 1

    ladder: MutableMapping[int, Decimal] = {}
    cursor_px = anchor_px
    ladder[n - 1] = anchor_px
    walker = n - 2
    while walker >= 0:
        next_px = cursor_px * _exp_decimal(-log_returns[walker])
        ladder[walker] = next_px
        cursor_px = next_px
        walker -= 1

    return {int(chronological_ids[index]): ladder[index] for index in range(n)}


def _ensure_utc(ts: datetime) -> datetime:
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)


@dataclass(frozen=True)
class SimHistoryRow:
    row_id: int
    asset: str
    ts: datetime
    total_score: float
    decision: str
    passes_gate: bool


@dataclass(frozen=True)
class TierAccumulator:
    wins: int
    trades: int

    def record(self, win: bool) -> TierAccumulator:
        return TierAccumulator(
            wins=self.wins + (1 if win else 0),
            trades=self.trades + 1,
        )


def _empty_tiers() -> dict[ScoreTier, TierAccumulator]:
    tiers: Sequence[ScoreTier] = ("under_150", "150_179", "180_plus")
    return {name: TierAccumulator(0, 0) for name in tiers}


def _iso_z(ts: datetime) -> str:
    utc_ts = ts.replace(tzinfo=timezone.utc) if ts.tzinfo is None else ts.astimezone(timezone.utc)
    return utc_ts.isoformat().replace("+00:00", "Z")


def _annualized_weekly_sharpe(week_end_equity: Mapping[tuple[int, int], Decimal]) -> float:
    keys_sorted = sorted(week_end_equity.keys())
    if len(keys_sorted) < 3:
        return 0.0

    returns: list[float] = []
    prev_equity: Decimal | None = None
    for bucket in keys_sorted:
        equity_now = week_end_equity[bucket]
        if prev_equity is None:
            prev_equity = equity_now
            continue
        if prev_equity <= 0:
            prev_equity = equity_now
            continue
        returns.append(float((equity_now - prev_equity) / prev_equity))
        prev_equity = equity_now

    if len(returns) < 2:
        return 0.0

    avg = mean(returns)
    vol = pstdev(returns)
    if vol <= 1e-9:
        return 0.0
    return math.sqrt(52.0) * avg / vol


def _anchor_lookup(mapping: Mapping[str, Decimal], asset: str) -> Decimal | None:
    direct = mapping.get(asset)
    if direct is not None and direct > 0:
        return direct
    upper = mapping.get(asset.upper())
    if upper is not None and upper > 0:
        return upper
    return None


def simulate_parallel_portfolio_usd(
    rows: Sequence[SimHistoryRow],
    spot_anchors_usd: Mapping[str, Decimal],
    *,
    fixed_notional: Decimal | None = None,
    fee_rate: Decimal = TAKER_BPS,
    initial_cash: Decimal = INITIAL_CASH,
    fallback_spot_usd: Decimal = Decimal("1"),
    roll_positions_each_signal: bool = True,
) -> dict[str, object]:
    notional_each = FIXED_NOTIONAL_USD if fixed_notional is None else fixed_notional

    chronological = sorted(rows, key=lambda item: (_ensure_utc(item.ts), item.row_id))

    timelines: defaultdict[str, list[SimHistoryRow]] = defaultdict(list)
    for item in chronological:
        timelines[item.asset].append(item)

    price_by_row: dict[int, Decimal] = {}
    for ticker, timeline in timelines.items():
        anchor = _anchor_lookup(spot_anchors_usd, ticker)
        usable_anchor = fallback_spot_usd if anchor is None or anchor <= 0 else anchor
        identifiers = [r.row_id for r in timeline]
        scores_series = [r.total_score for r in timeline]
        clock_series = [_ensure_utc(r.ts) for r in timeline]
        price_by_row.update(
            build_backward_anchored_prices(identifiers, scores_series, clock_series, usable_anchor),
        )

    tier_board = _empty_tiers()

    cash = initial_cash
    open_side: Literal["long", "short"] | None = None
    quantity = Decimal("0")
    avg_entry_px = Decimal("0")
    entry_tier: ScoreTier | None = None

    equity_curve: list[dict[str, object]] = []
    drawdown_curve: list[dict[str, object]] = []
    weekly_last_equity: dict[tuple[int, int], Decimal] = {}

    equity_peak = initial_cash

    def unrealized(px_mark: Decimal) -> Decimal:
        if quantity <= 0 or open_side is None:
            return Decimal("0")
        if open_side == "long":
            return quantity * (px_mark - avg_entry_px)
        return quantity * (avg_entry_px - px_mark)

    def push_snapshot(moment: datetime, px_mark: Decimal) -> None:
        nonlocal equity_peak
        equity_now = cash + unrealized(px_mark)
        equity_peak = max(equity_peak, equity_now)
        ts_label = _iso_z(moment)

        equity_curve.append({"ts": ts_label, "equity_usd": str(equity_now)})
        dd_pct = float((equity_now / equity_peak - Decimal("1")) * Decimal("100")) if equity_peak > 0 else 0.0
        drawdown_curve.append({"ts": ts_label, "drawdown_pct": dd_pct})
        iso_meta = moment.isocalendar()
        weekly_last_equity[(int(iso_meta[0]), int(iso_meta[1]))] = equity_now

    def close_position(px_exit: Decimal) -> None:
        nonlocal cash, quantity, avg_entry_px, open_side, entry_tier, tier_board
        if open_side is None or quantity <= 0 or entry_tier is None:
            return

        if open_side == "long":
            entry_outlay = quantity * avg_entry_px * (Decimal("1") + fee_rate)
            exit_proceeds = quantity * px_exit * (Decimal("1") - fee_rate)
            cash += exit_proceeds
            net_change = exit_proceeds - entry_outlay
        else:
            entry_credit = quantity * avg_entry_px * (Decimal("1") - fee_rate)
            buyback_cost = quantity * px_exit * (Decimal("1") + fee_rate)
            cash -= buyback_cost
            net_change = entry_credit - buyback_cost

        prior = tier_board[entry_tier]
        tier_board[entry_tier] = prior.record(net_change > Decimal("0"))

        open_side = None
        quantity = Decimal("0")
        avg_entry_px = Decimal("0")
        entry_tier = None

    def enter_position(direction: Literal["long", "short"], px: Decimal, bucket: ScoreTier) -> None:
        nonlocal cash, quantity, avg_entry_px, open_side, entry_tier
        if px <= 0 or notional_each <= 0:
            return
        qty_local = notional_each / px
        if direction == "long":
            spend = qty_local * px * (Decimal("1") + fee_rate)
            if cash < spend:
                return
            cash -= spend
            open_side = "long"
        else:
            credit = qty_local * px * (Decimal("1") - fee_rate)
            cash += credit
            open_side = "short"

        quantity = qty_local
        avg_entry_px = px
        entry_tier = bucket

    for row_item in chronological:
        px_here = price_by_row.get(row_item.row_id, fallback_spot_usd)

        gate_ok = row_item.passes_gate
        directional = normalize_trade_side(row_item.decision)
        target: TradeSide = directional if gate_ok else "flat"

        if target == "flat":
            if open_side is not None:
                close_position(px_here)
        elif roll_positions_each_signal:
            if open_side is not None:
                close_position(px_here)
            enter_position(target, px_here, classify_score_tier(row_item.total_score))
        else:
            if open_side is not None:
                close_needed = target != open_side
                if close_needed:
                    close_position(px_here)

            if target in ("long", "short"):
                if open_side is None:
                    enter_position(target, px_here, classify_score_tier(row_item.total_score))
                elif target != open_side:
                    enter_position(target, px_here, classify_score_tier(row_item.total_score))

        push_snapshot(_ensure_utc(row_item.ts), px_here)

    if open_side is not None:
        closing_row = chronological[-1]
        close_position(price_by_row.get(closing_row.row_id, fallback_spot_usd))

    tier_payload = {
        key: {
            "wins": value.wins,
            "trades": value.trades,
            "win_rate": (value.wins / value.trades if value.trades else 0.0),
        }
        for key, value in tier_board.items()
    }

    sharpe_weekly = _annualized_weekly_sharpe(weekly_last_equity)

    ending_equity = Decimal(equity_curve[-1]["equity_usd"]) if equity_curve else initial_cash

    return {
        "initial_usd": str(initial_cash),
        "ending_usd": str(ending_equity),
        "equity_curve": equity_curve,
        "drawdown_curve": drawdown_curve,
        "weekly_sharpe_annualized": sharpe_weekly,
        "tier_win_rates": tier_payload,
        "row_count_used": len(chronological),
        "pricing_model": "score_delta_back_anchor",
    }
