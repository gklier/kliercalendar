#!/usr/bin/env python3
"""Find the line that opens DAKboard when the Pi's desktop starts, and swap it
for the Family Dashboard launcher. The original file is backed up first, and
`restore` puts it back byte for byte.

  kiosk_setup.py install --dir /opt/family-dashboard --port 8080 [--home /home/pi]
  kiosk_setup.py restore --dir /opt/family-dashboard
  kiosk_setup.py configure --dir /opt/family-dashboard --server https://dash.example.com --tv-key KEY
  kiosk_setup.py configure --dir /opt/family-dashboard --server ""      (back to the server on this Pi)
  kiosk_setup.py display --dir /opt/family-dashboard --browser chromium-browser --dakboard-url URL
      (a fresh Raspberry Pi OS Lite: write the launcher's settings for Chromium under cage)

Nothing is changed unless exactly one DAKboard browser line is found.

DAKboard OS images never put the DAKboard link on a browser line: the desktop
autostart runs ~/startup/run.sh, which runs ~/startup/browser.sh, which opens
Chromium on a local loader page that redirects to the screen link kept in
/var/www/config.json. On those images the line in run.sh that starts
browser.sh is the one swapped; the browser command is captured by running
browser.sh with a stand-in browser, and the loader page stays the fallback.
DAKboard OS also starts browser.sh from dakos-browser.service at boot; a
systemd drop-in empties that service's ExecStart (its DAKboard check-in still
runs) so only the launcher opens the browser. restore removes the drop-in.
"""

from __future__ import annotations

import argparse
import contextlib
import glob
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

BROWSER = re.compile(r"chrom(e|ium)", re.I)
URL = re.compile(r"https?://[^\s'\"]*dakboard[^\s'\"]*", re.I)


def candidate_files(homes: list[Path], etc: Path = Path("/etc/xdg")) -> list[Path]:
    pats = []
    for h in homes:
        c = h / ".config"
        pats += [c / "labwc/autostart", c / "wayfire.ini", c / "lxsession/*/autostart",
                 c / "autostart/*.desktop", h / "*.sh", h / ".xinitrc", h / ".bash_profile", h / ".profile"]
    pats += [etc / "lxsession/*/autostart", etc / "labwc/autostart", etc / "autostart/*.desktop"]
    seen, out = set(), []
    for p in pats:
        for f in sorted(glob.glob(str(p))):
            if f not in seen and os.path.isfile(f):
                seen.add(f)
                out.append(Path(f))
    return out


def find_matches(files: list[Path]) -> list[tuple[Path, int, str]]:
    hits = []
    for f in files:
        try:
            lines = f.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            continue
        for i, line in enumerate(lines):
            s = line.strip()
            if s and not s.startswith("#") and BROWSER.search(s) and URL.search(s):
                hits.append((f, i, line))
    return hits


def find_dakos(homes: list[Path]) -> list[tuple[Path, int, str]]:
    """DAKboard OS: the line in ~/startup/run.sh that starts ~/startup/browser.sh."""
    hits = []
    for h in homes:
        run, browser = h / "startup/run.sh", h / "startup/browser.sh"
        try:
            if not (run.is_file() and browser.is_file() and BROWSER.search(browser.read_text(encoding="utf-8"))):
                continue
            lines = run.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            continue
        for i, line in enumerate(lines):
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            try:
                tokens = shlex.split(s.rstrip("&").strip())
            except ValueError:
                continue
            if tokens and tokens[0] in ("exec", "bash", "sh"):
                tokens = tokens[1:]
            if tokens and (tokens[0] == str(browser) or tokens[0].endswith("startup/browser.sh")):
                hits.append((run, i, line))
    return hits


