#!/usr/bin/env bash
# ATLAS / POLARIS — bring up docker infra, migrations, backend + frontend.
# Intended for use from the Desktop launcher or: ./scripts/atlas-dev-stack.sh
#
# Optional:
#   ATLAS_START_HYDRA=1          — also runs ../docker-compose.hydra-atlas.yml (Engine 8 root)
#   ATLAS_SKIP_LM_STUDIO=1       — do not run ``lms server start --cors``
#   ATLAS_LM_STUDIO_CMD="..."    — custom shell command instead of ``lms server start --cors``

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_FILE="$ROOT/docker-compose.dev.yml"
export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"

# .desktop / GUI launches often inherit a minimal PATH (no Docker from snap, no npm from nvm).
bootstrap_environment_for_gui_launch() {
  case "${ATLAS_SKIP_GUI_BOOTSTRAP:-0}" in
    1 | true | TRUE | yes | YES) return 0 ;;
  esac
  export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin${PATH:+:$PATH}"
  if [[ -d "${HOME}/.local/bin" ]]; then
    export PATH="${HOME}/.local/bin:${PATH}"
  fi
  if [[ -d "/snap/bin" ]]; then
    export PATH="${PATH}:/snap/bin"
  fi
  if [[ -f "${HOME}/.nvm/nvm.sh" ]]; then
    # shellcheck source=/dev/null
    . "${HOME}/.nvm/nvm.sh"
  fi
  if [[ -d "${HOME}/.volta/bin" ]]; then
    export PATH="${HOME}/.volta/bin:${PATH}"
  fi
}

bootstrap_environment_for_gui_launch
# Avoid :8000 (common MCP default; Cursor sandboxes can leave stale listeners).
ATLAS_API_PORT="${ATLAS_API_PORT:-8787}"
export ATLAS_API_PORT
ATLAS_API_HOST="${ATLAS_API_HOST:-127.0.0.1}"
export ATLAS_API_HOST

notify() {
  if command -v notify-send >/dev/null 2>&1; then
    notify-send -a "ATLAS" "$1" "${2:-}" || true
  fi
}

die() {
  echo "ERROR: $*" >&2
  notify "Startup failed" "$*"
  exit 1
}

command -v docker >/dev/null 2>&1 || die "docker not found in PATH"
docker info >/dev/null 2>&1 || die "Docker daemon is not running — start Docker first"

cd "$ROOT"

ATLAS_DEV_CONTAINER_NAMES=(
  atlas-redis-dev
  atlas-postgres-dev
  atlas-qdrant-dev
)

dev_container_running() {
  local name="$1"
  docker inspect -f '{{.State.Running}}' "$name" 2>/dev/null | grep -q true
}

all_dev_containers_running() {
  local name
  for name in "${ATLAS_DEV_CONTAINER_NAMES[@]}"; do
    if ! dev_container_running "$name"; then
      return 1
    fi
  done
  return 0
}

start_stopped_dev_containers_by_name() {
  local name status
  for name in "${ATLAS_DEV_CONTAINER_NAMES[@]}"; do
    if ! docker container inspect "$name" >/dev/null 2>&1; then
      continue
    fi
    status="$(docker inspect -f '{{.State.Status}}' "$name" 2>/dev/null || true)"
    if [[ "$status" == "exited" || "$status" == "created" ]]; then
      echo "    Starting existing container ${name}…"
      docker start "$name" >/dev/null 2>&1 || true
    fi
  done
}

