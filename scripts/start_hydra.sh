#!/usr/bin/env bash
# Bring up HYDRA (Redis + core API + dashboard) next to this ATLAS checkout.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ATLAS_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
HYDRA_ROOT="$(cd "${ATLAS_ROOT}/../HYDRA" && pwd)"

if [[ ! -f "${HYDRA_ROOT}/docker-compose.yml" ]]; then
  echo "HYDRA docker-compose.yml not found at ${HYDRA_ROOT}" >&2
  exit 1
fi

echo "Starting Hydra stack from ${HYDRA_ROOT} ..."
(cd "${HYDRA_ROOT}" && docker compose up -d)

if command -v redis-cli >/dev/null 2>&1; then
  echo "Waiting for Hydra Redis (127.0.0.1:6380) ..."
  _ok=0
  for _ in $(seq 1 45); do
    if redis-cli -u redis://127.0.0.1:6380 ping 2>/dev/null | grep -q PONG; then
      _ok=1
      echo "Hydra Redis responded to PING."
      break
    fi
    sleep 1
  done
  if [[ "${_ok}" -eq 0 ]]; then
    echo "Warning: Hydra Redis did not respond on 6380 — check: docker compose -f ${HYDRA_ROOT}/docker-compose.yml ps" >&2
  fi
fi

echo ""
echo "ATLAS cascade streams + hydra:* keys: HYDRA_REDIS_URL=redis://127.0.0.1:6380/1 (PolarisSettings default matches)."
echo "Hydra API health: curl -s http://127.0.0.1:8001/health"
echo "Dashboard: http://127.0.0.1:3001/"
