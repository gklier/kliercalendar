#!/usr/bin/env bash
# Turn a fresh Raspberry Pi OS Lite (Bookworm, 64-bit) into the family TV: a
# full-screen Chromium inside cage (a one-app Wayland kiosk, no desktop) that
# shows the dashboard server elsewhere in the house, or DAKboard, whichever
# Settings → On the TV picks. Run from the project folder copied to the Pi:
#
#   sudo DASHBOARD_SERVER=http://<server>:8080 \
#        DASHBOARD_TV_KEY=<key from Settings → On the TV> \
#        DAKBOARD_URL=https://dakboard.com/display/uuid/... \
#        ./pi/install-display.sh
#
# Optional: DISPLAY_MODE=1920x1080@60 (empty = the TV's own choice, often 4K at 30 Hz).
# When the screen sleeps is set on the server (Settings → Schedule), not here.
# Safe to run again to change any of these.
set -euo pipefail
if [[ $EUID -ne 0 ]]; then echo "Run with sudo: sudo ./pi/install-display.sh" >&2; exit 1; fi

SRC="$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)"
DIR="${DASHBOARD_DIR:-/opt/family-dashboard}"
RUN_USER="${DASHBOARD_USER:-${SUDO_USER:-pi}}"
SERVER_URL="${DASHBOARD_SERVER:-}"
TV_KEY="${DASHBOARD_TV_KEY:-}"
DAK_URL="${DAKBOARD_URL:-}"
DISPLAY_MODE="${DISPLAY_MODE-1920x1080@60}"

[[ -n "$SERVER_URL" && -n "$TV_KEY" ]] || { echo "Set DASHBOARD_SERVER and DASHBOARD_TV_KEY (make the key in Settings → On the TV)." >&2; exit 1; }
id "$RUN_USER" >/dev/null 2>&1 || { echo "No user $RUN_USER. Run with sudo from the Pi's normal user, or set DASHBOARD_USER." >&2; exit 1; }
[[ -z "$DISPLAY_MODE" || "$DISPLAY_MODE" =~ ^[0-9]{3,4}x[0-9]{3,4}@[0-9]{2,3}$ ]] || { echo "DISPLAY_MODE looks like 1920x1080@60, not $DISPLAY_MODE" >&2; exit 1; }
[[ "$RUN_USER" != root ]] || { echo "The TV shouldn't run as root. Run with sudo from the Pi's normal user." >&2; exit 1; }
if [[ -e /usr/bin/startlxde-pi || -e /usr/bin/labwc || -e /usr/bin/wayfire ]]; then
  echo "This Pi has a desktop. This installer is for Raspberry Pi OS Lite; on a desktop use ./pi/install.sh." >&2; exit 1
fi
UID_N="$(id -u "$RUN_USER")"

echo "==> Installing cage, Chromium and helpers"
apt-get update -qq
# Trixie's "chromium-browser" is a stale Bookworm build; "chromium" is the current one
BROWSER=chromium
apt-cache show chromium >/dev/null 2>&1 || BROWSER=chromium-browser
DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
  cage "$BROWSER" curl wlr-randr v4l-utils iw fonts-dejavu-core fonts-noto-color-emoji python3 >/dev/null
BROWSER_BIN="$(command -v chromium || command -v chromium-browser)"

echo "==> Copying the launcher to $DIR"
mkdir -p "$DIR/data"
if [[ "$SRC" != "$DIR" ]]; then rm -rf "$DIR/pi"; cp -r "$SRC/pi" "$DIR/"; fi
chmod +x "$DIR/pi/"*.sh
python3 "$DIR/pi/kiosk_setup.py" display --dir "$DIR" --browser "$BROWSER_BIN" --dakboard-url "$DAK_URL"
python3 "$DIR/pi/kiosk_setup.py" configure --dir "$DIR" --server "$SERVER_URL" --tv-key "$TV_KEY"
sed -i '/^SCREEN_MODE=/d' "$DIR/data/kiosk.env"
[[ -z "$DISPLAY_MODE" ]] || echo "SCREEN_MODE=$DISPLAY_MODE" >> "$DIR/data/kiosk.env"
chown -R "$RUN_USER":"$RUN_USER" "$DIR"
chmod 700 "$DIR/data"

