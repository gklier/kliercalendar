"""Chaos tests: hostile settings, hostile web requests, upstream services that lie, hang or flood,
and a damaged data folder. Nothing here may crash the server, hang it, or corrupt its settings.
Run: cd server && python3 -m unittest test_chaos.py"""

import json
import random
import socket
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import server
from test_server import WEB, Client

DEFAULTS = server.deep_merge(server.DEFAULT_CONFIG, {})


# ---------------------------------------------------------------- 1. settings fuzzing

def junk(rng: random.Random, depth: int = 0):
    pool = [None, True, False, 0, -1, 7, 2**70, -2**70, 1.5, 1e308, "", " ", "x" * 5000, "../../etc/passwd", "<script>alert(1)</script>",
            "\x00", "💥" * 40, "http://", "ftp://x", "javascript:alert(1)", "08:60", "25:00", "Mars/Base", "#GGGGGG", "camera.", "lock.a\n", "a" * 70 + ".b",
            [], {}, [None], [[]], [{}]]
    if depth < 3 and rng.random() < 0.35:
        return rng.choice([[junk(rng, depth + 1) for _ in range(rng.randint(0, 4))],
                           {rng.choice(["id", "name", "x", "y", "w", "h", "type", "days", "off", "on", "url", "token", "options", "cameras"]): junk(rng, depth + 1)
                            for _ in range(rng.randint(0, 4))}])
    return rng.choice(pool)


class SettingsFuzz(unittest.TestCase):
    def test_apply_settings_only_ever_says_no_politely(self):
        rng = random.Random(20261122)
        sections = list(DEFAULTS) + ["layout", "ha", "schedule", "bogus"]
        crashes = {}
        accepted = 0
        for _ in range(6000):
            patch = {}
            for _ in range(rng.randint(1, 3)):
                sec = rng.choice(sections)
                body = DEFAULTS.get(sec)
                if isinstance(body, dict) and rng.random() < 0.8:
                    patch[sec] = {k: junk(rng) for k in rng.sample(list(body), k=min(len(body), rng.randint(1, 3)))}
                else:
                    patch[sec] = junk(rng)
            try:
                out = server.apply_settings(DEFAULTS, patch)
            except ValueError:
                continue
            except Exception as e:  # noqa: BLE001 — that's what we're hunting
                crashes.setdefault((type(e).__name__, str(e)[:80], tuple(patch)), patch)
                continue
            accepted += 1
            json.dumps(out, allow_nan=False)                        # whatever was accepted must be storable
            server.apply_settings(out, {})                          # ...and survive being loaded and saved again
            server.public_settings(out)                             # ...and shown to a browser
        self.assertFalse(crashes, "\n".join(f"{k}: {json.dumps(v)[:200]}" for k, v in list(crashes.items())[:8]))
        self.assertGreater(accepted, 50)                            # the fuzz isn't just bouncing off the front door

    def test_layout_extremes(self):
        box = lambda **kw: {"type": "clock", "x": 0, "y": 0, "w": 100, "h": 100, **kw}
        for bad in (dict(w=0), dict(h=-5), dict(x=10**9), dict(w=True), dict(x="1"), dict(x=float("inf")), dict(scale=0), dict(scale=99)):
            with self.assertRaises(ValueError, msg=str(bad)):
                server.apply_settings(DEFAULTS, {"layout": {"widgets": [box(**bad)]}})


# ---------------------------------------------------------------- 2. the web front door

def raw(port: int, payload: bytes, wait: float = 3.0) -> bytes:
    s = socket.create_connection(("127.0.0.1", port), timeout=wait)
    try:
        s.sendall(payload)
        s.shutdown(socket.SHUT_WR)
        out = b""
        while True:
            try:
                chunk = s.recv(65536)
            except socket.timeout:
                break
            if not chunk:
                break
            out += chunk
        return out
    finally:
        s.close()


