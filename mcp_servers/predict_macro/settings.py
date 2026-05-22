"""
Typed configuration for the Fiat Gravity MCP process.

MacroCrossMarketAgent - Fiat Gravity Engine runs as a standalone stdio MCP;
``pydantic-settings`` loads ``FRED_API_KEY`` and ``ETH_RPC_URL`` from the
package-local ``.env`` (and process environment).
"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class PredictMacroSettings(BaseSettings):
    """Environment-backed settings for FRED and Ethereum RPC."""

    model_config = SettingsConfigDict(
        env_file=str(Path(__file__).resolve().parent / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    fred_api_key: str = Field(
        description="FRED API key for api.stlouisfed.org observations.",
    )
    eth_rpc_url: str = Field(
        description="Ethereum mainnet HTTPS RPC URL (Infura, Alchemy, etc.).",
    )
