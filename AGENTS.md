# AGENTS.md

## Cursor Cloud specific instructions

### Services overview

| Service | Command | Port | Notes |
|---------|---------|------|-------|
| Infrastructure (Redis, Postgres, Qdrant) | `docker compose -f docker-compose.dev.yml up -d` | 6379, 5433, 6333 | Must be running before backend starts |
| Backend (FastAPI) | `uvicorn backend.main:app --host 0.0.0.0 --port 8787` | 8787 | Health: `GET /api/health` |
| Frontend (Vite) | `cd frontend && npm run dev` | 5173 | Proxies `/api` and `/ws` to backend |

### Non-obvious caveats

- **PATH**: Python scripts (`uvicorn`, `pytest`, `pyright`) install to `/home/ubuntu/.local/bin`. Ensure this is on PATH (`export PATH="/home/ubuntu/.local/bin:$PATH"`).
- **uvloop required**: The `atlas` package imports `uvloop` at the top level (`atlas/shared/loop.py`). It is not listed in `pyproject.toml` dependencies — install it with `pip install uvloop`.
- **Docker nested container workaround**: The cloud VM runs inside a container. Docker requires `fuse-overlayfs` storage driver and `iptables-legacy`. See the daemon config at `/etc/docker/daemon.json`.
- **Binance WebSocket 451**: The backend's Coinbase Premium service will log repeated 451 errors for the Binance WebSocket — this is expected in cloud environments without direct exchange access and does not affect health.
- **fakeredis fallback**: If Redis is unavailable, the backend falls back to in-memory `fakeredis`. Many features degrade but the app still starts.
- **Postgres port 5433**: Docker maps Postgres to host port 5433 (not 5432) to avoid conflicts.
- **Migrations**: Run `python3 -m atlas.rag.migrations.run_migrations` after infrastructure is up. Idempotent — safe to rerun.

### Running tests

- **Python**: `pytest` from repo root. Expect ~1461 passing tests. Collection errors for GNN tests require `torch-geometric` (optional `[gnn]` extra — heavy dependency, not required for core flow).
- **Frontend**: `cd frontend && npx vitest run` — 196 tests, all pass.
- **Linting**: `pyright backend/` (Python type checking), `cd frontend && npx eslint src/` (JS/TS linting).

### Environment variables

Copy `.env.example` → `.env` at repo root. The defaults match `docker-compose.dev.yml` ports and are sufficient for local development without exchange writes. No real API keys are needed for basic health/dashboard functionality.
