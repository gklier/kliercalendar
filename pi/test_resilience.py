"""Power and network outages: the TV launcher reopens the dashboard when the server comes back, and the
network watchdog only acts when the Pi loses its router.  Run: python3 -m unittest -v test_resilience.py"""

import http.server
import os
import shutil
import signal
import stat
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path

PI = Path(__file__).parent


def launcher_env(base, **extra):
    """The environment kiosk.sh runs in. A Mac has no setsid command (the Pi does), so give it a stand-in."""
    env = {**os.environ, **extra}
    env.pop("WAYLAND_DISPLAY", None)
    if not shutil.which("setsid"):
        shim = base / "shim"
        shim.mkdir(exist_ok=True)
        (shim / "setsid").write_text('#!/bin/bash\nexec python3 -c "import os,sys; os.setsid(); os.execvp(sys.argv[1], sys.argv[1:])" "$@"\n')
        (shim / "setsid").chmod(0o755)
        env["PATH"] = f"{shim}:{env['PATH']}"
    return env


def wait_for(cond, seconds=15):
    end = time.time() + seconds
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


class WatchdogTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.log = self.root / "calls.log"
        self.log.touch()
        nmcli = """
case "$*" in
  "-t -f AUTOCONNECT-PRIORITY,NAME,TYPE connection show") printf '40:HomeWiFi:802-11-wireless\\n0:lo:loopback\\n60:HomeIoT:802-11-wireless\\n' ;;
  "-t -f NAME,TYPE connection show --active") echo "HomeIoT:802-11-wireless" ;;
  *) echo "nmcli $*" >> "$CALLS" ;;
esac"""
        for name, body in {
            "ip": 'echo "default via 192.168.0.1 dev wlan0"',
            "ping": '[ -f "$FAKE_ROUTER_UP" ]',
            "curl": 'if [ -f "$FAKE_SERVER_UP" ]; then printf 401; else printf 000; exit 7; fi',
            "nmcli": nmcli,
            "systemctl": 'echo "systemctl $*" >> "$CALLS"',
            "rfkill": 'echo "rfkill $*" >> "$CALLS"',
            "reboot": 'echo "reboot" >> "$CALLS"',
            "logger": "true",
        }.items():
            f = self.bin / name
            f.write_text("#!/bin/bash\n" + body + "\n")
            f.chmod(f.stat().st_mode | stat.S_IEXEC)
        self.router = self.root / "router-up"
        self.server = self.root / "server-up"
        self.envfile = self.root / "kiosk.env"
        self.envfile.write_text("SERVER='http://dashboard.example:8080'\nTV_KEY='x'\n")

    def tearDown(self):
        self.tmp.cleanup()

    def run_watch(self, times=1):
        env = {**os.environ, "PATH": f"{self.bin}:{os.environ['PATH']}", "CALLS": str(self.log),
               "FAKE_ROUTER_UP": str(self.router), "FAKE_SERVER_UP": str(self.server), "NETWATCH_ENV": str(self.envfile),
               "NETWATCH_STATE": str(self.root / "state"), "NETWATCH_SERVER_STATE": str(self.root / "sstate"),
               "NETWATCH_STAMP": str(self.root / "stamp")}
        for _ in range(times):
            subprocess.run(["bash", str(PI / "netwatch.sh")], env=env, check=True, capture_output=True)

    def calls(self):
        return self.log.read_text().splitlines()

    # ---- the router

    def test_internet_outage_does_nothing(self):
        self.router.touch()                    # router and server answer; only the internet is gone (never checked)
        self.server.touch()
        self.run_watch(40)
        self.assertEqual(self.calls(), [])

    def test_lost_router_is_handled_in_steps(self):
        self.run_watch(2)
        self.assertEqual(self.calls(), [])                                   # a couple of missed checks are nothing
        self.run_watch(1)
        self.assertIn("nmcli connection up HomeIoT", self.calls())           # 3: ask for the preferred saved Wi-Fi again
        self.assertNotIn("reboot", " ".join(self.calls()))
        self.run_watch(5)
        self.assertIn("systemctl restart NetworkManager", self.calls())      # 8: restart the network service
        self.run_watch(11)
        self.assertNotIn("systemctl reboot", self.calls())                   # 19 misses: still waiting
        self.run_watch(1)
        self.assertIn("systemctl reboot", self.calls())                      # 20: restart the Pi
        before = len(self.calls())
        self.run_watch(5)
        self.assertEqual(len(self.calls()), before)                          # never a second restart within the hour

    def test_coming_back_starts_the_count_again(self):
        self.server.touch()
        self.run_watch(2)
        self.router.touch()
        self.run_watch(1)
        self.router.unlink()
        self.run_watch(2)
        self.assertEqual(self.calls(), [])                                   # 2 more misses, not 5

    # ---- the dashboard server

    def test_unreachable_server_is_handled_in_steps(self):
        self.router.touch()                                                  # joined to Wi-Fi, but no way to the server
        self.run_watch(4)
        self.assertEqual(self.calls(), [])
        self.run_watch(1)
        self.assertEqual(self.calls(), ["nmcli connection up HomeIoT"])      # 5: join the current network again
        self.run_watch(5)
        self.assertEqual(self.calls()[-1], "nmcli connection up HomeWiFi")   # 10: try the other saved network
        self.run_watch(10)
        self.assertEqual(self.calls()[-1], "systemctl restart NetworkManager")   # 20: back to the preferred one
        self.run_watch(40)
        self.assertNotIn("reboot", " ".join(self.calls()))                   # never a restart of the Pi for this
        self.assertEqual(self.calls().count("nmcli connection up HomeIoT"), 2)   # minutes 5 and 35: the steps repeat every 30

    def test_server_answering_resets_the_count(self):
        self.router.touch()
        self.run_watch(4)
        self.server.touch()
        self.run_watch(1)
        self.server.unlink()
        self.run_watch(4)
        self.assertEqual(self.calls(), [])

    def test_server_on_the_pi_itself_is_not_checked(self):
        self.router.touch()
        self.envfile.write_text("PORT=8080\n")                              # no SERVER: the dashboard runs on this Pi
        self.run_watch(25)
        self.assertEqual(self.calls(), [])


