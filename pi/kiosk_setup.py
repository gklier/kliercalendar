#!/usr/bin/env python3
"""Writes the TV launcher's settings (data/kiosk.env) on a Raspberry Pi OS Lite TV.

  kiosk_setup.py display --dir /opt/family-dashboard --browser chromium-browser
      (the browser command for Chromium under cage, and the local port)
  kiosk_setup.py configure --dir /opt/family-dashboard --server https://dash.example.com --tv-key KEY
      (which dashboard server the TV asks, and the TV key that proves it is the family TV)
  kiosk_setup.py configure --dir /opt/family-dashboard --server ""      (the server on this Pi)

install-display.sh runs both. Running them again changes only what you pass.
"""

from __future__ import annotations

import argparse
import os
import re
import shlex
import sys
from pathlib import Path

# Chromium as a full-screen Wayland app under cage (Raspberry Pi OS Lite)
DISPLAY_FLAGS = ["--ozone-platform=wayland", "--kiosk", "--noerrdialogs", "--disable-infobars",
                 "--disable-session-crashed-bubble", "--incognito", "--no-first-run", "--password-store=basic",
                 "--check-for-update-interval=31536000", "--autoplay-policy=no-user-gesture-required",
                 "--enable-gpu-rasterization", "--ignore-gpu-blocklist"]


def display(base: Path, browser: str, port: int = 8080) -> int:
    """Launcher settings for a display-only Pi with no desktop. The server answers
    which page to show; if it never answers, the launcher shows a waiting page."""
    if not re.match(r"^[A-Za-z0-9_./-]+$", browser):
        print(f"Unexpected browser command: {browser!r}", file=sys.stderr)
        return 2
    data = base / "data"
    data.mkdir(parents=True, exist_ok=True)
    env = data / "kiosk.env"
    keep = [l for l in env.read_text(encoding="utf-8").splitlines() if l.startswith(("SERVER=", "TV_KEY="))] if env.exists() else []
    tokens = [browser, *DISPLAY_FLAGS, "__URL__"]
    env.write_text(
        f"CMD=({' '.join(shlex.quote(t) for t in tokens)})\n"
        f"PORT={int(port)}\n" + "".join(l + "\n" for l in keep)
    , encoding="utf-8")
    os.chmod(env, 0o600)
    print(f"Launcher settings written to {env}.")
    return 0


def configure(base: Path, server: str, tv_key: str) -> int:
    """Point the launcher at an off-site dashboard server (or back at this Pi)."""
    env = base / "data" / "kiosk.env"
    if not env.exists():
        print("The launcher isn't installed yet. Run install first.", file=sys.stderr)
        return 2
    server = server.strip().rstrip("/")
    if server and not re.match(r"^https?://[^\s/'\"]+(:\d+)?(/[^\s'\"]*)?$", server):
        print(f"The server must be a web address like https://dash.example.com, not {server!r}", file=sys.stderr)
        return 2
    if server.startswith("http://") and not re.match(r"^http://(localhost|127\.|10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.)", server):
        print("Warning: an off-site server should use https://, or the TV key travels in the clear.", file=sys.stderr)
    if server and not tv_key:
        print("An off-site server needs the TV key from Settings → On the TV.", file=sys.stderr)
        return 2
    if tv_key and not re.match(r"^[A-Za-z0-9_-]{16,100}$", tv_key):
        print("That TV key doesn't look right. Copy it again from Settings → On the TV.", file=sys.stderr)
        return 2
    lines = [l for l in env.read_text(encoding="utf-8").splitlines() if not l.startswith(("SERVER=", "TV_KEY="))]
    if server:
        lines += [f"SERVER={shlex.quote(server)}", f"TV_KEY={shlex.quote(tv_key)}"]
    tmp = env.with_name(env.name + ".tmp")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.chmod(tmp, 0o600)
    st = env.stat()
    if os.geteuid() == 0:
        os.chown(tmp, st.st_uid, st.st_gid)
    os.replace(tmp, env)
    print(f"The TV now asks {server or 'the server on this Pi'} what to show.")
    return 0


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):   # a Pi's sudo shell may not be UTF-8: never crash on a "→"
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=["configure", "display"])
    ap.add_argument("--dir", default="/opt/family-dashboard")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--server", default="", help="configure: dashboard server address")
    ap.add_argument("--tv-key", default="", help="configure: the TV key from Settings")
    ap.add_argument("--browser", default="chromium-browser", help="display: browser command")
    a = ap.parse_args(argv)
    base = Path(a.dir)
    if a.action == "configure":
        return configure(base, a.server, a.tv_key)
    return display(base, a.browser, a.port)


if __name__ == "__main__":
    sys.exit(main())
