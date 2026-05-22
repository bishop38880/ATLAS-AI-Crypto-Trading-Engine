"""Idempotent migration runner for RAG database tables.

Uses ``asyncpg`` directly. Tracks applied migrations in the
``rag_migrations`` table so each SQL file runs at most once.

Usage::

    python -m atlas.rag.migrations.run_migrations
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import asyncpg
from loguru import logger

_MIGRATION_DIR = Path(__file__).resolve().parent
_SQL_GLOB = "*.sql"


# ---------------------------------------------------------------------------
# Bootstrap — ensure the tracker table exists
# ---------------------------------------------------------------------------


async def _ensure_tracker(conn: asyncpg.Connection) -> None:
    """Create the ``rag_migrations`` table if it does not exist.

    Args:
        conn: Active asyncpg connection.
    """
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS rag_migrations (
            migration_id  TEXT PRIMARY KEY,
            applied_at    TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        """
    )


# ---------------------------------------------------------------------------
# Core runner
# ---------------------------------------------------------------------------


async def _already_applied(
    conn: asyncpg.Connection,
    migration_id: str,
) -> bool:
    """Check whether a migration has already been applied.

    Args:
        conn: Active asyncpg connection.
        migration_id: Name of the migration file.

    Returns:
        True if the migration row exists.
    """
    row = await conn.fetchval(
        "SELECT 1 FROM rag_migrations WHERE migration_id = $1",
        migration_id,
    )
    return row is not None


async def _apply_migration(
    conn: asyncpg.Connection,
    migration_id: str,
    sql: str,
) -> None:
    """Apply a single migration inside a transaction.

    Args:
        conn: Active asyncpg connection.
        migration_id: Name of the migration file.
        sql: Raw SQL to execute.
    """
    async with conn.transaction():
        await conn.execute(sql)
        await conn.execute(
            "INSERT INTO rag_migrations (migration_id) VALUES ($1)",
            migration_id,
        )
    logger.info("Applied migration: {}", migration_id)


async def run_migrations(postgres_url: str) -> list[str]:
    """Discover and apply all pending SQL migrations.

    Migrations are sorted lexicographically (``001_…``, ``002_…``).
    Each migration is applied exactly once; re-runs are no-ops.

    Args:
        postgres_url: PostgreSQL connection string.

    Returns:
        List of migration IDs that were applied in this run.
    """
    conn: asyncpg.Connection = await asyncpg.connect(postgres_url, timeout=30)
    applied: list[str] = []

    try:
        await _ensure_tracker(conn)
        sql_files = sorted(_MIGRATION_DIR.glob(_SQL_GLOB))

        for sql_path in sql_files:
            migration_id = sql_path.name
            if await _already_applied(conn, migration_id):
                logger.debug("Skipping (already applied): {}", migration_id)
                continue

            sql = sql_path.read_text(encoding="utf-8")
            await _apply_migration(conn, migration_id, sql)
            applied.append(migration_id)

        if not applied:
            logger.info("All migrations already applied — nothing to do.")
    finally:
        await conn.close()

    return applied


# ---------------------------------------------------------------------------
# CLI entry-point
# ---------------------------------------------------------------------------


async def _main() -> None:
    """Run migrations using ``PolarisSettings.postgres_url``."""
    from atlas.shared.config import PolarisSettings

    settings = PolarisSettings()  # type: ignore[call-arg]
    applied = await run_migrations(settings.postgres_url)
    logger.info("Migration run complete. Applied: {}", applied)


if __name__ == "__main__":
    asyncio.run(_main())
