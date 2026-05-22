"""Async DB connection for the decision log MCP. Read-only."""

import os
import asyncpg

_pool = None

async def get_pool():
    global _pool
    if _pool is None:
        async def init_connection(conn: asyncpg.Connection) -> None:
            await conn.prepare("SELECT 1 AS warmup")
            
        _pool = await asyncpg.create_pool(
            dsn=os.environ.get("DATABASE_URL", "postgresql://localhost/polaris"),
            min_size=1,
            max_size=3,
            command_timeout=10,
            init=init_connection,
        )
    return _pool

async def fetch(query: str, *args) -> list[dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(query, *args)
        return [dict(row) for row in rows]