echo "==> Starting the TV at boot (cage on the first screen, no login prompt)"
usermod -aG video,render,input "$RUN_USER"
cat > /etc/pam.d/family-tv <<'EOF'
auth     required pam_unix.so nullok
account  required pam_unix.so
session  required pam_unix.so
session  required pam_systemd.so
EOF
cat > /etc/systemd/system/family-tv.service <<EOF
[Unit]
Description=Family Dashboard TV (Chromium in cage)
After=systemd-user-sessions.service plymouth-quit-wait.service network-online.target
Wants=network-online.target
Conflicts=getty@tty1.service

[Service]
User=$RUN_USER
PAMName=family-tv
TTYPath=/dev/tty1
TTYReset=yes
TTYVHangup=yes
TTYVTDisallocate=yes
StandardInput=tty-fail
StandardOutput=journal
StandardError=journal
UtmpIdentifier=tty1
UtmpMode=user
Environment=XDG_SESSION_TYPE=wayland
ExecStart=/usr/bin/cage -d -s -- $DIR/pi/kiosk.sh
Restart=always
RestartSec=5

[Install]
WantedBy=graphical.target
EOF
systemctl disable getty@tty1.service >/dev/null 2>&1 || true
systemctl set-default graphical.target >/dev/null
systemctl daemon-reload
systemctl enable family-tv.service >/dev/null

echo "==> Wi-Fi (country and power saving)"
# Without a country the Pi 4 limits itself; with power saving on, a Wi-Fi Pi answers slowly and stutters
cur="$(iw reg get 2>/dev/null | awk '/^country/{print $2; exit}' | tr -d ':')"
if [[ -z "$cur" || "$cur" == 00 ]] && command -v raspi-config >/dev/null; then
  raspi-config nonint do_wifi_country "${WIFI_COUNTRY:-US}" >/dev/null 2>&1 || true
  echo "Wi-Fi country set to ${WIFI_COUNTRY:-US}."
fi
if systemctl is-active --quiet NetworkManager 2>/dev/null; then
  mkdir -p /etc/NetworkManager/conf.d
  printf '[connection]\nwifi.powersave = 2\n' > /etc/NetworkManager/conf.d/99-family-tv-wifi.conf
  iw dev wlan0 set power_save off >/dev/null 2>&1 || true
  echo "Wi-Fi power saving off."
fi

echo "==> Screen size"
CMDLINE=/boot/firmware/cmdline.txt
[[ -f "$CMDLINE" ]] || CMDLINE=/boot/cmdline.txt
[[ -f "$CMDLINE.family-tv.bak" ]] || cp -p "$CMDLINE" "$CMDLINE.family-tv.bak"
line="$(head -n1 "$CMDLINE" | sed -E 's/ ?video=HDMI-A-1:[^ ]*//g')"
if [[ -n "$DISPLAY_MODE" ]]; then
  line="$line video=HDMI-A-1:$DISPLAY_MODE"
  echo "HDMI set to $DISPLAY_MODE from the next boot (a Pi 4 animates far more smoothly at 1080p than 4K)."
else
  echo "HDMI left to the TV's own choice."
fi
printf '%s\n' "$line" > "$CMDLINE"

echo "==> Screen sleep"
# Sleep times come from the server now; remove the fixed timers older installs had
systemctl disable --now family-tv-screen-on.timer family-tv-screen-off.timer >/dev/null 2>&1 || true
rm -f /etc/systemd/system/family-tv-screen-on.service /etc/systemd/system/family-tv-screen-on.timer \
      /etc/systemd/system/family-tv-screen-off.service /etc/systemd/system/family-tv-screen-off.timer
systemctl daemon-reload
echo "The screen follows Settings → Schedule on the dashboard server."

echo "==> Checking that $SERVER_URL knows this TV"
if answer="$(curl -fsS --max-time 10 -H "Authorization: Bearer $TV_KEY" "$SERVER_URL/api/screen-url")"; then
  echo "It answered: the TV will show $(printf '%s\n' "$answer" | head -n1)"
else
  echo "The server didn't accept this TV yet (wrong key, or not reachable). The TV shows DAKboard until it does." >&2
fi
echo
echo "Done. Reboot to start the TV: sudo reboot"
echo "Logs: journalctl -u family-tv -b    Restart the TV: sudo systemctl restart family-tv"
