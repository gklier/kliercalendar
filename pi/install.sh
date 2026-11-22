#!/usr/bin/env bash
# Family Dashboard installer for a Raspberry Pi that already shows DAKboard in
# Chromium. DAKboard is kept: the TV shows it until you switch in Settings.
#   cd family-dashboard && sudo ./pi/install.sh
# Display only, with the server running off-site (see README):
#   sudo DASHBOARD_SERVER=https://dash.example.com DASHBOARD_TV_KEY=<key> ./pi/install.sh
set -euo pipefail
if [[ $EUID -ne 0 ]]; then echo "Run with sudo: sudo ./pi/install.sh" >&2; exit 1; fi

SRC="$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)"
DIR="${DASHBOARD_DIR:-/opt/family-dashboard}"
RUN_USER="${DASHBOARD_USER:-${SUDO_USER:-$(logname 2>/dev/null || echo pi)}}"
PORT="${DASHBOARD_PORT:-8080}"
RUNTIME="${DASHBOARD_RUNTIME:-native}"   # native (systemd), docker, or display (server off-site)
SERVER_URL="${DASHBOARD_SERVER:-}"
TV_KEY="${DASHBOARD_TV_KEY:-}"
if [[ -n "$SERVER_URL" ]]; then RUNTIME=display; fi
if [[ "$RUNTIME" == display && ( -z "$SERVER_URL" || -z "$TV_KEY" ) ]]; then
  echo "Display-only needs DASHBOARD_SERVER and DASHBOARD_TV_KEY (make the key in Settings → On the TV)." >&2; exit 1
fi
HOME_DIR="$(getent passwd "$RUN_USER" | cut -d: -f6)"

echo "==> Checking the Pi"
if [[ "$(uname -m)" == "armv6l" ]]; then
  echo "This is an original Pi Zero / Zero W. Chromium can't run the dashboard on it." >&2; exit 1
fi
if [[ "$RUNTIME" != display ]] && ss -ltn 2>/dev/null | grep -q ":$PORT "; then
  if ! systemctl is-active --quiet family-dashboard && ! docker ps --format '{{.Names}}' 2>/dev/null | grep -q dashboard; then
    echo "Port $PORT is already in use. Re-run with DASHBOARD_PORT=8090 sudo -E ./pi/install.sh" >&2; exit 1
  fi
fi
python3 -c 'import sys; assert sys.version_info >= (3, 9)' || { echo "Python 3.9 or newer is needed." >&2; exit 1; }

echo "==> Installing packages"
if [[ "$RUNTIME" == display ]]; then
  command -v curl >/dev/null || apt-get install -y --no-install-recommends curl >/dev/null
else
  apt-get install -y --no-install-recommends python3-venv curl >/dev/null
fi
if [[ "$RUNTIME" == docker ]] && ! docker compose version >/dev/null 2>&1; then
  echo "Docker with the compose plugin isn't installed. Install it first (curl -fsSL https://get.docker.com | sh), or run without DASHBOARD_RUNTIME=docker." >&2
  exit 1
fi

echo "==> Copying files to $DIR"
mkdir -p "$DIR/data/state"
if [[ "$SRC" != "$DIR" ]]; then
  rm -rf "$DIR/web" "$DIR/server" "$DIR/pi"
  cp -r "$SRC/web" "$SRC/server" "$SRC/pi" "$SRC/Dockerfile" "$SRC/.dockerignore" "$DIR/"
  cp "$SRC/compose.pi.yaml" "$DIR/compose.yaml"   # the single-Pi Docker option; the server stack at the repo root is for another machine
fi
chmod +x "$DIR/pi/"*.sh
if [[ "$RUNTIME" == native ]]; then
  [[ -d "$DIR/venv" ]] || python3 -m venv "$DIR/venv"
  "$DIR/venv/bin/pip" install --quiet --disable-pip-version-check -r "$DIR/server/requirements.txt"
fi

echo "==> Finding how DAKboard starts"
python3 "$DIR/pi/kiosk_setup.py" install --dir "$DIR" --port "$PORT" --home "$HOME_DIR" | tee "$DIR/data/kiosk-install.log" || LAUNCHER_FAILED=1

