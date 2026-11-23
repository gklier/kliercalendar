"""Tests for the TV launcher and its settings writer.  Run: python3 -m unittest -v test_kiosk_setup.py"""

import http.server
import shlex
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path

import kiosk_setup as ks


class KioskSetupTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.base = self.root / "opt"
        (self.base / "pi").mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def env(self):
        # Evaluate kiosk.env the way kiosk.sh does
        return subprocess.run(["bash", "-c", f'source {shlex.quote(str(self.base / "data/kiosk.env"))}; url=X; '
                               'for t in "${CMD[@]}"; do printf "%s\\n" "${t//__URL__/$url}"; done'],
                              capture_output=True, text=True, check=True).stdout.splitlines()

    def test_configure_and_launcher_urls(self):
        self.assertEqual(ks.display(self.base, "chromium-browser"), 0)
        (self.base / "pi/kiosk.sh").write_bytes((Path(ks.__file__).parent / "kiosk.sh").read_bytes())
        answer = {"text": "/?mode=live\n0\n"}
        seen = {}

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a): pass
            def do_GET(self):
                seen["auth"] = self.headers.get("Authorization")
                body = answer["text"].encode()
                self.send_response(200); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

        srv = http.server.HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        server = f"http://127.0.0.1:{srv.server_port}"
        url = lambda: subprocess.run(["bash", str(self.base / "pi/kiosk.sh"), "--print-url"], capture_output=True, text=True, check=True).stdout.strip()
        try:
            self.assertEqual(ks.configure(self.base, server, "short"), 2)               # bad key
            self.assertEqual(ks.configure(self.base, "ftp://x", "k" * 32), 2)           # bad address
            self.assertEqual(ks.configure(self.base, server + "/", "k" * 32), 0)
            self.assertEqual(url(), f"{server}/tv#k={'k' * 32}&next=/?mode=live")
            self.assertEqual(seen["auth"], f"Bearer {'k' * 32}")
            # Back to the server on the Pi: no key, the server's own address
            self.assertEqual(ks.configure(self.base, "", ""), 0)
            self.assertNotIn("TV_KEY", (self.base / "data/kiosk.env").read_text())
        finally:
            srv.shutdown()

    def test_waiting_page_when_the_server_is_silent(self):
        self.assertEqual(ks.display(self.base, "chromium-browser", 1), 0)           # nothing listens on port 1
        (self.base / "pi/kiosk.sh").write_bytes((Path(ks.__file__).parent / "kiosk.sh").read_bytes())
        out = subprocess.run(["bash", str(self.base / "pi/kiosk.sh"), "--print-url"], capture_output=True, text=True, check=True).stdout
        self.assertIn("Waiting for the family dashboard server", out)

    def test_display_only_lite(self):
        """Raspberry Pi OS Lite: the launcher runs under cage."""
        self.assertEqual(ks.display(self.base, "chromium-browser"), 0)
        self.assertEqual(ks.configure(self.base, "http://dashboard.example:8080", "k" * 32), 0)
        cmd = self.env()
        self.assertEqual(cmd[0], "chromium-browser")
        self.assertIn("--ozone-platform=wayland", cmd)
        self.assertEqual(cmd[-1], "X")
        # Writing the display settings again keeps the server and key
        self.assertEqual(ks.display(self.base, "chromium"), 0)
        self.assertIn("SERVER=http://dashboard.example:8080", (self.base / "data/kiosk.env").read_text())
        self.assertEqual(ks.display(self.base, "chromium; rm -rf /"), 2)

    # --- the launcher follows the server's sleep schedule

    def stub_screen(self):
        """A stand-in screen.sh that logs what it was told and remembers on/off."""
        pidir = self.base / "pi"
        pidir.mkdir(parents=True, exist_ok=True)
        self.log, state = self.root / "screen.log", self.root / "screen.state"
        self.state = state
        state.write_text("on")
        (pidir / "screen.sh").write_text(
            '#!/usr/bin/env bash\n'
            f'case "$1" in state) cat {state};; on) echo on > {state}; echo on >> {self.log};; '
            f'off) echo off > {state}; echo off >> {self.log};; esac\n')
        (pidir / "screen.sh").chmod(0o755)
        (pidir / "kiosk.sh").write_bytes((Path(ks.__file__).parent / "kiosk.sh").read_bytes())
        (self.base / "data").mkdir(parents=True, exist_ok=True)
        (self.base / "data/kiosk.env").write_text("CMD=(echo __URL__)\nPORT=1\n")
        return pidir / "kiosk.sh"

    def run_lib(self, kiosk, script, wayland="w"):
        env = {"PATH": "/usr/bin:/bin", "KIOSK_LIB_ONLY": "1", **({"WAYLAND_DISPLAY": wayland} if wayland else {})}
        return subprocess.run(["bash", "-c", f"source {shlex.quote(str(kiosk))}; {script}"], capture_output=True, text=True,
                              check=True, env=env).stdout

    ANSWER = "$(printf '/?mode=live\\n0\\n%s\\n' {})"

    def test_launcher_follows_sleep_state(self):
        kiosk = self.stub_screen()
        asleep, awake = (self.ANSWER.format(s) for s in ("asleep", "awake"))
        out = self.run_lib(kiosk, "\n".join([
            f'sync_screen "{asleep}"',                       # awake -> off
            f'sync_screen "{asleep}"',                       # same answer within 30 s: nothing more to do
            f'sync_screen "{awake}"',                        # asleep -> on
            "sync_screen \"$(printf 'x\\n1\\n')\"",          # an older server (no third line) changes nothing
            f'printf "pages=%s\\n" "$(page_of "{asleep}" | tr "\\n" ,)"',
            f'[ "$(page_of "{asleep}")" = "$(page_of "{awake}")" ] && echo same-page || echo different-page',
        ]))
        self.assertEqual(self.log.read_text().split(), ["off", "on"])
        self.assertIn("pages=/?mode=live,0,", out)
        self.assertIn("same-page", out)                      # a sleep change alone never restarts the browser

    def test_launcher_leaves_the_screen_alone_off_cage(self):
        kiosk = self.stub_screen()
        self.run_lib(kiosk, f'sync_screen "{self.ANSWER.format("asleep")}"', wayland="")
        self.assertFalse(self.log.exists())

    def test_launcher_corrects_a_stray_wake(self):
        """A stale timer turning the screen on during a scheduled sleep is put right at the next check."""
        kiosk = self.stub_screen()
        asleep = self.ANSWER.format("asleep")
        self.run_lib(kiosk, "\n".join([
            f'sync_screen "{asleep}"',
            f'echo on > {shlex.quote(str(self.state))}',    # something wakes the screen
            'last_check=-100',                              # ...and the 30 s check comes round
            f'sync_screen "{asleep}"',
        ]))
        self.assertEqual(self.log.read_text().split(), ["off", "off"])

    def test_screen_state_reads_wlr_randr(self):
        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        (self.base / "data").mkdir(parents=True, exist_ok=True)
        (self.base / "data/kiosk.env").write_text("PORT=1\n")
        (self.base / "pi").mkdir(parents=True, exist_ok=True)
        (self.base / "pi/screen.sh").write_bytes((Path(ks.__file__).parent / "screen.sh").read_bytes())
        for enabled in ("yes", "no"):
            (bin_dir / "wlr-randr").write_text(f'#!/bin/sh\necho \'HDMI-A-1 "TV"\'; echo "  Enabled: {enabled}"\n')
            (bin_dir / "wlr-randr").chmod(0o755)
            out = subprocess.run(["bash", str(self.base / "pi/screen.sh"), "state"], capture_output=True, text=True,
                                 env={"PATH": f"{bin_dir}:/usr/bin:/bin"}).stdout.strip()
            self.assertEqual(out, "on" if enabled == "yes" else "off")


if __name__ == "__main__":
    unittest.main()
