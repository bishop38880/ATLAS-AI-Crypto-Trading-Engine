"""Model Registry — PostgreSQL-backed ML model lifecycle tracking.

Manages the canary deployment state machine for ML models:
    shadow → canary_10pct → canary_50pct → live

All new models default to ``shadow`` mode. State transitions are
atomic via PostgreSQL UPDATE with WHERE guards.

Architecture note:
    Table is auto-created on first access. Performance metrics are
    stored as JSONB for flexible schema evolution.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

import asyncpg
import msgspec
from loguru import logger
from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_QUERY_TIMEOUT: float = 10.0


# ---------------------------------------------------------------------------
# Canary state enum
# ---------------------------------------------------------------------------


class CanaryState(str, Enum):
    """Model deployment state in the canary pipeline.

    Values:
        SHADOW: Running in parallel, outputs not used.
        CANARY_10PCT: 10% of analysis cycles use this model.
        CANARY_50PCT: 50% of analysis cycles use this model.
        LIVE: Model is the active production model.
        RETIRED: Previously live model, replaced by promotion.
    """

    SHADOW = "shadow"
    CANARY_10PCT = "canary_10pct"
    CANARY_50PCT = "canary_50pct"
    LIVE = "live"
    RETIRED = "retired"


# ---------------------------------------------------------------------------
# In-memory representation
# ---------------------------------------------------------------------------


class ModelRegistryEntry(BaseModel):
    """Pydantic model for a registered ML model version.

    Attributes:
        model_name: Logical model identifier (e.g. ``meta_learner``).
        version: Semver or integer version string.
        canary_state: Current deployment state.
        task_type: Either ``classification`` or ``regression``.
        shadow_cycles_remaining: Cycles left in shadow evaluation.
        canary_start_time: When the model entered canary state.
        performance_metrics: Rolling performance data (JSONB).
        created_at: Registration timestamp.
        updated_at: Last state change timestamp.
    """

    model_config = ConfigDict(frozen=True)

    model_name: str
    version: str
    canary_state: CanaryState = CanaryState.SHADOW
    task_type: str = "classification"
    shadow_cycles_remaining: int = 500
    canary_start_time: datetime | None = None
    performance_metrics: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
    )


# ---------------------------------------------------------------------------
# Table DDL
# ---------------------------------------------------------------------------

_CREATE_TABLE_SQL: str = """
CREATE TABLE IF NOT EXISTS ml_model_registry (
    model_name          TEXT NOT NULL,
    version             TEXT NOT NULL,
    canary_state        TEXT NOT NULL DEFAULT 'shadow',
    task_type           TEXT NOT NULL DEFAULT 'classification',
    shadow_cycles_remaining INTEGER NOT NULL DEFAULT 500,
    canary_start_time   TIMESTAMPTZ,
    performance_metrics JSONB NOT NULL DEFAULT '{}',
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (model_name, version)
);
"""

_INSERT_MODEL_SQL: str = """
INSERT INTO ml_model_registry
    (model_name, version, canary_state, task_type,
     shadow_cycles_remaining)