start_dev_compose_stack() {
  local compose_log
  compose_log="$(mktemp)"
  echo "==> Starting Redis, Postgres, Qdrant (docker compose)…"
  start_stopped_dev_containers_by_name
  if all_dev_containers_running; then
    echo "    Redis, Postgres, and Qdrant already running"
    rm -f "$compose_log"
    return 0
  fi
  if docker compose -f "$COMPOSE_FILE" up -d >"$compose_log" 2>&1; then
    rm -f "$compose_log"
    return 0
  fi
  if grep -qiE 'already in use|Conflict' "$compose_log" 2>/dev/null; then
    echo "    Compose reported a name conflict — reusing existing dev containers…"
    start_stopped_dev_containers_by_name
    if all_dev_containers_running; then
      echo "    Named dev containers are up (compose project may be out of sync)"
      rm -f "$compose_log"
      return 0
    fi
    if docker compose -f "$COMPOSE_FILE" up -d --no-recreate >>"$compose_log" 2>&1; then
      rm -f "$compose_log"
      return 0
    fi
    if all_dev_containers_running; then
      echo "    Named dev containers are up after compose conflict"
      rm -f "$compose_log"
      return 0
    fi
  fi
  cat "$compose_log" >&2
  rm -f "$compose_log"
  die "docker compose failed — see errors above"
}

start_dev_compose_stack

if [[ "${ATLAS_START_HYDRA:-0}" == "1" ]]; then
  HYDRA_COMPOSE="$(cd "$ROOT/.." && pwd)/docker-compose.hydra-atlas.yml"
  if [[ -f "$HYDRA_COMPOSE" ]]; then
    echo "==> Starting HYDRA + ATLAS sidecar (docker compose from Engine 8 root)…"
    docker compose -f "$HYDRA_COMPOSE" up -d
  else
    echo "    NOTE: ATLAS_START_HYDRA=1 but file missing: $HYDRA_COMPOSE" >&2
  fi
fi

wait_redis() {
  echo "==> Waiting for Redis…"
  local i=0
  while [[ i -lt 45 ]]; do
    if docker exec atlas-redis-dev redis-cli ping 2>/dev/null | grep -q PONG; then
      echo "    Redis OK"
      return 0
    fi
    i=$((i + 1))
    sleep 1
  done
  die "Redis did not become ready within 45s"
}

wait_postgres() {
  echo "==> Waiting for Postgres…"
  local i=0
  while [[ i -lt 45 ]]; do
    if docker exec atlas-postgres-dev pg_isready -U postgres -d atlas >/dev/null 2>&1; then
      echo "    Postgres OK"
      return 0
    fi
    i=$((i + 1))
    sleep 1
  done
  die "Postgres did not become ready within 45s"
}

wait_tcp_host() {
  local label="$1" host="$2" port="$3"
  echo "==> Waiting for ${label} (TCP ${host}:${port})…"
  local i=0
  while [[ i -lt 45 ]]; do
    if timeout 1 bash -c "</dev/tcp/${host}/${port}" >/dev/null 2>&1; then
      echo "    ${label} OK"
      return 0
    fi
    i=$((i + 1))
    sleep 1
  done
  die "${label} did not accept connections within 45s"
}

wait_redis
wait_postgres
wait_tcp_host "Qdrant" "127.0.0.1" "6333"

start_lm_studio_server() {
  case "${ATLAS_SKIP_LM_STUDIO:-0}" in
    1 | true | TRUE | yes | YES)
      return 0
      ;;
  esac
  if [[ -n "${ATLAS_LM_STUDIO_CMD:-}" ]]; then
    echo "==> LM Studio (ATLAS_LM_STUDIO_CMD)…"
    # shellcheck disable=SC2086
    eval "$ATLAS_LM_STUDIO_CMD"
    return 0
  fi
  local lms_bin=""
  if command -v lms >/dev/null 2>&1; then
    lms_bin="$(command -v lms)"
  elif [[ -x "${HOME}/.lmstudio/bin/lms" ]]; then
    lms_bin="${HOME}/.lmstudio/bin/lms"
  fi
  if [[ -z "$lms_bin" ]]; then
    echo "==> LM Studio CLI (lms) not on PATH — open the app manually or install LM Studio (optional)."
    notify "LM Studio" "Add ~/.lmstudio/bin to PATH for auto-start, or set ATLAS_SKIP_LM_STUDIO=1."
    return 0
  fi
  if timeout 1 bash -c "</dev/tcp/127.0.0.1/1234" >/dev/null 2>&1; then
    echo "==> LM Studio API already on :1234 — skip"
    return 0
  fi
  echo "==> Starting LM Studio local server (lms server start --cors)…"
  : >"$ROOT/.atlas-lmstudio.log"
  nohup "$lms_bin" server start --cors >>"$ROOT/.atlas-lmstudio.log" 2>&1 &
  disown "$!" 2>/dev/null || true
}

