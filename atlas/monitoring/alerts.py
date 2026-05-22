"""Threshold-based alerts for hourly monitoring cycles."""

from __future__ import annotations

from atlas.monitoring.models import AssetHourlyAnalytics, NormalizedHourlyQuote
from atlas.shared.config import PolarisSettings


def evaluate_hourly_alerts(
    settings: PolarisSettings,
    quotes: list[NormalizedHourlyQuote],
    analytics: list[AssetHourlyAnalytics],
) -> list[tuple[str, str, str]]:
    """
    Return alert tuples ``(level, source, message)`` when thresholds are breached.

    Levels: WARNING | CRITICAL
    """
    alerts: list[tuple[str, str, str]] = []
    analytics_by_asset = {row.asset_base: row for row in analytics}

    for quote in quotes:
        if quote.quality == "degraded":
            alerts.append(
                (
                    "WARNING",
                    "hourly_monitor",
                    f"{quote.asset_base} quote degraded: {','.join(quote.degraded_reasons)}",
                )
            )
        row = analytics_by_asset.get(quote.asset_base)
        if row is None or row.hourly_return_pct is None:
            continue
        if abs(row.hourly_return_pct) >= settings.hourly_monitor_alert_return_pct_critical:
            alerts.append(
                (
                    "CRITICAL",
                    "hourly_monitor",
                    f"{quote.asset_base} hourly return {row.hourly_return_pct:.2f}%",
                )
            )
        elif abs(row.hourly_return_pct) >= settings.hourly_monitor_alert_return_pct_warning:
            alerts.append(
                (
                    "WARNING",
                    "hourly_monitor",
                    f"{quote.asset_base} hourly return {row.hourly_return_pct:.2f}%",
                )
            )
        if row.volume_z_score is not None and row.volume_z_score >= settings.hourly_monitor_alert_volume_z_warning:
            alerts.append(
                (
                    "WARNING",
                    "hourly_monitor",
                    f"{quote.asset_base} volume z-score {row.volume_z_score:.2f}",
                )
            )

    return alerts