class LauncherRecoveryTest(unittest.TestCase):
    def test_reopens_the_dashboard_after_an_outage(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        base = Path(tmp.name)
        (base / "pi").mkdir()
        (base / "data").mkdir()
        (base / "pi/kiosk.sh").write_bytes((PI / "kiosk.sh").read_bytes())
        opened = base / "opened.log"
        browser = base / "fakebrowser"
        browser.write_text(f'#!/bin/bash\necho "$1" >> {opened}\nexec sleep 20\n')
        browser.chmod(0o755)
        state = {"down": False}

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a): pass
            def do_GET(self):
                if state["down"]:
                    self.send_response(503)
                    self.end_headers()
                    return
                body = b"/?mode=live\n0\nawake\n"
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.shutdown)
        (base / "data/kiosk.env").write_text(f"CMD=({browser} __URL__)\nPORT={srv.server_port}\n")
        env = launcher_env(base, KIOSK_POLL="0.1", KIOSK_RECOVER_AFTER="4", KIOSK_FIRST_TRIES="5")
        p = subprocess.Popen(["bash", str(base / "pi/kiosk.sh")], env=env, start_new_session=True,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(lambda: os.killpg(p.pid, signal.SIGKILL))
        count = lambda: len(opened.read_text().splitlines()) if opened.exists() else 0
        self.assertTrue(wait_for(lambda: count() == 1), "the browser should open once the server answers")
        time.sleep(1.0)
        self.assertEqual(count(), 1)                          # nothing changes while all is well
        state["down"] = True
        time.sleep(1.5)                                       # the outage: the browser is left alone meanwhile
        self.assertEqual(count(), 1)
        state["down"] = False
        self.assertTrue(wait_for(lambda: count() == 2), "the dashboard should be opened again when the server is back")

    def test_short_hiccup_does_not_restart_the_browser(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        base = Path(tmp.name)
        (base / "pi").mkdir()
        (base / "data").mkdir()
        (base / "pi/kiosk.sh").write_bytes((PI / "kiosk.sh").read_bytes())
        opened = base / "opened.log"
        browser = base / "fakebrowser"
        browser.write_text(f'#!/bin/bash\necho "$1" >> {opened}\nexec sleep 20\n')
        browser.chmod(0o755)
        state = {"down": False}

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a): pass
            def do_GET(self):
                if state["down"]:
                    self.send_response(503)
                    self.end_headers()
                    return
                body = b"/?mode=live\n0\nawake\n"
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.shutdown)
        (base / "data/kiosk.env").write_text(f"CMD=({browser} __URL__)\nPORT={srv.server_port}\n")
        env = launcher_env(base, KIOSK_POLL="0.1", KIOSK_RECOVER_AFTER="10", KIOSK_FIRST_TRIES="5")
        p = subprocess.Popen(["bash", str(base / "pi/kiosk.sh")], env=env, start_new_session=True,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(lambda: os.killpg(p.pid, signal.SIGKILL))
        self.assertTrue(wait_for(lambda: opened.exists() and len(opened.read_text().splitlines()) == 1))
        state["down"] = True
        time.sleep(0.5)                                       # about three missed checks, fewer than the limit
        state["down"] = False
        time.sleep(1.5)
        self.assertEqual(len(opened.read_text().splitlines()), 1)


if __name__ == "__main__":
    unittest.main()