class Boot:
    """A real server on a temporary folder."""

    def __init__(self, config: dict | str | None = None, offsite: bool = False):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.cfg = self.root / "config.json"
        if config is not None:
            self.cfg.write_text(config if isinstance(config, str) else json.dumps(config))
        (self.root / "state").mkdir()
        self.app = server.App(server.Store(self.cfg), WEB, self.root / "state", offsite=offsite)
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), server.make_handler(self.app))
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.port = self.srv.server_port
        self.base = f"http://127.0.0.1:{self.port}"

    def close(self):
        self.srv.shutdown()
        self.tmp.cleanup()

    def admin(self) -> Client:
        c = Client(self.base)
        if not self.app.config["admin"]["password_hash"]:
            assert c.call("/admin/api/setup", {"password": "correct horse"})[0] == 200
        else:
            assert c.call("/admin/api/login", {"password": "correct horse"})[0] == 200
        return c


class WebAbuse(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.b = Boot()
        cls.c = cls.b.admin()

    @classmethod
    def tearDownClass(cls):
        cls.b.close()

    def alive(self):
        s, _ = Client(self.b.base).call("/admin/api/session")
        self.assertEqual(s, 200)

    def test_malformed_requests_get_an_answer_not_a_crash(self):
        port = self.b.port
        cookie = self.c.cookie
        cases = {
            "garbage": b"\x00\x01\x02 not http at all\r\n\r\n",
            "no-version": b"GET /\r\n\r\n",
            "bad-length": b"POST /admin/api/settings HTTP/1.1\r\nHost: x\r\nX-Dashboard: 1\r\nContent-Length: abc\r\n\r\n{}",
            "negative-length": b"POST /admin/api/login HTTP/1.1\r\nHost: x\r\nX-Dashboard: 1\r\nContent-Length: -1\r\n\r\n{}",
            "huge-length": b"POST /admin/api/login HTTP/1.1\r\nHost: x\r\nX-Dashboard: 1\r\nContent-Length: 99999999999\r\n\r\n{}",
            "short-body": b"POST /admin/api/login HTTP/1.1\r\nHost: x\r\nX-Dashboard: 1\r\nContent-Length: 500\r\n\r\n{}",
            "not-json": b"POST /admin/api/login HTTP/1.1\r\nHost: x\r\nX-Dashboard: 1\r\nContent-Length: 6\r\n\r\n<html>",
            "json-list": b"POST /admin/api/login HTTP/1.1\r\nHost: x\r\nX-Dashboard: 1\r\nContent-Length: 2\r\n\r\n[]",
            "long-url": b"GET /" + b"a" * 70000 + b" HTTP/1.1\r\nHost: x\r\n\r\n",
            "null-byte-path": b"GET /%00 HTTP/1.1\r\nHost: x\r\n\r\n",
            "traversal": b"GET /../server/server.py HTTP/1.1\r\nHost: x\r\n\r\n",
            "traversal-encoded": b"GET /%2e%2e/%2e%2e/etc/passwd HTTP/1.1\r\nHost: x\r\n\r\n",
            "traversal-admin": b"GET /admin/..%2f..%2fserver/server.py HTTP/1.1\r\nHost: x\r\n\r\n",
            "traversal-photo": b"GET /photos/..%2f..%2fconfig.json HTTP/1.1\r\nHost: x\r\n\r\n",
            "weird-method": b"BREW /coffee HTTP/1.1\r\nHost: x\r\n\r\n",
            "put-settings": b"PUT /admin/api/settings HTTP/1.1\r\nHost: x\r\nContent-Length: 2\r\n\r\n{}",
            "cookie-junk": b"GET /admin/api/settings HTTP/1.1\r\nHost: x\r\nCookie: " + b"a=" + b"\xff\xfe" * 500 + b"\r\n\r\n",
            "many-headers": b"GET / HTTP/1.1\r\nHost: x\r\n" + b"".join(b"X-%d: y\r\n" % i for i in range(500)) + b"\r\n",
            "photo-upload-lies": b"POST /admin/api/photos?name=a.jpg HTTP/1.1\r\nHost: x\r\nX-Dashboard: 1\r\nContent-Length: 14000000\r\nCookie: " + cookie.encode() + b"\r\n\r\nabc",
        }
        for name, payload in cases.items():
            t0 = time.time()
            reply = raw(port, payload, wait=4)
            took = time.time() - t0
            self.assertLess(took, 3.8, f"{name}: the server sat on the connection for {took:.1f}s")
            head = reply.split(b"\r\n", 1)[0]
            self.assertFalse(head.startswith(b"HTTP/1.") and b" 500" in head[:14], f"{name}: {head!r}")
            for secret in (b"root:", b"password_hash", b"def make_handler"):
                self.assertNotIn(secret, reply, f"{name} leaked something")
            self.alive()

    def test_nobody_gets_in_without_signing_in(self):
        anon = Client(self.b.base)
        for path in ("/admin/api/settings", "/admin/api/status", "/admin/api/backup", "/admin/api/ha/entities", "/admin/api/photos"):
            self.assertEqual(anon.call(path)[0], 401, path)
        for path, body in (("/admin/api/settings", {"timezone": "UTC"}), ("/admin/api/tv/key", {}), ("/admin/api/password", {"new": "hackedhacked"})):
            self.assertIn(anon.call(path, body)[0], (401, 403, 404), path)
        self.assertEqual(anon.call("/admin/api/settings", {"timezone": "UTC"}, header=False)[0], 403)   # and not from another site

    def test_login_brute_force_is_throttled(self):
        c = Client(self.b.base)
        codes = [c.call("/admin/api/login", {"password": f"guess{i}"})[0] for i in range(12)]
        self.assertIn(429, codes)
        self.assertEqual(Client(self.b.base).call("/admin/api/login", {"password": "correct horse"})[0], 429)   # even the right one waits
        self.b.app.sessions.failures.clear()

    def test_a_stalled_client_does_not_block_the_rest(self):
        stuck = [socket.create_connection(("127.0.0.1", self.b.port)) for _ in range(40)]
        try:
            for s in stuck:
                s.sendall(b"POST /admin/api/login HTTP/1.1\r\nHost: x\r\nX-Dashboard: 1\r\nContent-Length: 50\r\n\r\n{")
            t0 = time.time()
            self.alive()
            self.assertLess(time.time() - t0, 2)
        finally:
            for s in stuck:
                s.close()

    def test_a_crowd_saving_and_reading_at_once(self):
        errors, lock = [], threading.Lock()
        zones = ["UTC", "America/Chicago", "Europe/Paris", "Asia/Tokyo", "Australia/Sydney"]

        def worker(n):
            c = Client(self.b.base)
            c.cookie = self.c.cookie
            for i in range(15):
                try:
                    if i % 3 == 0:
                        s, _ = c.call("/admin/api/settings", {"timezone": zones[(n + i) % 5]})
                        if s not in (200, 429):
                            raise AssertionError(f"save gave {s}")
                    else:
                        s, _ = Client(self.b.base).call(["/api/config", "/api/version", "/api/weather", "/api/calendar", "/admin/api/session"][i % 5])
                        if s == 500 or s > 504:                       # 502 is fine: nothing is connected to ask
                            raise AssertionError(f"read gave {s}")
                except Exception as e:  # noqa: BLE001
                    with lock:
                        errors.append(repr(e))

        threads = [threading.Thread(target=worker, args=(n,)) for n in range(30)]
        [t.start() for t in threads]
        [t.join(60) for t in threads]
        self.assertFalse(errors[:5])
        saved = json.loads(self.b.cfg.read_text())                # the file on disk is whole, never half-written
        self.assertIn(saved["timezone"], zones)
        self.alive()


# ---------------------------------------------------------------- 3. services that misbehave

class Chaos(BaseHTTPRequestHandler):
    mode = "ok"
    hits = 0

    def log_message(self, *a):
        pass

    def do_GET(self):
        Chaos.hits += 1
        m = Chaos.mode
        if m == "500":
            return self._send(500, b'{"message":"boom"}')
        if m == "401":
            return self._send(401, b'{"message":"nope"}')
        if m == "html":
            return self._send(200, b"<html><body>Captive portal. Please log in.</body></html>", "text/html")
        if m == "garbage":
            return self._send(200, b"\x00\xff{{{{ not json", "application/json")
        if m == "wrong-shape":
            return self._send(200, b'{"entity_id": 5, "state": null, "attributes": "oops"}')
        if m == "list-of-nulls":
            return self._send(200, json.dumps([None, 1, "x", {}, {"entity_id": None}, {"entity_id": "lock.a", "state": None, "attributes": None}]).encode())
        if m == "hostile-names":
            rows = [{"entity_id": f"lock.l{i}", "state": "unlocked", "attributes": {"friendly_name": "<img src=x onerror=alert(1)>" * 20},
                     "last_changed": "not a date"} for i in range(40)]
            return self._send(200, json.dumps(rows).encode())
        if m == "hang":
            time.sleep(12)
            return self._send(200, b"{}")
        if m == "flood":
            try:
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.end_headers()
                for _ in range(400):
                    self.wfile.write(b"\xff" * 100_000)
            except OSError:
                pass
            return
        if m == "drip":
            self.send_response(200)
            self.send_header("Content-Length", "100000")
            self.end_headers()
            try:
                for _ in range(40):
                    self.wfile.write(b"x")
                    self.wfile.flush()
                    time.sleep(0.5)
            except OSError:
                pass
            return
        self._send(200, b"{}")

    do_POST = do_GET

    def _send(self, code, body, ctype="application/json"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


TOKEN = "t" * 40


class UpstreamChaos(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.chaos = ThreadingHTTPServer(("127.0.0.1", 0), Chaos)
        threading.Thread(target=cls.chaos.serve_forever, daemon=True).start()
        cls.url = f"http://127.0.0.1:{cls.chaos.server_port}"
        cls.b = Boot()
        cls.c = cls.b.admin()
        s, res = cls.c.call("/admin/api/settings", {"ha": {"enabled": True, "url": cls.url, "token": TOKEN,
                                                           "entities": [{"id": "lock.a", "name": "A"}, {"id": "lock.l1", "name": "L1"}],
                                                           "cameras": [{"id": "camera.a", "name": "Cam", "motion": ["binary_sensor.m"]}]}})
        assert s == 200, res

    @classmethod
    def tearDownClass(cls):
        cls.b.close()
        cls.chaos.shutdown()

    def setUp(self):
        Chaos.mode = "ok"
        self.b.app.ha.snaps.clear() if hasattr(self.b.app.ha, "snaps") else None

    def test_home_assistant_lying_never_breaks_the_tv_feed(self):
        for mode in ("500", "401", "html", "garbage", "wrong-shape", "list-of-nulls", "hostile-names"):
            Chaos.mode = mode
            for path in ("/api/home", "/api/cameras", "/admin/api/ha/entities"):
                t0 = time.time()
                s, body = self.c.call(path)
                self.assertLess(time.time() - t0, 8, f"{mode} {path}")
                self.assertNotEqual(s, 500, f"{mode} {path}: {body}")
                self.assertIn(s, (200, 400, 401, 404, 502, 503, 504), f"{mode} {path}")
                self.assertIsInstance(body, dict, f"{mode} {path} should answer in JSON")
            Client(self.b.base).call("/admin/api/session")           # still alive

    def test_hostile_names_arrive_as_plain_text_for_the_tv_to_escape(self):
        Chaos.mode = "hostile-names"
        s, body = self.c.call("/api/home")
        if s == 200:
            for item in body["items"]:
                self.assertLessEqual(len(item["name"]), 60)
                self.assertIsInstance(item["minutes"], int)

    def test_a_hanging_home_assistant_does_not_hang_everything(self):
        Chaos.mode = "hang"
        done = []
        t = threading.Thread(target=lambda: done.append(self.c.call("/api/home")))
        t.start()
        t0 = time.time()
        Client(self.b.base).call("/admin/api/session")                # an unrelated request is not queued behind it
        self.assertLess(time.time() - t0, 2)
        t.join(20)
        self.assertTrue(done, "the request to a hanging Home Assistant never finished")
        self.assertNotEqual(done[0][0], 500)

    def test_a_camera_that_floods_or_drips_is_cut_off(self):
        for mode in ("flood", "drip"):
            Chaos.mode = mode
            t0 = time.time()
            try:
                with urllib.request.urlopen(urllib.request.Request(self.b.base + "/api/ha/camera/camera.a.jpg", headers={"Cookie": self.c.cookie}), timeout=30) as r:
                    size, status = len(r.read()), r.status
            except urllib.error.HTTPError as e:
                size, status = 0, e.code
            self.assertLess(time.time() - t0, 25, mode)
            self.assertLessEqual(size, 8_000_000, f"{mode}: relayed {size} bytes")
            self.assertNotEqual(status, 500, mode)

    def test_other_feeds_survive_nonsense_too(self):
        # Point calendar, weather, drive times and stocks at a service that lies
        s, res = self.c.call("/admin/api/settings", {
            "tempest": {"station_id": "123", "token": "x" * 20},
            "calendar": {"calendars": [{"name": "Junk", "color": "#112233", "ics_url": self.url + "/c.ics"}]},
        })
        self.assertEqual(s, 200, res)
        for mode in ("500", "html", "garbage", "list-of-nulls", "flood"):
            Chaos.mode = mode
            for path in ("/api/calendar", "/api/weather", "/api/drive", "/api/stocks", "/admin/api/status"):
                t0 = time.time()
                s, body = self.c.call(path)
                self.assertLess(time.time() - t0, 30, f"{mode} {path}")
                self.assertNotEqual(s, 500, f"{mode} {path}: {body}")
        Client(self.b.base).call("/admin/api/session")


# ---------------------------------------------------------------- 4. a damaged data folder

class DamagedData(unittest.TestCase):
    def boot(self, text):
        b = Boot(text)
        try:
            self.assertEqual(Client(b.base).call("/admin/api/session")[0], 200)
            self.assertEqual(Client(b.base).call("/api/config")[0] in (200, 401), True)
        finally:
            b.close()

    def test_odd_config_files_still_start_the_server(self):
        for name, text in {"empty": "", "spaces": "   ", "wrong-types": json.dumps({"ha": "yes", "stocks": [], "layout": 5, "schedule": None,
                                                                                    "calendar": {"calendars": "all", "account": 3}}),
                           "bom": "\ufeff{}"}.items():
            try:
                self.boot(text)
            except BaseException as e:  # noqa: BLE001
                self.fail(f"{name}: {type(e).__name__}: {e}")

    def test_unreadable_config_files_stop_with_a_clear_message_rather_than_being_overwritten(self):
        for name, text in {"half-written": '{"timezone": "UTC", "ha": {', "list": "[]", "string": '"hi"', "number": "12", "null": "null"}.items():
            with self.assertRaises(SystemExit, msg=name) as ctx:
                Boot(text)
            self.assertIn("Settings file", str(ctx.exception), name)

    def test_a_failed_save_leaves_the_old_settings_intact(self):
        b = Boot({})
        try:
            c = b.admin()
            self.assertEqual(c.call("/admin/api/settings", {"timezone": "Europe/Paris"})[0], 200)
            real = server.os.replace
            server.os.replace = lambda *a, **k: (_ for _ in ()).throw(OSError(28, "No space left on device"))
            try:
                s, res = c.call("/admin/api/settings", {"timezone": "Asia/Tokyo"})
            finally:
                server.os.replace = real
            self.assertEqual(s, 500)
            self.assertIn("data folder", res["error"])
            self.assertEqual(json.loads(b.cfg.read_text())["timezone"], "Europe/Paris")
            Client(b.base).call("/admin/api/session")
        finally:
            b.close()


if __name__ == "__main__":
    unittest.main()