def capture_browser(script: Path, timeout: int = 20) -> tuple[list[str], str]:
    """Run a browser start script with a stand-in browser that records its
    arguments, and return them with the page it opens replaced by __URL__."""
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / "argv"
        fake = f'#!/bin/sh\nfor a in "$0" "$@"; do printf \'%s\\0\' "$a"; done > {shlex.quote(str(out))}\n'
        for name in ("chromium-browser", "chromium", "google-chrome", "chrome"):
            f = Path(d) / name
            f.write_text(fake, encoding="utf-8")
            f.chmod(0o755)
        env = dict(os.environ, PATH=f"{d}:{os.environ.get('PATH', '/usr/bin:/bin')}")
        try:
            subprocess.run(["bash", str(script)], env=env, cwd=str(script.parent), timeout=timeout,
                           stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except subprocess.TimeoutExpired:
            pass
        if not out.exists():
            raise ValueError(f"{script} did not start a browser")
        tokens = out.read_bytes().decode().split("\0")[:-1]
    tokens[0] = Path(tokens[0]).name
    idx = None
    for i, t in enumerate(tokens[1:-1], 1):
        if t in ("--kiosk", "--app") and not tokens[i + 1].startswith("-"):
            idx = i + 1
    if idx is None:
        rest = [i for i, t in enumerate(tokens[1:], 1) if not t.startswith("-")]
        if not rest:
            raise ValueError(f"No page in the browser command from {script}")
        idx = rest[-1]
    url = tokens[idx]
    tokens[idx] = "__URL__"
    return tokens, url


def dakos_screen_url(www: Path) -> str | None:
    """The DAKboard screen link DAKboard OS keeps in /var/www/config.json."""
    try:
        url = json.loads((www / "config.json").read_text(encoding="utf-8")).get("private_url") or ""
    except (OSError, ValueError, AttributeError):
        return None
    return url if re.match(r"https?://", url) and "localhost" not in url else None


DAKOS_UNIT = "dakos-browser.service"
DROPIN = """# Added by Family Dashboard: the TV launcher (pi/kiosk.sh) opens the browser now.
# The DAKboard check-in (ExecStartPre) still runs. Removed by restore.sh.
[Service]
ExecStart=
ExecStart=/bin/true
"""


def systemd_reload(unit_dir: Path) -> None:
    if os.geteuid() == 0 and str(unit_dir).startswith("/etc/systemd") and shutil.which("systemctl"):
        subprocess.run(["systemctl", "daemon-reload"], check=False)


def split_line(path: Path, line: str) -> tuple[str, str, str]:
    """Return (prefix, command, suffix) in the syntax of the file it came from."""
    s = line.strip()
    if path.suffix == ".desktop":
        if not s.startswith("Exec="):
            raise ValueError(f"Unexpected line in {path}: {line}")
        return "Exec=", s[5:], ""
    if path.name == "wayfire.ini":
        key, sep, cmd = s.partition("=")
        if not sep:
            raise ValueError(f"Unexpected line in {path}: {line}")
        return f"{key.strip()} = ", cmd.strip(), ""
    if path.parent.parent.name == "lxsession" and s.startswith("@"):
        return "@", s[1:], ""
    # Shell syntax (labwc autostart, scripts, .xinitrc)
    background = s.endswith("&")
    cmd = s[:-1].strip() if background else s
    if cmd.startswith("exec "):
        cmd = cmd[5:]
    return ("", cmd, " &") if background else ("exec ", cmd, "")


def template(cmd: str) -> tuple[list[str], str]:
    """Split the browser command, replacing the DAKboard link with __URL__."""
    tokens = shlex.split(cmd)
    url = None
    for i, t in enumerate(tokens):
        m = URL.search(t)
        if m:
            url = url or m.group(0)
            tokens[i] = t.replace(m.group(0), "__URL__")
    if not url:
        raise ValueError("No DAKboard link in the browser command")
    return tokens, url


def install(base: Path, port: int, homes: list[Path], www: Path = Path("/var/www"),
            unit_dir: Path = Path("/etc/systemd/system")) -> int:
    data = base / "data"
    manifest = data / "kiosk-backup.json"
    if manifest.exists():
        print("The launcher is already installed. Run restore first to reinstall it.")
        return 0
    matches = find_matches(candidate_files(homes))
    dakos = [] if matches else find_dakos(homes)
    matches = matches or dakos
    if len(matches) != 1:
        print("Could not find exactly one line that opens DAKboard in a browser at startup.", file=sys.stderr)
        for f, i, line in matches:
            print(f"  {f}:{i + 1}: {line.strip()}", file=sys.stderr)
        if not matches:
            print("  (none found in the usual autostart files)", file=sys.stderr)
        print("Nothing was changed. The dashboard server is still installed and reachable.", file=sys.stderr)
        return 2

    path, idx, line = matches[0]
    prefix, cmd, suffix = split_line(path, line)
    if dakos:
        tokens, fallback = capture_browser(path.parent / "browser.sh")
        url = dakos_screen_url(www) or fallback
    else:
        tokens, url = template(cmd)
        fallback = url

    backup_dir = data / "backup"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup = backup_dir / f"{int(time.time())}-{path.name}"
    shutil.copy2(path, backup)
    dropin = unit_dir / f"{DAKOS_UNIT}.d" / "family-dashboard.conf" if dakos and (unit_dir / DAKOS_UNIT).exists() else None
    # Written before anything changes, so restore can always undo a half-finished install
    manifest.write_text(json.dumps({"file": str(path), "backup": str(backup), "line": line, "url": url,
                                    "fallback": fallback, "dropin": str(dropin) if dropin else None}, indent=2), encoding="utf-8")

    launcher = base / "pi" / "kiosk.sh"
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    ending = "\n" if lines[idx].endswith("\n") else ""
    lines[idx] = f"{prefix}{launcher}{suffix}{ending}"
    tmp = path.with_name(path.name + ".fd-tmp")
    tmp.write_text("".join(lines), encoding="utf-8")
    shutil.copymode(path, tmp)
    st = path.stat()
    if os.geteuid() == 0:
        os.chown(tmp, st.st_uid, st.st_gid)   # keep the file's owner (DAKboard OS updates its own scripts)
    os.replace(tmp, path)

    if dropin:
        dropin.parent.mkdir(parents=True, exist_ok=True)
        dropin.write_text(DROPIN, encoding="utf-8")
        systemd_reload(unit_dir)
        print(f"Stopped {DAKOS_UNIT} opening a second browser at boot ({dropin}).")

    (data / "kiosk.env").write_text(
        f"CMD=({' '.join(shlex.quote(t) for t in tokens)})\n"
        f"DAKBOARD_URL={shlex.quote(fallback)}\n"
        f"PORT={int(port)}\n"
    , encoding="utf-8")
    print(f"Launcher installed in {path} (backup: {backup}).")
    print(f"DAKBOARD_URL={url}")
    return 0


def restore(base: Path) -> int:
    manifest = base / "data" / "kiosk-backup.json"
    if not manifest.exists():
        print("Nothing to restore: the launcher was never installed.")
        return 0
    m = json.loads(manifest.read_text(encoding="utf-8"))
    shutil.copy2(m["backup"], m["file"])
    if m.get("dropin"):
        dropin = Path(m["dropin"])
        dropin.unlink(missing_ok=True)
        with contextlib.suppress(OSError):
            dropin.parent.rmdir()
        systemd_reload(dropin.parent.parent)
        print(f"Removed {dropin}.")
    manifest.rename(manifest.with_suffix(".restored.json"))
    print(f"Restored {m['file']} from {m['backup']}.")
    return 0


# Chromium as a full-screen Wayland app under cage (Raspberry Pi OS Lite)
DISPLAY_FLAGS = ["--ozone-platform=wayland", "--kiosk", "--noerrdialogs", "--disable-infobars",
                 "--disable-session-crashed-bubble", "--incognito", "--no-first-run", "--password-store=basic",
                 "--check-for-update-interval=31536000", "--autoplay-policy=no-user-gesture-required",
                 "--enable-gpu-rasterization", "--ignore-gpu-blocklist"]
NO_SERVER_PAGE = ("data:text/html,<body style='background:%230B1626;color:%23aaa;font:600 32px sans-serif;"
                  "display:grid;place-items:center;height:100vh;margin:0'>Waiting for the family dashboard server...")


def display(base: Path, browser: str, dakboard_url: str, port: int = 8080) -> int:
    """Launcher settings for a display-only Pi with no desktop. The server answers
    which page to show; if it never answers, DAKboard (or a waiting page)."""
    if not re.match(r"^[A-Za-z0-9_./-]+$", browser):
        print(f"Unexpected browser command: {browser!r}", file=sys.stderr)
        return 2
    if dakboard_url and not re.match(r"^https?://[^\s'\"]+$", dakboard_url):
        print("The DAKboard link must start with https://", file=sys.stderr)
        return 2
    data = base / "data"
    data.mkdir(parents=True, exist_ok=True)
    env = data / "kiosk.env"
    keep = [l for l in env.read_text(encoding="utf-8").splitlines() if l.startswith(("SERVER=", "TV_KEY="))] if env.exists() else []
    tokens = [browser, *DISPLAY_FLAGS, "__URL__"]
    env.write_text(
        f"CMD=({' '.join(shlex.quote(t) for t in tokens)})\n"
        f"DAKBOARD_URL={shlex.quote(dakboard_url or NO_SERVER_PAGE)}\n"
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
    ap.add_argument("action", choices=["install", "restore", "find", "configure", "display"])
    ap.add_argument("--dir", default="/opt/family-dashboard")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--home", action="append", help="home folder(s) to search; default all of /home")
    ap.add_argument("--www", default="/var/www", help="DAKboard OS settings folder")
    ap.add_argument("--server", default="", help="configure: off-site dashboard server address")
    ap.add_argument("--tv-key", default="", help="configure: the TV key from Settings")
    ap.add_argument("--browser", default="chromium-browser", help="display: browser command")
    ap.add_argument("--dakboard-url", default="", help="display: DAKboard link to fall back to")
    a = ap.parse_args(argv)
    base = Path(a.dir)
    homes = [Path(h) for h in a.home] if a.home else [Path(p) for p in sorted(glob.glob("/home/*"))] + [Path("/root")]
    www = Path(a.www)
    if a.action == "configure":
        return configure(base, a.server, a.tv_key)
    if a.action == "display":
        return display(base, a.browser, a.dakboard_url, a.port)
    if a.action == "find":
        matches = find_matches(candidate_files(homes))
        dakos = [] if matches else find_dakos(homes)
        for f, i, line in matches or dakos:
            print(f"{f}:{i + 1}: {line.strip()}")
        if len(dakos) == 1:
            tokens, fallback = capture_browser(dakos[0][0].parent / "browser.sh")
            print(f"  DAKboard OS browser: {' '.join(shlex.quote(t) for t in tokens)}")
            print(f"  fallback page: {fallback}")
            print(f"  DAKboard link: {dakos_screen_url(www) or '(not readable; run with sudo)'}")
            if Path("/etc/systemd/system", DAKOS_UNIT).exists():
                print(f"  {DAKOS_UNIT} also runs browser.sh at boot; install empties its ExecStart with a drop-in")
        return 0
    return install(base, a.port, homes, www) if a.action == "install" else restore(base)


if __name__ == "__main__":
    sys.exit(main())