start_lm_studio_server

if timeout 1 bash -c "</dev/tcp/127.0.0.1/${ATLAS_API_PORT}" >/dev/null 2>&1; then
  echo "    NOTE: port ${ATLAS_API_PORT} is already in use — backend tab may fail to bind until it is free." >&2
fi
if timeout 1 bash -c "</dev/tcp/127.0.0.1/5173" >/dev/null 2>&1; then
  echo "    NOTE: port 5173 is already in use — frontend tab may fail to bind until it is free." >&2
fi

PYTHON="python3"
if [[ -x "$ROOT/.venv/bin/python" ]]; then
  PYTHON="$ROOT/.venv/bin/python"
fi
command -v "$PYTHON" >/dev/null 2>&1 || die "Python not found (expected python3 or $ROOT/.venv/bin/python)"

command -v npm >/dev/null 2>&1 || die "npm not found in PATH — Node.js is required for the dashboard dev server. Install Node.js or ensure nvm/Volta is under your home directory (the stack script loads nvm when ${HOME}/.nvm/nvm.sh exists)."

echo "==> Applying RAG / history migrations (idempotent)…"
"$PYTHON" -m atlas.rag.migrations.run_migrations

if [[ ! -d "$ROOT/frontend/node_modules" ]]; then
  echo "==> Installing frontend dependencies (first run)…"
  (cd "$ROOT/frontend" && npm install)
fi

