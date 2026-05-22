#!/usr/bin/env bash
# Installs one Desktop icon: ATLAS.desktop — full stack + browser dashboard (see start-atlas-and-dashboard.sh).

set -euo pipefail

if [[ "$(id -u)" -eq 0 ]]; then
  echo "Do not run this installer with sudo — files would be owned by root and the Desktop" >&2
  echo "shortcut will often do nothing when double-clicked. Run as your normal user:" >&2
  echo "  ./scripts/install-atlas-desktop-launcher.sh" >&2
  exit 1
fi

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DESKTOP="$(xdg-user-dir DESKTOP 2>/dev/null || true)"
DESKTOP="${DESKTOP:-$HOME/Desktop}"
LAUNCHER="$DESKTOP/ATLAS.desktop"
APPS_DIR="$HOME/.local/share/applications"
MENU_LAUNCHER="$APPS_DIR/atlas-polaris.desktop"
SCRIPT="$ROOT/scripts/start-atlas-and-dashboard.sh"
BASH_BIN="$(command -v bash 2>/dev/null || printf '%s' '/bin/bash')"
ICON_SRC="$ROOT/frontend/public/atlas_logo.png"
ICON_DST="$HOME/.local/share/icons/atlas-polaris.png"

mark_launcher_trusted() {
  local file="$1"
  if command -v gio >/dev/null 2>&1; then
    gio set "$file" metadata::trusted true 2>/dev/null || true
  fi
}

write_launcher_file() {
  local target="$1"
  local icon_line="Icon=applications-engineering"
  if [[ -f "$ICON_DST" ]]; then
    icon_line="Icon=$ICON_DST"
  fi
  {
    echo "[Desktop Entry]"
    echo "Version=1.0"
    echo "Type=Application"
    echo "Name=ATLAS"
    echo "Comment=Docker, LM Studio API, migrations, API :8787, UI :5173 — opens dashboard"
    echo "Keywords=ATLAS;POLARIS;Docker;LMStudio;Redis;Trading;"
    echo "Terminal=true"
    echo "Categories=Development;"
    echo "StartupNotify=true"
    echo "DBusActivatable=false"
    printf 'TryExec=%s\n' "$BASH_BIN"
    printf 'Exec=%s "%s"\n' "$BASH_BIN" "$SCRIPT"
    echo "$icon_line"
  } >"$target"
}

mkdir -p "$DESKTOP" "$APPS_DIR"

# Single icon: remove older launcher names so only ATLAS.desktop remains.
for legacy in "$DESKTOP/ATLAS Dev Stack.desktop" "$DESKTOP/Start ATLAS & Dashboard.desktop"; do
  if [[ -e "$legacy" ]]; then
    rm -f "$legacy"
    echo "Removed legacy shortcut: $legacy"
  fi
done

chmod +x "$ROOT/scripts/atlas-dev-stack.sh"
chmod +x "$ROOT/scripts/start-atlas-and-dashboard.sh"
chmod +x "$ROOT/scripts/install-atlas-desktop-launcher.sh"

if [[ -f "$ICON_SRC" ]]; then
  mkdir -p "$(dirname "$ICON_DST")"
  cp -f "$ICON_SRC" "$ICON_DST"
fi

write_launcher_file "$LAUNCHER"
write_launcher_file "$MENU_LAUNCHER"
chmod +x "$LAUNCHER" "$MENU_LAUNCHER"
mark_launcher_trusted "$LAUNCHER"
mark_launcher_trusted "$MENU_LAUNCHER"

if command -v update-desktop-database >/dev/null 2>&1; then
  update-desktop-database "$APPS_DIR" 2>/dev/null || true
fi

echo "Installed: $LAUNCHER"
echo "Installed: $MENU_LAUNCHER (application menu / gtk-launch atlas-polaris)"
echo "You now have one Desktop icon «ATLAS» — double-click to start the full stack and open the dashboard."
echo "Re-run this installer after moving the repo so Exec stays correct."
