# ATLAS Intelligence Engine

This repository contains the ATLAS intelligence engine and the PROMETHEUS reconciliation system.

## Where the FastAPI app lives

- **Production / dev HTTP server:** `backend.main:app` — started via `uvicorn backend.main:app` (see below). Lifespan builds typed `AtlasAppState`, runs `run_fastapi_startup_checks` (Polaris steps 1–3 + deferred 4–9 markers), and mounts Prometheus portfolio routes.
- **`atlas.main`:** thin re-export of `backend.main:app` only so `from atlas.main import app` keeps working; it does not define a separate ASGI stack.
- **Full nine-step `PolarisStartup`:** for standalone analysis-engine processes (Qdrant, provider registry, orchestrator loop). Use `python -m atlas` only with `POLARIS_ALLOW_STANDALONE_CLI=1` after injecting real dependencies — default CLI exits with instructions because stubs are unsafe.

## Local Paper Demo Bring-Up

The local development stack is defined in `docker-compose.dev.yml` and provides
Redis, Postgres with pgvector support, and Qdrant using the same ports as the
default `PolarisSettings` and `PrometheusSettings` values.

1. Create a local environment file from `.env.example` and fill in the Bitget
   demo credentials before enabling exchange writes.
2. Start local infrastructure:
   ```bash
   docker compose -f docker-compose.dev.yml up -d
   ```
3. Apply the idempotent RAG/history migrations:
   ```bash
   python -m atlas.rag.migrations.run_migrations
   ```
4. Start the backend (default dev port **8787** — keeps **8000** free for tools
   like Dune MCP):
   ```bash
   uvicorn backend.main:app --host 0.0.0.0 --port 8787
   ```
   If you use another port, export `ATLAS_API_PORT` to match before `npm run dev`
   so the Vite proxy still lines up.
5. Start the frontend:
   ```bash
   cd frontend && npm run dev
   ```

The LM Studio defaults in `.env.example` target:

- Chat: `mistralai/mistral-3-14b-reasoning`
- Embeddings: `text-embedding-qwen3-embedding-0.6b`
- Base URL: `http://192.168.5.26:1234/v1`

## HYDRA Side-by-Side Bring-Up

From the `Engine 8` workspace root, ATLAS infrastructure and the standalone
HYDRA stack can be launched together without sharing code or Redis data paths:

```bash
docker compose -f docker-compose.hydra-atlas.yml up -d
```

POLARIS exposes a `/hydra` page that opens the HYDRA dashboard at
`http://127.0.0.1:3001` by default. Set `VITE_HYDRA_DASHBOARD_URL` in the
frontend environment to point at another HYDRA dashboard origin.

Paper trading has three separate controls:

- `PAPER_TRADE_EXECUTOR_ENABLED=true` starts the PROMETHEUS Redis subscriber.
- `PAPER_TRADE_DRY_RUN=true` ACKs paper instructions without exchange writes.
- `BITGET_DEMO_WRITE_ENABLED=true` allows Bitget demo-market writes when
  `PAPER_TRADE_DRY_RUN=false`.

Keep `PAPER_TRADE_DRY_RUN=true` until the demo-symbol cache, Redis ACK path, and
database migrations are verified.

## Pre-commit Hooks (Sentinel Audit)

This project enforces strict invariants (the ATLAS/PROMETHEUS hard wall and central registry rules) using pre-commit hooks.

### Installation

To set up the pre-commit hooks locally:

1. Install `pre-commit` in your environment (if not already installed):
   ```bash
   pip install pre-commit
   ```

2. Install the git hook scripts:
   ```bash
   pre-commit install
   ```

### Running the Hooks

The hooks will run automatically on `git commit`. 

To run the hooks manually on all files:
```bash
pre-commit run --all-files
```

### Overriding the Invariant Checker

In rare cases where an invariant must be broken (e.g. for testing or a very specific edge case), you can bypass the `check_invariants` hook for a specific file by adding the following exact comment string anywhere in the file:

```python
# SKIP_INVARIANT_CHECK
```
*Note: Any use of this override must be logged and reviewed manually.*