launch_terminals() {
  local uvicorn_bin="uvicorn"
  if [[ -x "$ROOT/.venv/bin/uvicorn" ]]; then
    uvicorn_bin="$ROOT/.venv/bin/uvicorn"
  fi

  local backend_cmd="cd $(printf %q "$ROOT") && $(printf %q "$uvicorn_bin") backend.main:app --host 0.0.0.0 --port $(printf %q "$ATLAS_API_PORT"); exec bash"
  local frontend_cmd="export ATLAS_API_HOST=$(printf %q "$ATLAS_API_HOST") ATLAS_API_PORT=$(printf %q "$ATLAS_API_PORT"); cd $(printf %q "$ROOT")/frontend && npm run dev; exec bash"
  local skip_fe="${ATLAS_SKIP_FRONTEND_TAB:-0}"

  run_second_window() {
    if [[ "$skip_fe" == "1" ]]; then
      return 0
    fi
    sleep 0.85
  }

  if command -v gnome-terminal >/dev/null 2>&1; then
    if [[ "$skip_fe" == "1" ]]; then
      gnome-terminal --window --tab --title="ATLAS Backend" -- bash -lc "$backend_cmd" &
    else
      gnome-terminal --window \
        --tab --title="ATLAS Backend" -- bash -lc "$backend_cmd" \
        --tab --title="ATLAS Frontend" -- bash -lc "$frontend_cmd" &
    fi
    return 0
  fi

  if command -v tilix >/dev/null 2>&1; then
    tilix -t "ATLAS Backend" -- bash -lc "$backend_cmd" >/dev/null 2>&1 &
    run_second_window
    if [[ "$skip_fe" != "1" ]]; then
      tilix -t "ATLAS Frontend" -- bash -lc "$frontend_cmd" >/dev/null 2>&1 &
    fi
    return 0
  fi

  for term in ptyxis kgx; do
    if command -v "$term" >/dev/null 2>&1; then
      "$term" -- bash -lc "$backend_cmd" >/dev/null 2>&1 &
      run_second_window
      if [[ "$skip_fe" != "1" ]]; then
        "$term" -- bash -lc "$frontend_cmd" >/dev/null 2>&1 &
      fi
      return 0
    fi
  done

  if command -v xfce4-terminal >/dev/null 2>&1; then
    xfce4-terminal -T "ATLAS Backend" -e "bash -lc $(printf %q "$backend_cmd")" >/dev/null 2>&1 &
    run_second_window
    if [[ "$skip_fe" != "1" ]]; then
      xfce4-terminal -T "ATLAS Frontend" -e "bash -lc $(printf %q "$frontend_cmd")" >/dev/null 2>&1 &
    fi
    return 0
  fi

  if command -v konsole >/dev/null 2>&1; then
    konsole --title "ATLAS Backend" -e bash -lc "$backend_cmd" >/dev/null 2>&1 &
    run_second_window
    if [[ "$skip_fe" != "1" ]]; then
      konsole --title "ATLAS Frontend" -e bash -lc "$frontend_cmd" >/dev/null 2>&1 &
    fi
    return 0
  fi

  if command -v cosmic-term >/dev/null 2>&1; then
    cosmic-term -- bash -lc "$backend_cmd" >/dev/null 2>&1 &
    run_second_window
    if [[ "$skip_fe" != "1" ]]; then
      cosmic-term -- bash -lc "$frontend_cmd" >/dev/null 2>&1 &
    fi
    return 0
  fi

  if command -v foot >/dev/null 2>&1; then
    foot bash -lc "$backend_cmd" >/dev/null 2>&1 &
    run_second_window
    if [[ "$skip_fe" != "1" ]]; then
      foot bash -lc "$frontend_cmd" >/dev/null 2>&1 &
    fi
    return 0
  fi

  if command -v alacritty >/dev/null 2>&1; then
    alacritty -e bash -lc "$backend_cmd" >/dev/null 2>&1 &
    run_second_window
    if [[ "$skip_fe" != "1" ]]; then
      alacritty -e bash -lc "$frontend_cmd" >/dev/null 2>&1 &
    fi
    return 0
  fi

  if command -v kitty >/dev/null 2>&1; then
    kitty bash -lc "$backend_cmd" >/dev/null 2>&1 &
    run_second_window
    if [[ "$skip_fe" != "1" ]]; then
      kitty bash -lc "$frontend_cmd" >/dev/null 2>&1 &
    fi
    return 0
  fi

  if command -v x-terminal-emulator >/dev/null 2>&1; then
    x-terminal-emulator -T "ATLAS Backend" -e bash -lc "$backend_cmd" >/dev/null 2>&1 &
    run_second_window
    if [[ "$skip_fe" != "1" ]]; then
      x-terminal-emulator -T "ATLAS Frontend" -e bash -lc "$frontend_cmd" >/dev/null 2>&1 &
    fi
    return 0
  fi

  die "No terminal emulator found (tried gnome-terminal, tilix, ptyxis, kgx, xfce4-terminal, konsole, cosmic-term, foot, alacritty, kitty, x-terminal-emulator)"
}

vite_port_open() {
  timeout 1 bash -c "</dev/tcp/127.0.0.1/5173" >/dev/null 2>&1
}

wait_for_vite_port() {
  local max_seconds="$1"
  local phase="$2"
  echo "==> Waiting for Vite on 5173 (${phase}, up to ${max_seconds}s)…"
  local i=0
  while [[ i -lt max_seconds ]]; do
    if vite_port_open; then
      echo "    Vite OK (port 5173 listening)"
      return 0
    fi
    i=$((i + 1))
    sleep 1
  done
  return 1
}

