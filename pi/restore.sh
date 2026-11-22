#!/usr/bin/env bash
# Put the Pi back exactly as it was before Family Dashboard: DAKboard opens at
# startup the way it used to, and the dashboard server stops.
set -euo pipefail
if [[ $EUID -ne 0 ]]; then echo "Run with sudo: sudo $0" >&2; exit 1; fi
DIR="$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)"
python3 "$DIR/pi/kiosk_setup.py" restore --dir "$DIR"
systemctl disable --now family-dashboard.service 2>/dev/null || true
if [ -f "$DIR/compose.yaml" ] && command -v docker >/dev/null; then (cd "$DIR" && docker compose down 2>/dev/null) || true; fi
echo "Done. Reboot to finish: sudo reboot"
echo "The files in $DIR are left in place; run sudo $DIR/pi/install.sh to turn it back on."