DAK_URL="$(sed -n 's/^DAKBOARD_URL=//p' "$DIR/data/kiosk-install.log" | tail -1)"
if [[ "$RUNTIME" == display ]]; then
  [[ "${LAUNCHER_FAILED:-0}" == 1 ]] || python3 "$DIR/pi/kiosk_setup.py" configure --dir "$DIR" --server "$SERVER_URL" --tv-key "$TV_KEY"
  chown -R "$RUN_USER":"$RUN_USER" "$DIR"
  chmod 700 "$DIR/data"
  echo "==> Turning off the dashboard server on this Pi (it runs at $SERVER_URL now)"
  systemctl disable --now family-dashboard.service >/dev/null 2>&1 || true
  if [ -f "$DIR/compose.yaml" ] && command -v docker >/dev/null; then (cd "$DIR" && docker compose down >/dev/null 2>&1) || true; fi
  echo "==> Checking that $SERVER_URL knows this TV"
  if answer="$(curl -fsS --max-time 10 -H "Authorization: Bearer $TV_KEY" "$SERVER_URL/api/screen-url")"; then
    echo "It answered: the TV will show $(printf '%s\n' "$answer" | head -n1)"
  else
    echo "The server didn't accept this TV yet (wrong key, or not reachable). The TV shows DAKboard until it does." >&2
  fi
  [[ -n "$DAK_URL" ]] && echo "If DAKboard isn't set on the server yet, paste this in Settings → On the TV: $DAK_URL"
  echo "Reboot when convenient: sudo reboot. To undo everything: sudo $DIR/pi/restore.sh"
  exit 0
fi

# The server runs on this Pi: the launcher asks it (undoes an earlier display-only install)
[[ "${LAUNCHER_FAILED:-0}" == 1 ]] || python3 "$DIR/pi/kiosk_setup.py" configure --dir "$DIR" --server "" >/dev/null

# Seed settings on first install, and remember the DAKboard link either way
python3 - "$DIR" "$PORT" "$DAK_URL" <<'PY'
import sys
from pathlib import Path
base, port, url = Path(sys.argv[1]), int(sys.argv[2]), sys.argv[3]
sys.path.insert(0, str(base / "server"))
import server
store = server.Store(base / "data" / "config.json", base / "server" / "config.seed.json")
data = store.data
data["port"] = port
if url:
    data["screen"]["dakboard_url"] = url
store.save(data)
PY
chown -R "$RUN_USER":"$RUN_USER" "$DIR"
mkdir -p "$DIR/data/photos"
chmod 700 "$DIR/data"

echo "==> Starting the dashboard server ($RUNTIME)"
if [[ "$RUNTIME" == docker ]]; then
  chown -R 1000:1000 "$DIR/data"   # the container runs as uid 1000
  systemctl disable --now family-dashboard.service >/dev/null 2>&1 || true
  (cd "$DIR" && docker compose up -d --build)
else
  sed -e "s|__USER__|$RUN_USER|g" -e "s|__DIR__|$DIR|g" "$DIR/pi/family-dashboard.service" > /etc/systemd/system/family-dashboard.service
  systemctl daemon-reload
  systemctl enable --now family-dashboard.service >/dev/null
  systemctl restart family-dashboard.service
fi
for _ in $(seq 1 20); do curl -fs "http://127.0.0.1:$PORT/api/version" >/dev/null && break; sleep 1; done

IP="$(hostname -I | awk '{print $1}')"
echo
echo "Settings:   http://$IP:$PORT/admin/   (also http://$(hostname).local:$PORT/admin/)"
if [[ "${LAUNCHER_FAILED:-0}" == 1 ]]; then
  echo "The server is running, but the TV launcher wasn't installed (see above). DAKboard is untouched."
else
  echo "Reboot when convenient so the TV starts through the launcher: sudo reboot"
  echo "The TV keeps showing DAKboard until you switch it in Settings."
fi
echo "To undo everything: sudo $DIR/pi/restore.sh"