ensure_vite_dev_server() {
  local skip_fe="${ATLAS_SKIP_FRONTEND_TAB:-0}"
  if [[ "$skip_fe" == "1" ]]; then
    echo "==> Skipping Vite dashboard (ATLAS_SKIP_FRONTEND_TAB=1)"
    return 2
  fi

  local first_wait="${ATLAS_VITE_FIRST_WAIT_SECONDS:-75}"
  if wait_for_vite_port "$first_wait" "after GUI terminals"; then
    return 0
  fi

  case "${ATLAS_DISABLE_VITE_FALLBACK:-0}" in
    1 | true | TRUE | yes | YES)
      echo "ERROR: Vite did not start and inline fallback is disabled (ATLAS_DISABLE_VITE_FALLBACK)." >&2
      notify "ATLAS" "Vite not on :5173 — check the Frontend terminal tab for npm errors."
      return 1
      ;;
  esac

  local logf="$ROOT/.atlas-vite.log"
  echo "==> Vite still down — starting inline dev server (log: ${logf})…"
  : >"$logf"
  nohup bash -c "cd $(printf %q "$ROOT")/frontend && export ATLAS_API_HOST=$(printf %q "$ATLAS_API_HOST") ATLAS_API_PORT=$(printf %q "$ATLAS_API_PORT") && npm run dev" >>"$logf" 2>&1 &
  disown "$!" 2>/dev/null || true

  local fb_wait="${ATLAS_VITE_FALLBACK_WAIT_SECONDS:-50}"
  if wait_for_vite_port "$fb_wait" "inline npm run dev"; then
    notify "ATLAS Vite" "Started inline fallback — logs: ${logf}"
    return 0
  fi

  echo "ERROR: Vite never listened on 5173." >&2
  echo "---- tail ${logf} ----" >&2
  tail -n 50 "$logf" 2>/dev/null >&2 || true
  notify "ATLAS" "Vite failed — see ${logf} and any Frontend terminal"
  return 1
}

ensure_graphical_launch_environment() {
  local uid rid leader kv
  uid="$(id -u)"

  if [[ -z "${DBUS_SESSION_BUS_ADDRESS:-}" && -S "/run/user/${uid}/bus" ]]; then
    export DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/${uid}/bus"
  fi

  if [[ -n "${DISPLAY:-}" || -n "${WAYLAND_DISPLAY:-}" ]]; then
    return 0
  fi

  rid="$(loginctl --no-legend list-sessions 2>/dev/null | awk -v u="$uid" '$3 == u { print $1; exit }')"
  if [[ -n "${rid:-}" ]]; then
    leader="$(loginctl show-session "$rid" -p Leader --value 2>/dev/null || true)"
    leader="${leader//$'\r'/}"
    if [[ -n "${leader:-}" && -r "/proc/${leader}/environ" ]]; then
      while IFS= read -r -d '' kv; do
        case "$kv" in
          DISPLAY=* | WAYLAND_DISPLAY=* | XDG_SESSION_TYPE=* | XDG_CURRENT_DESKTOP=*)
            export "$kv"
            ;;
        esac
      done < "/proc/${leader}/environ"
    fi
  fi

  if [[ -z "${DISPLAY:-}" && -z "${WAYLAND_DISPLAY:-}" ]]; then
    if [[ -n "${rid:-}" ]] && command -v loginctl >/dev/null 2>&1; then
      local disp
      disp="$(loginctl show-session -p Display --value "$rid" 2>/dev/null | head -n1 || true)"
      disp="${disp//$'\r'/}"
      if [[ -n "$disp" ]]; then
        export DISPLAY="$disp"
      fi
    fi
    if [[ -z "${DISPLAY:-}" && -d /tmp/.X11-unix ]]; then
      export DISPLAY=":0"
    fi
  fi
}

