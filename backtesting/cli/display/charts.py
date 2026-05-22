"""ASCII chart rendering for CLI results."""

from __future__ import annotations

from decimal import Decimal

from backtesting.analytics.metrics import MonthlyReturns

_BLOCK_CHARS = " ▁▂▃▄▅▆▇█"
_POSITIVE_STYLE = "green"
_NEGATIVE_STYLE = "red"


def render_equity_curve(
    equity_curve: list[dict[str, object]],
    width: int = 80,
    height: int = 15,
) -> str:
    """Render an ASCII equity curve with drawdown shading."""
    if not equity_curve:
        return "No equity data available."
    values = [_parse_decimal(row.get("equity_usd", "0")) for row in equity_curve]
    min_value = min(values)
    max_value = max(values)
    span = max_value - min_value
    if span <= Decimal("0"):
        span = Decimal("1")
    lines: list[str] = []
    grid_height = max(height - 2, 4)
    for row_index in range(grid_height):
        threshold = max_value - (span * Decimal(row_index) / Decimal(grid_height - 1))
        label = f"${threshold:,.0f}".rjust(10)
        cells: list[str] = []
        for column in range(min(width - 12, len(values))):
            sample_index = int(column * (len(values) - 1) / max(width - 13, 1))
            sample = values[sample_index]
            cells.append("╭" if sample >= threshold else " ")
        lines.append(f"{label} ┤{''.join(cells)}")
    lines.append(" " * 11 + "├" + "─" * min(width - 12, len(values)))
    marker_row = " " * 11 + " " + "▲ " * min(20, len(values) // 3)
    lines.append(marker_row.strip())
    return "\n".join(lines)


def render_monthly_heatmap(monthly_returns: list[MonthlyReturns]) -> str:
    """Render a colour-coded monthly returns grid."""
    if not monthly_returns:
        return "No monthly return data."
    years = sorted({row.year for row in monthly_returns})
    header = "         " + "".join(f"{month:>7}" for month in range(1, 13))
    lines = ["Monthly Returns (%)", header]
    lookup = {(row.year, row.month): row for row in monthly_returns}
    for year in years:
        cells: list[str] = []
        for month in range(1, 13):
            row = lookup.get((year, month))
            if row is None:
                cells.append(f"{'—':>7}")
                continue
            cells.append(_format_month_cell(row.pnl_pct))
        lines.append(f"{year}   " + "".join(cells))
    return "\n".join(lines)


def _format_month_cell(pnl_pct: Decimal) -> str:
    text = f"{pnl_pct:+.1f}%"
    if pnl_pct > Decimal("0"):
        return f"[{_POSITIVE_STYLE}]{text:>7}[/]"
    if pnl_pct < Decimal("0"):
        return f"[{_NEGATIVE_STYLE}]{text:>7}[/]"
    return f"{text:>7}"


def render_score_bar(points: int, maximum: int, width: int = 16) -> str:
    """Render a mini progress bar for tutorial score breakdown."""
    if maximum <= 0:
        return ""
    filled = int(width * points / maximum)
    return "█" * filled + "░" * (width - filled)


def _parse_decimal(value: object) -> Decimal:
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))
