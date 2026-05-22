#!/usr/bin/env bash
# One entrypoint: Docker (Redis/Postgres/Qdrant), migrations, backend + frontend terminals,
# then open the POLARIS dashboard in the default browser (Vite dev server on :5173).

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export ATLAS_UI_URL="${ATLAS_UI_URL:-http://127.0.0.1:5173/}"
export ATLAS_OPEN_BROWSER="${ATLAS_OPEN_BROWSER:-1}"

exec bash "$ROOT/scripts/atlas-dev-stack.sh"