try_open_url() {
  local url="$1"
  local err
  err="$(mktemp)"

  if command -v xdg-open >/dev/null 2>&1; then
    if xdg-open "$url" 2>"$err"; then
      rm -f "$err"
      return 0
    fi
    if [[ -s "$err" ]]; then
      echo "    xdg-open: $(head -n3 "$err" | tr '\n' ' ')" >&2
    fi
  fi

  if command -v gio >/dev/null 2>&1; then
    if gio open "$url" 2>>"$err"; then
      rm -f "$err"
      return 0
    fi
  fi

  if command -v x-www-browser >/dev/null 2>&1; then
    if x-www-browser "$url" 2>>"$err"; then
      rm -f "$err"
      return 0
    fi
  fi

  if command -v sensible-browser >/dev/null 2>&1; then
    if sensible-browser "$url" 2>>"$err"; then
      rm -f "$err"
      return 0
    fi
  fi

  if command -v "${PYTHON}" >/dev/null 2>&1; then
    if ATLAS_OPEN_URL="$url" "${PYTHON}" -c "import os, sys, webbrowser; sys.exit(0 if webbrowser.open(os.environ['ATLAS_OPEN_URL']) else 1)" 2>>"$err"; then
      rm -f "$err"
      return 0
    fi
  fi

  if [[ -s "$err" ]]; then
    echo "    Browser launch diagnostics: $(head -n5 "$err" | tr '\n' '; ')" >&2
  fi
  rm -f "$err"
  return 1
}

open_ui_in_browser() {
  local url="${ATLAS_UI_URL:-http://127.0.0.1:5173}"

  ensure_graphical_launch_environment

  if ! vite_port_open; then
    echo "ERROR: Refusing to open browser — nothing is listening on 127.0.0.1:5173." >&2
    notify "ATLAS" "Port 5173 not ready — open ${url} manually after Vite starts."
    return 1
  fi

  echo "==> Opening UI in your default browser…"
  echo "    (session: DISPLAY=${DISPLAY:-} WAYLAND_DISPLAY=${WAYLAND_DISPLAY:-} DBUS=${DBUS_SESSION_BUS_ADDRESS:+set})"
  if try_open_url "$url"; then
    return 0
  fi

  echo "    Could not auto-open a browser." >&2
  echo "    Open this URL manually: ${url}" >&2
  notify "ATLAS UI" "Open manually: ${url}"
}

echo "==> Opening Backend + Frontend terminals…"
launch_terminals

VITE_RESULT=0
ensure_vite_dev_server || VITE_RESULT=$?

case "${ATLAS_OPEN_BROWSER:-1}" in
  0 | false | FALSE | no | NO | off | OFF) ;;
  *)
    if [[ "$VITE_RESULT" -eq 0 ]]; then
      open_ui_in_browser
    elif [[ "$VITE_RESULT" -eq 2 ]]; then
      echo "==> Skipping browser (no Vite — ATLAS_SKIP_FRONTEND_TAB=1)."
    else
      echo "==> Not opening browser until Vite is healthy — fix errors above, then open http://127.0.0.1:5173" >&2
    fi
    ;;
esac

echo ""
echo "Stack status:"
echo "  • API:     http://127.0.0.1:${ATLAS_API_PORT}"
if [[ "$VITE_RESULT" -eq 0 ]]; then
  echo "  • UI:      http://127.0.0.1:5173"
elif [[ "$VITE_RESULT" -eq 2 ]]; then
  echo "  • UI:      (frontend tab skipped)"
else
  echo "  • UI:      not running on 5173 — see .atlas-vite.log or the Frontend terminal"
fi
echo "  • Redis:   localhost:6379 (Docker)"
echo ""
if [[ "$VITE_RESULT" -eq 0 ]]; then
  notify "ATLAS stack ready" "Backend :${ATLAS_API_PORT} · Frontend :5173"
elif [[ "$VITE_RESULT" -eq 2 ]]; then
  notify "ATLAS backend" "Docker + API :${ATLAS_API_PORT} (frontend skipped)"
else
  notify "ATLAS partial startup" "Fix Vite on :5173 — log: ${ROOT}/.atlas-vite.log"
fi

if [[ -t 0 ]]; then
  read -rp "Press Enter to close this window…" _
fi