VALUES ($1, $2, $3, $4, $5)
ON CONFLICT (model_name, version) DO NOTHING
"""


# ---------------------------------------------------------------------------
# Registry class
# ---------------------------------------------------------------------------


class ModelRegistry:
    """PostgreSQL-backed registry for ML model canary state.

    Attributes:
        _pool: Injected asyncpg connection pool.
        _table_ensured: Whether the table has been created.
    """

    def __init__(self, asyncpg_pool: asyncpg.Pool) -> None:
        """Initialize with a PostgreSQL connection pool.

        Args:
            asyncpg_pool: Injected asyncpg.Pool instance.
        """
        self._pool = asyncpg_pool
        self._table_ensured = False

    async def _ensure_table(self, conn: asyncpg.Connection) -> None:
        """Create the registry table if it does not exist."""
        if self._table_ensured:
            return
        await conn.execute(_CREATE_TABLE_SQL, timeout=_QUERY_TIMEOUT)
        self._table_ensured = True

    async def register_model(
        self,
        model_name: str,
        version: str,
        task_type: str = "classification",
        shadow_cycles: int = 500,
    ) -> ModelRegistryEntry:
        """Register a new model version in shadow state.

        Args:
            model_name: Logical model name.
            version: Model version string.
            task_type: ``classification`` or ``regression``.
            shadow_cycles: Number of shadow cycles required.

        Returns:
            The newly created registry entry.
        """
        await self._insert_model_row(
            model_name, version, task_type, shadow_cycles,
        )
        logger.info(
            "model_registered | name={} | version={} | task={}",
            model_name, version, task_type,
        )
        return _build_new_entry(
            model_name, version, task_type, shadow_cycles,
        )

    async def _insert_model_row(
        self,
        model_name: str,
        version: str,
        task_type: str,
        shadow_cycles: int,
    ) -> None:
        """Execute the INSERT for a new model row."""
        async with self._pool.acquire() as conn:
            await self._ensure_table(conn)
            await conn.execute(
                _INSERT_MODEL_SQL,
                model_name, version,
                CanaryState.SHADOW.value,
                task_type, shadow_cycles,
                timeout=_QUERY_TIMEOUT,
            )

    async def get_model(
        self, model_name: str, version: str,
    ) -> ModelRegistryEntry | None:
        """Fetch a single model entry by name and version."""
        async with self._pool.acquire() as conn:
            await self._ensure_table(conn)
            row = await conn.fetchrow(
                """
                SELECT * FROM ml_model_registry
                WHERE model_name = $1 AND version = $2
                """,
                model_name, version,
                timeout=_QUERY_TIMEOUT,
            )

        if row is None:
            return None
        return _row_to_entry(row)

    async def get_live_model(
        self, model_name: str,
    ) -> ModelRegistryEntry | None:
        """Fetch the currently live version for a model name."""
        async with self._pool.acquire() as conn:
            await self._ensure_table(conn)
            row = await conn.fetchrow(
                """
                SELECT * FROM ml_model_registry
                WHERE model_name = $1 AND canary_state = $2
                ORDER BY updated_at DESC LIMIT 1
                """,
                model_name, CanaryState.LIVE.value,
                timeout=_QUERY_TIMEOUT,
            )

        if row is None:
            return None
        return _row_to_entry(row)

    async def get_models_by_state(
        self, state: CanaryState,
    ) -> list[ModelRegistryEntry]:
        """List all models in a given canary state."""
        async with self._pool.acquire() as conn:
            await self._ensure_table(conn)
            rows = await conn.fetch(
                """
                SELECT * FROM ml_model_registry
                WHERE canary_state = $1
                ORDER BY model_name, version
                """,
                state.value,
                timeout=_QUERY_TIMEOUT,
            )

        return [_row_to_entry(r) for r in rows]

    async def update_state(
        self,
        model_name: str,
        version: str,
        new_state: CanaryState,
    ) -> None:
        """Transition a model to a new canary state.

        Sets ``canary_start_time`` when entering a canary state.
        """
        now = datetime.now(timezone.utc)
        canary_time = now if _is_canary_state(new_state) else None

        async with self._pool.acquire() as conn:
            await self._ensure_table(conn)
            await conn.execute(
                """
                UPDATE ml_model_registry
                SET canary_state = $3,
                    canary_start_time = COALESCE($4, canary_start_time),
                    updated_at = $5
                WHERE model_name = $1 AND version = $2
                """,
                model_name, version, new_state.value,
                canary_time, now,
                timeout=_QUERY_TIMEOUT,
            )

        logger.info(
            "model_state_changed | name={} | version={} | new_state={}",
            model_name, version, new_state.value,
        )

    async def update_metrics(
        self,
        model_name: str,
        version: str,
        metrics: dict[str, Any],
    ) -> None:
        """Merge new performance metrics into the JSONB column."""
        async with self._pool.acquire() as conn:
            await self._ensure_table(conn)
            await conn.execute(
                """
                UPDATE ml_model_registry
                SET performance_metrics = performance_metrics || $3::jsonb,
                    updated_at = NOW()
                WHERE model_name = $1 AND version = $2
                """,
                model_name, version,
                _encode_jsonb(metrics),
                timeout=_QUERY_TIMEOUT,
            )

    async def decrement_shadow_cycles(
        self, model_name: str, version: str,
    ) -> int:
        """Atomically decrement shadow_cycles_remaining.

        Returns:
            The new value after decrement.
        """
        async with self._pool.acquire() as conn:
            await self._ensure_table(conn)
            row = await conn.fetchrow(
                """
                UPDATE ml_model_registry
                SET shadow_cycles_remaining = GREATEST(
                        shadow_cycles_remaining - 1, 0
                    ),
                    updated_at = NOW()
                WHERE model_name = $1 AND version = $2
                RETURNING shadow_cycles_remaining
                """,
                model_name, version,
                timeout=_QUERY_TIMEOUT,
            )

        remaining = row["shadow_cycles_remaining"] if row else 0
        return int(remaining)


# ---------------------------------------------------------------------------
# Pure helper functions
# ---------------------------------------------------------------------------


def _build_new_entry(
    model_name: str,
    version: str,
    task_type: str,
    shadow_cycles: int,
) -> ModelRegistryEntry:
    """Construct a ModelRegistryEntry for a newly registered model."""
    return ModelRegistryEntry(
        model_name=model_name,
        version=version,
        task_type=task_type,
        shadow_cycles_remaining=shadow_cycles,
    )


def _row_to_entry(row: asyncpg.Record) -> ModelRegistryEntry:
    """Convert an asyncpg Record to a ModelRegistryEntry."""
    metrics = row["performance_metrics"]
    if isinstance(metrics, str):
        metrics = msgspec.json.decode(metrics.encode("utf-8"))
    elif metrics is None:
        metrics = {}

    return ModelRegistryEntry(
        model_name=row["model_name"],
        version=row["version"],
        canary_state=CanaryState(row["canary_state"]),
        task_type=row.get("task_type", "classification"),
        shadow_cycles_remaining=row["shadow_cycles_remaining"],
        canary_start_time=row["canary_start_time"],
        performance_metrics=metrics,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _is_canary_state(state: CanaryState) -> bool:
    """Return True if the state is a canary (not shadow/live/retired)."""
    return state in {CanaryState.CANARY_10PCT, CanaryState.CANARY_50PCT}


def _encode_jsonb(data: dict[str, Any]) -> str:
    """Encode a dict to a JSON string for JSONB merge."""
    return msgspec.json.encode(data).decode("utf-8")
