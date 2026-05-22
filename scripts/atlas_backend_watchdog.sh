#!/usr/bin/env bash
# Lightweight watchdog: curl ATLAS health endpoint on an interval.
#
# Usage:
#   ATLAS_WATCHDOG_URL=http://127.0.0.1:8787/api/health bash scripts/atlas_backend_watchdog.sh
#
# Optional Discord webhook (safe JSON via Python stdlib):
#   DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/... bash scripts/atlas_backend_watchdog.sh
#
# Cron (every 5 minutes):
#   */5 * * * * cd /path/to/ATLAS && ATLAS_WATCHDOG_URL=http://127.0.0.1:8787/api/health bash scripts/atlas_backend_watchdog.sh
#
set -euo pipefail

URL="${ATLAS_WATCHDOG_URL:-http://127.0.0.1:8787/api/health}"
TIMEOUT="${ATLAS_WATCHDOG_TIMEOUT_SECONDS:-12}"

if curl -fsS --max-time "${TIMEOUT}" "${URL}" >/dev/null; then
  echo "ok | ${URL}"
  exit 0
fi

msg="ATLAS watchdog FAILED | url=${URL} | no healthy response within ${TIMEOUT}s"
echo "${msg}" >&2

if [[ -n "${DISCORD_WEBHOOK_URL:-}" ]]; then
  MSG="${msg}" python3 <<'PY'
import json
import os
import urllib.error
import urllib.request

text = os.environ["MSG"]
url = os.environ["DISCORD_WEBHOOK_URL"]
body = json.dumps({"content": text}).encode("utf-8")
req = urllib.request.Request(
    url,
    data=body,
    headers={"Content-Type": "application/json"},
    method="POST",
)
try:
    urllib.request.urlopen(req, timeout=15)
except urllib.error.URLError:
    pass
PY
fi

if [[ -n "${TELEGRAM_BOT_TOKEN:-}" && -n "${TELEGRAM_CHAT_ID:-}" ]]; then
  curl -fsS -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
    -d "chat_id=${TELEGRAM_CHAT_ID}" \
    --data-urlencode "text=${msg}" >/dev/null || true
fi

exit 1
