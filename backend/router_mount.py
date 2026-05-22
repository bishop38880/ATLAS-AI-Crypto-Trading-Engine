"""Group FastAPI router registration for ``backend.main``.

Keeps the ASGI module readable and documents logical HTTP surfaces.
"""

from __future__ import annotations

from fastapi import FastAPI


def mount_atlas_legacy_and_domain_routes(app: FastAPI) -> None:
    """Paper trade, rotation, executive, MTF, websocket helpers, Nansen stats."""
    from atlas.routes.paper_trade import router as paper_trade_router
    from atlas.routes.rotation import router as rotation_router
    from atlas.routes.executive import router as executive_router
    from atlas.routes.mtf_confluence import router as mtf_confluence_router
    from atlas.signals.outcome_route import router as outcome_router
    from backend.routes.nansen_stats import router as nansen_router
    from backend.routes.websocket import router as ws_router

    app.include_router(outcome_router)
    app.include_router(paper_trade_router)
    app.include_router(rotation_router)
    app.include_router(executive_router)
    app.include_router(mtf_confluence_router)
    app.include_router(ws_router)
    app.include_router(nansen_router)


def mount_atlas_api_surface(app: FastAPI) -> None:
    """REST routers under ``atlas.api.routes`` plus telemetry and provider compat."""
    from atlas.api.routes.agents import router as agents_router
    from atlas.api.routes.backtest import router as backtest_router
    from atlas.api.routes.dashboard import router as dashboard_router
    from atlas.api.routes.decisions import router as decisions_router
    from atlas.api.routes.funding import router as funding_router
    from atlas.api.routes.gnn import router as gnn_router
    from atlas.api.routes.health import router as health_router
    from atlas.api.routes.log import router as log_router
    from atlas.api.routes.marl import router as marl_router
    from atlas.api.routes.market import router as market_router
    from atlas.api.routes.memory import router as memory_router
    from atlas.api.routes.monitoring import router as monitoring_router
    from atlas.api.routes.monitoring_market import router as monitoring_market_router
    from atlas.api.routes.omnibox import router as omnibox_router
    from atlas.api.routes.risk_governor import router as risk_governor_router
    from atlas.api.routes.coinbase_premium import router as coinbase_premium_router
    from atlas.api.routes.providers import router as providers_router
    from atlas.api.routes.helius_webhooks import router as helius_webhook_router
    from atlas.api.routes.helius_providers import router as helius_providers_router
    from atlas.api.routes.signals import router as new_signals_router
    from atlas.api.routes.state import router as state_router
    from atlas.api.routes.system import router as system_router
    from atlas.routers.telemetry import router as telemetry_router
    from backend.routes.prometheus_compat import router as prometheus_compat_router
    from backend.routes.trade_pipeline import router as trade_pipeline_router

    app.include_router(new_signals_router)
    app.include_router(trade_pipeline_router)
    app.include_router(market_router)
    app.include_router(state_router)
    app.include_router(memory_router)
    app.include_router(dashboard_router)
    app.include_router(backtest_router)
    app.include_router(funding_router)
    app.include_router(decisions_router)
    app.include_router(providers_router)
    app.include_router(helius_providers_router)
    app.include_router(coinbase_premium_router)
    app.include_router(gnn_router)
    app.include_router(omnibox_router)
    app.include_router(monitoring_router)
    app.include_router(monitoring_market_router)
    app.include_router(risk_governor_router)
    app.include_router(marl_router)
    app.include_router(agents_router)
    app.include_router(telemetry_router)
    app.include_router(system_router)
    app.include_router(log_router)
    app.include_router(health_router)
    app.include_router(helius_webhook_router)
    app.include_router(prometheus_compat_router)


def mount_prometheus_portfolio_bundle(app: FastAPI) -> None:
    """NEXT portfolio REST + websocket mounted on the ATLAS origin."""
    from prometheus.api.main import portfolio_ws as prometheus_portfolio_ws
    from prometheus.api.routes.portfolio import router as prom_portfolio_router

    app.include_router(prom_portfolio_router, prefix="/api/portfolio", tags=["prometheus"])
    app.add_api_websocket_route("/dashboard/ws/portfolio", prometheus_portfolio_ws)
