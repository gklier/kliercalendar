#!/usr/bin/env bash
# Turn the TV off at night and on in the morning (Raspberry Pi OS Lite, cage).
# Uses both: the Pi's HDMI output (wlr-randr, through cage's display) and HDMI-CEC
# standby/wake for TVs that listen to it. Either may be missing; the other still works.
#   screen.sh on|off|mode|state   (mode: just apply SCREEN_MODE, e.g. 1920x1080@60; state: print on or off)
set -u
what="${1:-}"
[[ "$what" == on || "$what" == off || "$what" == mode || "$what" == state ]] || { echo "Usage: $0 on|off|mode|state" >&2; exit 2; }
DIR="$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)"
SCREEN_MODE="$(sed -n "s/^SCREEN_MODE=//p" "$DIR/data/kiosk.env" 2>/dev/null | tr -d "'\"")"

export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
if [[ -z "${WAYLAND_DISPLAY:-}" ]]; then
  for s in "$XDG_RUNTIME_DIR"/wayland-*; do
    [[ -S "$s" ]] && { WAYLAND_DISPLAY="$(basename "$s")"; break; }
  done
fi
export WAYLAND_DISPLAY="${WAYLAND_DISPLAY:-wayland-0}"

if [[ "$what" == state ]]; then
  [[ "$(wlr-randr 2>/dev/null | awk '/Enabled:/ {print $2; exit}')" == yes ]] && echo on || echo off
  exit 0
fi

out="$(wlr-randr 2>/dev/null | awk 'NR==1 {print $1}')"
if [[ -z "$out" ]]; then
  echo "Couldn't reach the TV's display (is family-tv running?)" >&2
elif [[ "$what" == off ]]; then
  wlr-randr --output "$out" --off && echo "HDMI output $out turned off"
else
  # Coming back on, the TV may offer its own favourite (often 4K at 30 Hz): ask for ours
  args=(--output "$out" --on)
  [[ -n "$SCREEN_MODE" ]] && args+=(--mode "${SCREEN_MODE}Hz")
  wlr-randr "${args[@]}" && echo "HDMI output $out on${SCREEN_MODE:+ at $SCREEN_MODE}"
fi
[[ "$what" == mode ]] && exit 0   # setting the mode never wakes or sleeps the TV

if [[ -e /dev/cec0 ]]; then
  cec-ctl -d /dev/cec0 --playback >/dev/null 2>&1
  if [[ "$what" == on ]]; then
    cec-ctl -d /dev/cec0 --to 0 --image-view-on >/dev/null 2>&1 && echo "Asked the TV to wake (CEC)"
  else
    cec-ctl -d /dev/cec0 --to 0 --standby >/dev/null 2>&1 && echo "Asked the TV to sleep (CEC)"
  fi
fi
exit 0
