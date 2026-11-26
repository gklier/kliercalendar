#!/usr/bin/env bash
# Keeps the TV's own network connection alive. Run by the family-tv-net timer every minute, as root.
#
# It looks only at the router (the default gateway), never at the internet, so an internet outage does
# nothing: the TV keeps showing the dashboard server over the home network. It acts only when the Pi
# can't reach its router, which is what happens when the router loses power and the Pi's Wi-Fi gives up:
#
#   3 misses in a row (3 minutes)   wake the Wi-Fi and ask for the saved connection again
#   8 misses (8 minutes)            restart the network service
#   20 misses (20 minutes)          restart the Pi, at most once an hour
#
# Environment overrides exist for the tests only.
set -u
STATE="${NETWATCH_STATE:-/run/family-tv-net}"
STAMP="${NETWATCH_STAMP:-/var/lib/family-tv-net-reboot}"
mkdir -p "$(dirname "$STATE")" "$(dirname "$STAMP")" 2>/dev/null || true

gw="$(ip route 2>/dev/null | awk '/^default/ {print $3; exit}')"
if [ -n "$gw" ] && ping -c2 -W2 "$gw" >/dev/null 2>&1; then
  rm -f "$STATE"
  exit 0
fi

n=$(( $(cat "$STATE" 2>/dev/null || echo 0) + 1 ))
echo "$n" > "$STATE"
logger -t family-tv-net "Can't reach the router (miss $n)" 2>/dev/null || true

if [ "$n" -eq 3 ]; then
  rfkill unblock wifi 2>/dev/null || true
  nmcli radio wifi on 2>/dev/null || true
  # Ask for each saved Wi-Fi connection again; NetworkManager stops trying by itself after a few failures
  nmcli -t -f NAME,TYPE connection show 2>/dev/null | awk -F: '$2 ~ /wireless/ {print $1}' | while read -r name; do
    nmcli connection up "$name" >/dev/null 2>&1 || true
  done
elif [ "$n" -eq 8 ]; then
  systemctl restart NetworkManager 2>/dev/null || systemctl restart dhcpcd 2>/dev/null || systemctl restart systemd-networkd 2>/dev/null || true
elif [ "$n" -ge 20 ]; then
  now="$(date +%s)"
  last="$(cat "$STAMP" 2>/dev/null || echo 0)"
  if [ $(( now - last )) -ge 3600 ]; then
    echo "$now" > "$STAMP"
    logger -t family-tv-net "Router unreachable for $n minutes: restarting" 2>/dev/null || true
    systemctl reboot 2>/dev/null || reboot
  fi
fi
exit 0
