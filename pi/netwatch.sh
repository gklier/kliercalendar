#!/usr/bin/env bash
# Keeps the TV's own network connection alive. Run by the family-tv-net timer every minute, as root.
#
# It never looks at the internet, so an internet outage does nothing: the TV only needs the home
# network and the dashboard server. It checks two things.
#
# 1. The router (default gateway). When the router loses power the Pi's Wi-Fi can give up:
#      3 misses in a row (3 minutes)   wake the Wi-Fi and ask for the saved connections again
#      8 misses                        restart the network service
#      20 misses                       restart the Pi, at most once an hour
#
# 2. The dashboard server, when the router answers. The Pi can be joined to a Wi-Fi network and still
#    not reach the server (a flaky link, a network that lost its route). Then, repeating every 30 minutes:
#      5 misses     join the current Wi-Fi again
#      10 misses    try the other saved Wi-Fi networks, best first
#      20 misses    restart the network service, which goes back to the preferred network
#    It never restarts the Pi for this: if the server itself is down, a restart can't help.
#    Any answer from the server counts, even "not allowed": that still proves the way there works.
#
# Environment overrides exist for the tests only.
set -u
STATE="${NETWATCH_STATE:-/run/family-tv-net}"
SSTATE="${NETWATCH_SERVER_STATE:-/run/family-tv-net-server}"
STAMP="${NETWATCH_STAMP:-/var/lib/family-tv-net-reboot}"
ENVFILE="${NETWATCH_ENV:-$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)/data/kiosk.env}"
mkdir -p "$(dirname "$STATE")" "$(dirname "$SSTATE")" "$(dirname "$STAMP")" 2>/dev/null || true

note() { logger -t family-tv-net "$*" 2>/dev/null || true; }
count() { local n=$(( $(cat "$1" 2>/dev/null || echo 0) + 1 )); echo "$n" > "$1"; echo "$n"; }
wifi_profiles() {   # saved Wi-Fi connections, preferred first
  nmcli -t -f AUTOCONNECT-PRIORITY,NAME,TYPE connection show 2>/dev/null \
    | awk -F: '$3 ~ /wireless/ {print $1 ":" $2}' | sort -t: -k1,1nr | cut -d: -f2-
}
active_wifi() { nmcli -t -f NAME,TYPE connection show --active 2>/dev/null | awk -F: '$2 ~ /wireless/ {print $1; exit}'; }

# ---- 1. the router
gw="$(ip route 2>/dev/null | awk '/^default/ {print $3; exit}')"
if [ -z "$gw" ] || ! ping -c2 -W2 "$gw" >/dev/null 2>&1; then
  rm -f "$SSTATE"                       # the server can't be judged without a router
  n=$(count "$STATE")
  note "Can't reach the router (miss $n)"
  if [ "$n" -eq 3 ]; then
    rfkill unblock wifi 2>/dev/null || true
    nmcli radio wifi on 2>/dev/null || true
    # NetworkManager stops trying a connection by itself after a few failures: ask again
    wifi_profiles | while read -r name; do nmcli connection up "$name" >/dev/null 2>&1 && break; done
  elif [ "$n" -eq 8 ]; then
    systemctl restart NetworkManager 2>/dev/null || systemctl restart dhcpcd 2>/dev/null || systemctl restart systemd-networkd 2>/dev/null || true
  elif [ "$n" -ge 20 ]; then
    now="$(date +%s)"
    last="$(cat "$STAMP" 2>/dev/null || echo 0)"
    if [ $(( now - last )) -ge 3600 ]; then
      echo "$now" > "$STAMP"
      note "Router unreachable for $n minutes: restarting"
      systemctl reboot 2>/dev/null || reboot
    fi
  fi
  exit 0
fi
rm -f "$STATE"

# ---- 2. the dashboard server
SERVER="$(sed -n 's/^SERVER=//p' "$ENVFILE" 2>/dev/null | head -n1 | tr -d "'\"")"
case "$SERVER" in ""|*://localhost*|*://127.0.0.1*) rm -f "$SSTATE"; exit 0 ;; esac   # the server is on this Pi
code="$(curl -s -o /dev/null --max-time 8 -w '%{http_code}' "${SERVER%/}/api/screen-url" 2>/dev/null)"
if [ -n "$code" ] && [ "$code" != 000 ]; then
  rm -f "$SSTATE"
  exit 0
fi
n=$(count "$SSTATE")
note "Router answers but the dashboard server doesn't (miss $n)"
step=$(( n % 30 ))
if [ "$step" -eq 5 ]; then
  cur="$(active_wifi)"
  [ -n "$cur" ] && nmcli connection up "$cur" >/dev/null 2>&1 || true
elif [ "$step" -eq 10 ]; then
  cur="$(active_wifi)"
  wifi_profiles | while read -r name; do
    [ "$name" = "$cur" ] && continue
    note "Trying the Wi-Fi network $name"
    nmcli connection up "$name" >/dev/null 2>&1 && break
  done
elif [ "$step" -eq 20 ]; then
  systemctl restart NetworkManager 2>/dev/null || true
fi
exit 0
