from pydantic import AliasChoices, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

class PrometheusSettings(BaseSettings):
    """Configuration for the PROMETHEUS execution layer.

    This class owns all exchange-sensitive credentials. It is NEVER
    imported by ATLAS.
    """
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
    )

    # --- Redis & Postgres (duplicated from atlas if needed, or shared via .env) ---
    redis_url: str = "redis://localhost:6379/0"
    postgres_url: str = "postgresql://postgres:postgres@localhost:5432/atlas"

    # Bitget execution credentials — PROMETHEUS only.
    # Use SecretStr so credentials never accidentally serialize into
    # logs or pydantic .model_dump() output.
    bitget_api_key: SecretStr = SecretStr("")
    bitget_secret_key: SecretStr = Field(
        default=SecretStr(""),
        validation_alias=AliasChoices("BITGET_SECRET_KEY", "BITGET_API_SECRET"),
    )
    bitget_api_passphrase: SecretStr = Field(
        default=SecretStr(""),
        validation_alias=AliasChoices("BITGET_API_PASSPHRASE", "BITGET_PASSPHRASE"),
    )

    # Public Bitget base URL — also acceptable in atlas/settings.py
    # since it carries no credential value. Keep the canonical copy here.
    bitget_base_url: str = "https://api.bitget.com"

    # Paper-trade subsystem. Keep every execution path disabled by default.
    paper_trade_executor_enabled: bool = False
    paper_trade_dry_run: bool = True
    bitget_demo_write_enabled: bool = False
    paper_trade_enabled: bool = False               # Legacy compatibility flag.
    paper_trade_redis_channel: str = "polaris:paper_trade"
    paper_trade_symbol_ttl_seconds: int = 3600      # 1h cache
    paper_trade_default_leverage: int = Field(default=5, ge=1, le=125)

    # Demo-trading uses Bitget's SUSDT-FUTURES product family
    bitget_demo_product_type: str = "SUSDT-FUTURES"
    bitget_demo_margin_coin: str = "SUSDT"

    # HTTP timeout for the public symbol-fetcher
    bitget_symbol_fetch_timeout_s: float = 10.0

    # JWT Authentication
    JWT_SECRET: str = "unsafe_default_change_in_production"
    JWT_ALGORITHM: str = "HS256"

    # When True, /dashboard/ws/portfolio accepts ?user_id= for local dev (no JWT).
    # Keep False in any shared/production environment.
    ws_allow_query_user_id: bool = False

    # Same env as ATLAS: opt-in Swagger/OpenAPI on the standalone PROMETHEUS ASGI app.
    expose_openapi_docs: bool = Field(
        default=False,
        validation_alias=AliasChoices("ATLAS_EXPOSE_OPENAPI", "PROMETHEUS_EXPOSE_OPENAPI"),
    )

prometheus_settings = PrometheusSettings()
