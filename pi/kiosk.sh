#!/usr/bin/env bash
# Family Dashboard TV launcher. The desktop's autostart runs this in place of
# the original DAKboard browser line. It asks the dashboard server which page
# to show, opens the browser there, and checks every few seconds: when the
# answer changes (switched in Settings, or "Reload the TV"), it reopens the
# browser. If the server never answers, the TV shows DAKboard as before.
# SERVER is the dashboard server: this Pi, unless it runs off-site, where
# TV_KEY proves to it that this is the family TV. The server's answer also says
# whether the screen should be asleep (Settings → Schedule); this script switches
# the HDMI output and the TV to match.
DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]:-$0}")")/.." && pwd)"
# shellcheck source=/dev/null
source "$DIR/data/kiosk.env"      # CMD=(browser args with __URL__), DAKBOARD_URL, PORT[, SERVER, TV_KEY]
SERVER="${SERVER:-http://localhost:${PORT:-8080}}"
SERVER="${SERVER%/}"

ask() {
  if [ -n "${TV_KEY:-}" ]; then
    curl -fsS --max-time 8 -H "Authorization: Bearer $TV_KEY" "$SERVER/api/screen-url" 2>/dev/null
  else
    curl -fsS --max-time 3 "$SERVER/api/screen-url" 2>/dev/null
  fi
}

# The server answers a full link (DAKboard) or a path on itself (/?mode=live).
# Off-site, its pages open through /tv, which trades the key for a cookie; the
# key rides after "#", so it never leaves this Pi.
page() {
  case "$1" in
    /*)
      if [ -n "${TV_KEY:-}" ]; then
        printf '%s/tv#k=%s&next=%s' "$SERVER" "$TV_KEY" "${1//&/%26}"
      else
        printf '%s%s' "$SERVER" "$1"
      fi ;;
    *) printf '%s' "$1" ;;
  esac
}

# The answer is three lines: the page, a counter "Reload the TV" bumps, and asleep|awake
page_of() { printf '%s\n' "$1" | head -n2; }

# Make the screen match the schedule. Runs when the answer changes and every 30 s, so a stale
# timer or a stray wake can't leave the screen in the wrong state for long. Only under cage.
last_want=""; last_check=-100
sync_screen() {
  local want cur
  want=$(printf '%s\n' "$1" | sed -n '3p')
  { [ "$want" = asleep ] || [ "$want" = awake ]; } && [ -n "${WAYLAND_DISPLAY:-}" ] || return 0
  if [ "$want" != "$last_want" ] || (( SECONDS - last_check >= 30 )); then
    cur=$("$DIR/pi/screen.sh" state 2>/dev/null)
    if [ "$want" = asleep ] && [ "$cur" != off ]; then "$DIR/pi/screen.sh" off >/dev/null 2>&1 || true; fi
    if [ "$want" = awake ] && [ "$cur" != on ]; then "$DIR/pi/screen.sh" on >/dev/null 2>&1 || true; fi
    last_want=$want; last_check=$SECONDS
  fi
}

# Tests source this file for the functions above and stop here
if [ "${KIOSK_LIB_ONLY:-}" = 1 ]; then return 0 2>/dev/null || exit 0; fi

if [ "${1:-}" = "--print-url" ]; then   # for tests and troubleshooting: what would the TV open?
  answer=$(ask) || answer=""
  url=$(printf '%s\n' "$answer" | head -n1)
  if [ -n "$url" ]; then page "$url"; else printf '%s' "$DAKBOARD_URL"; fi
  echo
  exit 0
fi

# Under cage (Raspberry Pi OS Lite) the launcher also picks the screen mode
if [ -n "${SCREEN_MODE:-}" ] && [ -n "${WAYLAND_DISPLAY:-}" ]; then "$DIR/pi/screen.sh" mode || true; fi

answer=""
for ((i = 0; i < ${KIOSK_FIRST_TRIES:-45}; i++)); do   # first boot: give the server time to start
  answer=$(ask) && [ -n "$answer" ] && break
  sleep 1
done
sync_screen "$answer"

while true; do
  url=$(printf '%s\n' "$answer" | head -n1)
  if [ -n "$url" ]; then url=$(page "$url"); else url="$DAKBOARD_URL"; fi
  # Stop Chromium offering to restore pages after a restart
  prefs="$HOME/.config/chromium/Default/Preferences"
  [ -f "$prefs" ] && sed -i 's/"exited_cleanly":false/"exited_cleanly":true/; s/"exit_type":"[^"]*"/"exit_type":"Normal"/' "$prefs"
  args=()
  # Quoted replacement: bash 5.2 would otherwise treat "&" in the link as the matched text
  for t in "${CMD[@]}"; do args+=("${t//__URL__/"$url"}"); done
  setsid "${args[@]}" &      # own process group, so all browser helpers stop together
  pid=$!

  while kill -0 "$pid" 2>/dev/null; do
    sleep "${KIOSK_POLL:-5}"
    new=$(ask) || continue   # server busy or restarting: keep showing what's there
    sync_screen "$new"
    if [ -n "$new" ] && [ "$(page_of "$new")" != "$(page_of "$answer")" ]; then
      answer="$new"
      kill -TERM -- "-$pid" 2>/dev/null
      wait "$pid" 2>/dev/null
      break
    fi
  done
  sleep 2
done
