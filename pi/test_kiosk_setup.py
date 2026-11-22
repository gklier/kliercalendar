"""Tests for the autostart finder/patcher.  Run: python3 -m unittest -v test_kiosk_setup.py"""

import contextlib
import io
import json
import shlex
import subprocess
import tempfile
import unittest
from pathlib import Path

import kiosk_setup as ks

DAK = "https://dakboard.com/screen/uuid/1234-abcd"


class KioskSetupTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.home = self.root / "home/gk"
        self.base = self.root / "opt"
        (self.base / "pi").mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, rel, text):
        p = self.home / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
        return p

    def run_install(self):
        return ks.install(self.base, 8080, [self.home], self.root / "www", self.root / "systemd")

    def env(self):
        # Evaluate kiosk.env the way kiosk.sh does
        out = subprocess.run(["bash", "-c", f'source {shlex.quote(str(self.base / "data/kiosk.env"))}; url=X; '
                              'for t in "${CMD[@]}"; do printf "%s\\n" "${t//__URL__/$url}"; done; echo "$DAKBOARD_URL"'],
                             capture_output=True, text=True, check=True).stdout.splitlines()
        return out[:-1], out[-1]

    def test_labwc_autostart_roundtrip(self):
        original = f"swayidle -w timeout 600 'wlopm --off \\*' &\nchromium-browser --kiosk --noerrdialogs {DAK} &\n"
        f = self.write(".config/labwc/autostart", original)
        self.assertEqual(self.run_install(), 0)
        patched = f.read_text()
        self.assertIn(f"{self.base}/pi/kiosk.sh &\n", patched)
        self.assertIn("swayidle", patched)
        cmd, url = self.env()
        self.assertEqual(cmd, ["chromium-browser", "--kiosk", "--noerrdialogs", "X"])
        self.assertEqual(url, DAK)
        self.assertEqual(ks.restore(self.base), 0)
        self.assertEqual(f.read_text(), original)

    def test_lxde_app_flag(self):
        f = self.write(".config/lxsession/LXDE-pi/autostart", f"@xset s off\n@chromium-browser --start-fullscreen --app={DAK}\n")
        self.assertEqual(self.run_install(), 0)
        self.assertIn(f"@{self.base}/pi/kiosk.sh\n", f.read_text())
        self.assertEqual(self.env()[0], ["chromium-browser", "--start-fullscreen", "--app=X"])

    def test_wayfire_and_desktop(self):
        f = self.write(".config/wayfire.ini", f"[autostart]\nkiosk = chromium --kiosk '{DAK}'\n")
        self.assertEqual(self.run_install(), 0)
        self.assertIn(f"kiosk = {self.base}/pi/kiosk.sh", f.read_text())

    def test_desktop_entry(self):
        f = self.write(".config/autostart/dak.desktop", f"[Desktop Entry]\nType=Application\nExec=chromium-browser --kiosk {DAK}\n")
        self.assertEqual(self.run_install(), 0)
        self.assertIn(f"Exec={self.base}/pi/kiosk.sh\n", f.read_text())

    def test_shell_script_foreground_uses_exec(self):
        f = self.write("kiosk.sh", f"#!/bin/bash\nsleep 5\n/usr/bin/chromium-browser --kiosk {DAK}\n")
        self.assertEqual(self.run_install(), 0)
        self.assertIn(f"exec {self.base}/pi/kiosk.sh\n", f.read_text())

    def test_ambiguous_or_missing_changes_nothing(self):
        self.assertEqual(self.run_install(), 2)
        a = self.write(".config/labwc/autostart", f"chromium {DAK} &\n")
        b = self.write("start.sh", f"chromium {DAK}\n")
        self.assertEqual(self.run_install(), 2)
        self.assertEqual(a.read_text(), f"chromium {DAK} &\n")
        self.assertEqual(b.read_text(), f"chromium {DAK}\n")
        self.assertFalse((self.base / "data/kiosk.env").exists())

    def test_comments_ignored_and_second_install_is_noop(self):
        f = self.write(".config/labwc/autostart", f"# chromium {DAK}\nchromium {DAK} &\n")
        self.assertEqual(self.run_install(), 0)
        once = f.read_text()
        self.assertEqual(self.run_install(), 0)
        self.assertEqual(f.read_text(), once)

    # DAKboard OS: autostart -> ~/startup/run.sh -> ~/startup/browser.sh -> Chromium on a local
    # loader page; the screen link lives in /var/www/config.json (layout copied from a real Pi).
    LOADER = "file:///home/dakboard/startup/browser-init.html"
    DAKOS_RUN = ("#!/bin/bash\n\n#####PLACE RTSP VLC COMMANDS HERE#####\n"
                 "cvlc --loop rtsp://camera.example:7447/x :sout-keep &\n\n"
                 "#Start browser.sh in the background\n/home/dakboard/startup/browser.sh\n")

    def dakos(self, private_url=DAK):
        lib = self.write("dakos-lib.sh", "export IS_RPI4=1\n")
        self.write("startup/browser.sh", f"""#!/bin/bash
export DISPLAY=:0
COMMON_ARGS=" \\
    --window-size=7000,7000 \\
    --start-fullscreen \\
    --incognito \\
    "
LAST_ARGS="--kiosk {self.LOADER}"
. {lib}
if [ "${{IS_RPI4}}" = "1" ]
then {{
  echo "Opening Chromium with Pi4 flags";
  chromium-browser \\
    $COMMON_ARGS \\
    --ignore-gpu-blocklist \\
    $LAST_ARGS
}}
else {{
  chromium $COMMON_ARGS $LAST_ARGS
}}
fi
""")
        run = self.write("startup/run.sh", self.DAKOS_RUN)
        www = self.root / "www"
        www.mkdir(exist_ok=True)
        units = self.root / "systemd"
        units.mkdir(exist_ok=True)
        (units / ks.DAKOS_UNIT).write_text("[Service]\nExecStart=/home/dakboard/startup/browser.sh\n")
        if private_url is not None:
            (www / "config.json").write_text(json.dumps({"volume": "50", "private_url": private_url}))
        return run

    def test_dakboard_os_run_sh_roundtrip(self):
        run = self.dakos()
        run.chmod(0o775)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(self.run_install(), 0)
        self.assertIn(f"DAKBOARD_URL={DAK}", out.getvalue())       # install.sh gives this link to the server
        patched = run.read_text()
        self.assertIn(f"\nexec {self.base}/pi/kiosk.sh\n", patched)
        self.assertIn("cvlc --loop", patched)                     # the camera stream still starts
        self.assertNotIn("startup/browser.sh\n", patched)
        self.assertEqual(run.stat().st_mode & 0o777, 0o775)
        cmd, url = self.env()
        self.assertEqual(cmd, ["chromium-browser", "--window-size=7000,7000", "--start-fullscreen", "--incognito",
                               "--ignore-gpu-blocklist", "--kiosk", "X"])
        self.assertEqual(url, self.LOADER)                        # server down -> DAKboard OS's own loader
        self.assertEqual(json.loads((self.base / "data/kiosk-backup.json").read_text())["url"], DAK)
        dropin = self.root / "systemd" / f"{ks.DAKOS_UNIT}.d/family-dashboard.conf"
        self.assertIn("ExecStart=\nExecStart=/bin/true\n", dropin.read_text())   # no second browser at boot
        self.assertNotIn("\nExecStartPre=", dropin.read_text())                  # DAKboard check-in untouched
        self.assertEqual(ks.restore(self.base), 0)
        self.assertEqual(run.read_text(), self.DAKOS_RUN)
        self.assertFalse(dropin.parent.exists())

    def test_dakboard_os_without_readable_link_uses_loader(self):
        self.dakos(private_url=None)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(self.run_install(), 0)
        self.assertIn(f"DAKBOARD_URL={self.LOADER}", out.getvalue())

    def test_dakboard_os_welcome_page_is_not_a_link(self):
        self.dakos(private_url="http://localhost/welcome.php")
        self.assertIsNone(ks.dakos_screen_url(self.root / "www"))

    def test_direct_browser_line_wins_over_dakboard_os(self):
        run = self.dakos()
        f = self.write(".config/labwc/autostart", f"chromium {DAK} &\n")
        self.assertEqual(self.run_install(), 0)
        self.assertIn("kiosk.sh", f.read_text())
        self.assertEqual(run.read_text(), self.DAKOS_RUN)
        self.assertFalse((self.root / "systemd" / f"{ks.DAKOS_UNIT}.d").exists())

    def test_dakboard_os_commented_line_ignored(self):
        self.dakos()
        self.write("startup/run.sh", "#/home/dakboard/startup/browser.sh\n")
        self.assertEqual(self.run_install(), 2)

    # --- off-site server: configure + what the launcher opens

    def test_configure_and_launcher_urls(self):
        import http.server, threading
        self.write(".config/labwc/autostart", f"chromium-browser --kiosk {DAK} &\n")
        self.assertEqual(self.run_install(), 0)
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
            answer["text"] = f"{DAK}\n1\n"
            self.assertEqual(url(), DAK)
            # Back to the server on the Pi: no key, the server's own address
            self.assertEqual(ks.configure(self.base, "", ""), 0)
            self.assertNotIn("TV_KEY", (self.base / "data/kiosk.env").read_text())
            self.assertEqual(self.env()[1], DAK)                                     # fallback untouched
        finally:
            srv.shutdown()

    def test_display_only_lite(self):
        """Raspberry Pi OS Lite: no desktop to patch; the launcher runs under cage."""
        self.assertEqual(ks.display(self.base, "chromium-browser", DAK), 0)
        self.assertEqual(ks.configure(self.base, "http://dashboard.example:8080", "k" * 32), 0)
        cmd, url = self.env()
        self.assertEqual(cmd[0], "chromium-browser")
        self.assertIn("--ozone-platform=wayland", cmd)
        self.assertEqual(cmd[-1], "X")
        self.assertEqual(url, DAK)
        # Writing the display settings again keeps the server and key
        self.assertEqual(ks.display(self.base, "chromium", ""), 0)
        env = (self.base / "data/kiosk.env").read_text()
        self.assertIn("SERVER=http://dashboard.example:8080", env)
        self.assertEqual(self.env()[1], ks.NO_SERVER_PAGE)
        self.assertEqual(ks.display(self.base, "chromium; rm -rf /", ""), 2)


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
        (self.base / "data/kiosk.env").write_text("CMD=(echo __URL__)\nDAKBOARD_URL=x\nPORT=1\n")
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
