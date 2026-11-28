"""End-to-end tests: fake Tempest, iCal and Google Routes upstreams, and the
real dashboard server talking to them over HTTP.

Every name, address, password and key in this file is a made-up fixture for a throwaway test server.

Run:  python3 -m unittest -v test_server.py
"""

import datetime as dt
import json
import subprocess
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from zoneinfo import ZoneInfo

import server

TZ = ZoneInfo("America/New_York")
WEB = Path(server.__file__).resolve().parent.parent / "web"
# Google's documented example polyline: (38.5,-120.2) (40.7,-120.95) (43.252,-126.453)
POLY = "_p~iF~ps|U_ulLnnqC_mqNvxq`@"


def ics_fixture() -> str:
    today = dt.datetime.now(TZ).date()
    d = lambda n: (today + dt.timedelta(days=n)).strftime("%Y%m%d")
    first = today - dt.timedelta(days=21)
    excluded = first + dt.timedelta(days=28)
    return "\r\n".join([
        "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//test//EN",
        "BEGIN:VEVENT", "UID:weekly@test", "SUMMARY:Shop Shift", "LOCATION:Corner Market\\, Springfield",
        f"DTSTART;TZID=America/New_York:{first.strftime('%Y%m%d')}T160000",
        f"DTEND;TZID=America/New_York:{first.strftime('%Y%m%d')}T210000",
        "RRULE:FREQ=WEEKLY", f"EXDATE;TZID=America/New_York:{excluded.strftime('%Y%m%d')}T160000",
        "END:VEVENT",
        "BEGIN:VEVENT", "UID:trip@test", "SUMMARY:No School",
        f"DTSTART;VALUE=DATE:{d(3)}", f"DTEND;VALUE=DATE:{d(6)}", "END:VEVENT",
        "BEGIN:VEVENT", "UID:cancel@test", "SUMMARY:Cancelled thing", "STATUS:CANCELLED",
        f"DTSTART;TZID=America/New_York:{d(2)}T100000", f"DTEND;TZID=America/New_York:{d(2)}T110000", "END:VEVENT",
        "BEGIN:VEVENT", "UID:chicago@test", "SUMMARY:Call with Sam",
        f"DTSTART;TZID=America/Chicago:{d(1)}T180000", f"DTEND;TZID=America/Chicago:{d(1)}T183000", "END:VEVENT",
        "END:VCALENDAR", "",
    ])


def tempest_fixture() -> dict:
    midnight = dt.datetime.now(TZ).replace(hour=0, minute=0, second=0, microsecond=0)
    return {
        "status": {"status_code": 0, "status_message": "SUCCESS"},
        "units": {"units_temp": "f", "units_wind": "mph", "units_precip": "in"},
        "current_conditions": {"air_temperature": 61.2, "feels_like": 60.4, "conditions": "Rain Likely",
                               "icon": "rainy", "relative_humidity": 88, "wind_avg": 3.1,
                               "wind_direction_cardinal": "SW", "precip_accum_local_day": 0.33, "sea_level_pressure": 29.7},
        "forecast": {"daily": [{
            "day_start_local": int((midnight + dt.timedelta(days=i)).timestamp()),
            "air_temp_high": 64 + i, "air_temp_low": 45, "icon": "rainy", "conditions": "Rain",
            "precip_probability": 10 * i,
            "sunrise": int((midnight + dt.timedelta(days=i, hours=7)).timestamp()),
            "sunset": int((midnight + dt.timedelta(days=i, hours=17)).timestamp()),
        } for i in range(10)]},
    }


class FakeYahoo:
    """Stands in for the yfinance package, so the tests need no network (or pandas)."""
    PRICES = {"AAPL": (230.1, 227.48), "SPY": (590.0, 597.4), "BRK-B": (570.0, 571.72), "^IXIC": (22617.73, 23882.10),
              "AIVSX": (60.72, 63.25), "BTC-USD": (61134.67, 60086.0)}
    calls: list = []
    rate_limited = False
    gate = None                    # a threading.Event the fake waits on, to play a slow Yahoo
    flaky: dict = {}               # symbol -> how many times it fails before it works, like a cold-start hiccup

    class Ticker:
        def __init__(self, sym):
            self.sym = sym

        @property
        def fast_info(self):
            FakeYahoo.calls.append(self.sym)
            if FakeYahoo.gate is not None:
                FakeYahoo.gate.wait(5)
            if FakeYahoo.flaky.get(self.sym, 0) > 0:
                FakeYahoo.flaky[self.sym] -= 1
                raise OSError("connection reset")
            if FakeYahoo.rate_limited:
                raise type("YFRateLimitError", (Exception,), {})("Too Many Requests")
            if self.sym not in FakeYahoo.PRICES:
                raise KeyError("currentTradingPeriod")         # what yfinance does for a symbol that doesn't exist
            last, prev = FakeYahoo.PRICES[self.sym]
            return {"lastPrice": last, "previousClose": prev}

    class Search:
        def __init__(self, q, max_results=8, news_count=0):
            rows = [("AAPL", "Apple Inc.", "EQUITY", "NASDAQ"), ("SAAPL=F", "Apple Inc Stock Futures", "FUTURE", "CME"),
                    ("APLE", "Apple Hospitality REIT, Inc.", "EQUITY", "NYSE"), ("VFIAX", "Vanguard 500 Index Fd Admiral", "MUTUALFUND", "Nasdaq"),
                    ("^IXIC", "NASDAQ Composite", "INDEX", "Nasdaq GIDS"), ("not a ticker!", "junk", "EQUITY", ""),
                    ("BTC-USD", "Bitcoin USD", "CRYPTOCURRENCY", "CCC")]
            self.quotes = [{"symbol": a, "shortname": b, "quoteType": c, "exchDisp": d} for a, b, c, d in rows]


def use_fake_yahoo():
    real = server.load_yfinance
    server.load_yfinance = lambda: FakeYahoo
    FakeYahoo.calls, FakeYahoo.rate_limited = [], False
    return real


HA_TOKEN = "ha-test-token-abcdefghijklmnopqrstuvwxyz"
HA_JPEG = b"\xff\xd8\xff\xe0fake-camera-picture"


def ha_states():
    ago = lambda m: (dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=m)).isoformat().replace("+00:00", "Z")
    rows = [
        ("lock.front_door", "locked", {"friendly_name": "Front Door"}, 300),
        ("lock.back_door", "unlocked", {"friendly_name": "Back Door"}, 20),
        ("binary_sensor.garage_door", "on", {"friendly_name": "Garage Door", "device_class": "garage_door"}, 20),
        ("cover.side_gate", "closed", {"friendly_name": "Side Gate", "device_class": "gate"}, 500),
        ("person.alex", "home", {"friendly_name": "Alex"}, 60),
        ("person.sam", "not_home", {"friendly_name": "Sam"}, 60),
        ("alarm_control_panel.house", "armed_home", {"friendly_name": "House Alarm"}, 100),
        ("camera.front", "idle", {"friendly_name": "Front Camera"}, 5),
        ("binary_sensor.front_motion", Upstream.ha_motion, {"friendly_name": "Front Motion", "device_class": "motion"}, 1),
        ("binary_sensor.washer", "on", {"friendly_name": "Washer", "device_class": "running"}, 5),
        ("sensor.temp", "21", {"friendly_name": "Temperature"}, 5),
    ]
    return {eid: {"entity_id": eid, "state": st, "attributes": at, "last_changed": ago(m)} for eid, st, at, m in rows}


DAV_HOME = "/calendar/dav/parent%40example.com/user/"


def multistatus(*responses):
    return ('<?xml version="1.0"?><D:multistatus xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav" '
            'xmlns:A="http://apple.com/ns/ical/">' + "".join(responses) + "</D:multistatus>").encode()


class Upstream(BaseHTTPRequestHandler):
    posts = []
    proxy_paths: list = []
    ha_motion = "off"
    fail_tempest = False
    dav_calendars = ["family", "work"]

    def log_message(self, *a):
        pass

    def _reply(self, code, body, ctype="application/json"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/tempest"):
            if Upstream.fail_tempest:
                return self._reply(500, b'{"error":"boom"}')
            assert "token=TOKEN" in self.path and "station_id=123" in self.path
            return self._reply(200, json.dumps(tempest_fixture()).encode())
        if self.path == "/family.ics":
            return self._reply(200, ics_fixture().encode(), "text/calendar")
        if self.path == "/notcal.ics":
            return self._reply(200, b"<html>Sign in</html>", "text/html")
        if self.path.startswith("/ha/"):
            if self.headers.get("Authorization") != f"Bearer {HA_TOKEN}":
                return self._reply(401, b'{"message": "401: Unauthorized"}')
            path = self.path[3:]
            if path == "/api/":
                return self._reply(200, b'{"message": "API running."}')
            if path == "/api/config":
                return self._reply(200, json.dumps({"location_name": "Test House", "version": "2026.11.0"}).encode())
            if path == "/api/states":
                return self._reply(200, json.dumps(list(ha_states().values())).encode())
            if path.startswith("/api/states/"):
                st = ha_states().get(path[len("/api/states/"):])
                return self._reply(200, json.dumps(st).encode()) if st else self._reply(404, b'{"message": "Entity not found."}')
            if path.startswith("/api/camera_proxy/"):
                Upstream.proxy_paths.append(self.path)
                return self._reply(200, HA_JPEG, "image/jpeg")
            return self._reply(404, b"{}")
        self._reply(404, b"{}")

    def _dav_auth(self):
        import base64
        ok = self.headers.get("Authorization") == "Basic " + base64.b64encode(b"parent@example.com:abcdabcdabcdabcd").decode()
        if not ok:
            self._reply(401, b"", "text/plain")
        return ok

    def do_PROPFIND(self):
        self.rfile.read(int(self.headers["Content-Length"]))
        if not self._dav_auth():
            return
        if self.headers["Depth"] == "0":  # principal → calendar home
            body = multistatus(f'<D:response><D:href>{DAV_HOME}</D:href><D:propstat><D:prop><C:calendar-home-set>'
                               f'<D:href>/calendar/dav/home/</D:href></C:calendar-home-set></D:prop></D:propstat></D:response>')
        else:
            items = ['<D:response><D:href>/calendar/dav/home/</D:href><D:propstat><D:prop><D:resourcetype><D:collection/>'
                     '</D:resourcetype></D:prop></D:propstat></D:response>']
            for name in Upstream.dav_calendars:
                items.append(f'<D:response><D:href>/calendar/dav/{name}%40group/events/</D:href><D:propstat><D:prop>'
                             f'<D:displayname>{name.title()}</D:displayname><D:resourcetype><D:collection/><C:calendar/>'
                             f'</D:resourcetype><A:calendar-color>#9FE1E7FF</A:calendar-color></D:prop></D:propstat></D:response>')
            body = multistatus(*items)
        self._reply(207, body, "application/xml")

    def do_REPORT(self):
        q = self.rfile.read(int(self.headers["Content-Length"])).decode()
        if not self._dav_auth():
            return
        assert "time-range" in q
        ics = ics_fixture().replace("&", "&amp;").replace("<", "&lt;")
        self._reply(207, multistatus(f'<D:response><D:href>{self.path}x.ics</D:href><D:propstat><D:prop>'
                                     f'<C:calendar-data>{ics}</C:calendar-data></D:prop></D:propstat></D:response>'), "application/xml")

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        Upstream.posts.append((self.path, dict(self.headers), body))
        if self.path == "/matrix":
            rows = [{"originIndex": 0, "destinationIndex": 0, "status": {}, "condition": "ROUTE_EXISTS",
                     "distanceMeters": 39600, "duration": "1900s", "staticDuration": "1800s"},
                    {"originIndex": 0, "destinationIndex": 1, "status": {}, "condition": "ROUTE_NOT_FOUND"}]
            return self._reply(200, json.dumps(rows).encode())
        if self.path == "/routes":
            route = {"duration": "1500s", "staticDuration": "1200s", "distanceMeters": 16000, "description": "US-15 N",
                     "polyline": {"encodedPolyline": POLY},
                     "travelAdvisory": {"speedReadingIntervals": [
                         {"endPolylinePointIndex": 1, "speed": "NORMAL"},
                         {"startPolylinePointIndex": 1, "endPolylinePointIndex": 2, "speed": "TRAFFIC_JAM"}]}}
            return self._reply(200, json.dumps({"routes": [route]}).encode())
        self._reply(404, b"{}")


class Client:
    def __init__(self, base):
        self.base = base
        self.cookie = None

    def call(self, path, body=None, header=True):
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(self.base + path, data=data)
        if body is not None:
            req.add_header("Content-Type", "application/json")
            if header:
                req.add_header("X-Dashboard", "1")
        if self.cookie:
            req.add_header("Cookie", self.cookie)
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                if r.headers.get("Set-Cookie"):
                    self.raw_cookie = r.headers["Set-Cookie"]
                    self.cookie = self.raw_cookie.split(";")[0]
                raw = r.read()
                return r.status, (json.loads(raw) if r.headers.get_content_type() == "application/json" else raw)
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")


class DashboardTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.up = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
        threading.Thread(target=cls.up.serve_forever, daemon=True).start()
        up = f"http://127.0.0.1:{cls.up.server_port}"
        cls.tmp = tempfile.TemporaryDirectory()
        cfg_path = Path(cls.tmp.name) / "config.json"
        cfg_path.write_text(json.dumps({
            "timezone": "America/New_York",
            "home": {"address": "100 Example Street, Springfield, ST 00000"},
            "tempest": {"station_id": "123", "token": "TOKEN", "base_url": f"{up}/tempest", "refresh_seconds": 0},
            "calendar": {"calendars": [{"name": "Alex", "color": "#30D158", "ics_url": f"{up}/family.ics"},
                                       {"name": "Birthdays", "color": "#8E8E93", "ics_url": f"{up}/notcal.ics"}]},
            "drive": {"api_key": "KEY", "matrix_url": f"{up}/matrix", "routes_url": f"{up}/routes",
                      "active_hours": None,
                      "places": [{"name": "Office", "icon": "work", "address": "200 Office Park Road, Springfield"},
                                 {"name": "Island", "icon": "pin", "address": "Nowhere"}]},
            "screen": {"mode": "old", "link": "https://old.example/screen/abc"},      # a setting from an older version
        }))
        cls.state = Path(cls.tmp.name) / "state"
        cls.state.mkdir()
        cls.app = server.App(server.Store(cfg_path), WEB, cls.state)
        cls.srv = server.DashboardHTTPServer(("127.0.0.1", 0), server.make_handler(cls.app))
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.srv.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.up.shutdown()
        cls.tmp.cleanup()

    def signed_in(self):
        c = Client(self.base)
        if not self.app.config["admin"]["password_hash"]:
            self.assertEqual(c.call("/admin/api/setup", {"password": "correct horse"})[0], 200)
        else:
            self.assertEqual(c.call("/admin/api/login", {"password": "correct horse"})[0], 200)
        return c

    # --- pages

    def test_pages_served(self):
        c = Client(self.base)
        self.assertIn(b"Family Dashboard", c.call("/")[1])
        self.assertIn(b"admin.js", c.call("/admin/")[1])
        status, _ = c.call("/../server/config.json")
        self.assertIn(status, (403, 404))

    # --- weather / calendar / drive

    def test_weather(self):
        s, w = Client(self.base).call("/api/weather")
        self.assertEqual(s, 200)
        self.assertEqual(w["current"]["temp"], 61.2)
        self.assertEqual(w["current"]["pressure"], 29.7)
        self.assertEqual(w["daily"][0]["date"], dt.datetime.now(TZ).date().isoformat())
        self.assertEqual(len(w["daily"]), 10)

    def test_weather_stale_on_failure(self):
        Client(self.base).call("/api/weather")
        src = self.app.sources["weather"]
        Upstream.fail_tempest = True
        try:
            src.error, src.fetched_at = None, 0
            w = Client(self.base).call("/api/weather")[1]
            self.assertTrue(w["stale"])
            self.assertEqual(w["current"]["temp"], 61.2)
        finally:
            Upstream.fail_tempest = False
            src.error, src.fetched_at = None, 0

    def test_calendar(self):
        s, c = Client(self.base).call("/api/calendar")
        self.assertEqual(s, 200)
        today = dt.datetime.now(TZ).date()
        titles = [e["title"] for e in c["events"]]
        self.assertNotIn("Cancelled thing", titles)
        trip = next(e for e in c["events"] if e["title"] == "No School")
        self.assertEqual((trip["start"], trip["end"]), ((today + dt.timedelta(days=3)).isoformat(), (today + dt.timedelta(days=6)).isoformat()))
        call = next(e for e in c["events"] if e["title"] == "Call with Sam")
        self.assertEqual(dt.datetime.fromisoformat(call["start"]).hour, 19)  # 6 pm Chicago = 7 pm here
        shifts = sorted(dt.datetime.fromisoformat(e["start"]).date() for e in c["events"] if e["title"] == "Shop Shift")
        self.assertNotIn(today - dt.timedelta(days=21) + dt.timedelta(days=28), shifts)
        self.assertTrue(any(d >= today + dt.timedelta(days=13) for d in shifts))
        self.assertEqual(next(e for e in c["events"] if e["title"] == "Shop Shift")["location"], "Corner Market, Springfield")
        # A bad link doesn't hide the good calendar, and the admin sees why
        bad = next(x for x in self.app.status()["calendars"] if x["ok"] is False)
        self.assertIn("Secret address", bad["error"])

    def test_calendar_merges_copies(self):
        """The same event on two calendars is shown once, with both calendars."""
        before = server.copy.deepcopy(self.app.config)
        c = server.copy.deepcopy(before)
        c["calendar"]["calendars"].append({"id": "copy1", "name": "Jordan", "color": "#FF375F", "enabled": True,
                                           "ics_url": before["calendar"]["calendars"][0]["ics_url"]})
        self.app.save(c)
        try:
            cal = Client(self.base).call("/api/calendar")[1]
            trips = [e for e in cal["events"] if e["title"] == "No School"]
            self.assertEqual(len(trips), 1)
            self.assertEqual(trips[0]["calendars"], [before["calendar"]["calendars"][0]["name"], "Jordan"])
            self.assertEqual(trips[0]["colors"][1], "#FF375F")
            starts = [e["start"] for e in cal["events"] if e["title"] == "Shop Shift"]
            self.assertEqual(len(starts), len(set(starts)))
        finally:
            self.app.save(before)

    def test_drive_matrix(self):
        s, d = Client(self.base).call("/api/drive")
        self.assertEqual(s, 200)
        path, headers, body = [p for p in Upstream.posts if p[0] == "/matrix"][-1]
        self.assertEqual(headers["X-Goog-Api-Key"], "KEY")
        self.assertEqual(body["origins"][0]["waypoint"]["address"], "100 Example Street, Springfield, ST 00000")
        office, island = d["places"]
        self.assertEqual((office["minutes"], office["typicalMinutes"]), (32, 30))
        self.assertEqual(island["status"], "no_route")

    def test_route_leave_by(self):
        c = Client(self.base)
        cal = c.call("/api/calendar")[1]
        ev = next(e for e in cal["events"] if e["title"] == "Shop Shift" and dt.datetime.fromisoformat(e["start"]) > dt.datetime.now(TZ))
        q = urllib.parse.urlencode({"to": ev["location"], "arrive": ev["start"]})
        s, r = c.call(f"/api/route?{q}")
        self.assertEqual(s, 200, r)
        self.assertEqual((r["minutes"], r["typicalMinutes"], r["via"]), (25, 20, "US-15 N"))
        self.assertGreater(r["jam"], 100)  # 40.7,-120.95 → 43.252,-126.453 is a few hundred miles
        self.assertEqual(r["slow"], 0)
        leave = dt.datetime.fromisoformat(r["leaveBy"])
        self.assertEqual(dt.datetime.fromisoformat(ev["start"]) - leave, dt.timedelta(minutes=30))  # 25 drive + 5 buffer
        req = [p for p in Upstream.posts if p[0] == "/routes"][-1][2]
        self.assertEqual(req["extraComputations"], ["TRAFFIC_ON_POLYLINE"])
        # Only places on the calendar can be routed
        s, r = c.call("/api/route?to=Disneyland")
        self.assertEqual(s, 403)
        self.assertEqual(c.call("/api/route?to=https%3A%2F%2Fzoom.us%2Fj%2F1")[1]["status"], "no_route")

    def test_maps_limit_keeps_last_answer(self):
        c = Client(self.base)
        cal = c.call("/api/calendar")[1]
        ev = next(e for e in cal["events"] if e["title"] == "Shop Shift" and dt.datetime.fromisoformat(e["start"]) > dt.datetime.now(TZ))
        q = urllib.parse.urlencode({"to": ev["location"], "arrive": ev["start"]})
        first = c.call(f"/api/route?{q}")[1]
        self.assertEqual(first["status"], "ok")
        b = self.app.maps_budget
        saved = dict(b.s)
        posts = len(Upstream.posts)
        try:
            b.s["used"] = b.limit
            with self.app.route_lock:  # make the cached answer old
                for k, (t, v) in list(self.app.routes.items()):
                    self.app.routes[k] = (t - 7200, v)
            again = c.call(f"/api/route?{q}")[1]
            self.assertTrue(again["paused"])
            self.assertEqual(again["minutes"], first["minutes"])
            self.assertEqual(len(Upstream.posts), posts)   # Google wasn't asked
            self.assertEqual(self.app.status()["maps"]["used"], b.limit)
        finally:
            b.s.update(saved)

    def test_stocks(self):
        before = server.copy.deepcopy(self.app.config)
        real = use_fake_yahoo()
        self.app.save(server.apply_settings(before, {"stocks": {"enabled": True, "symbols": "aapl, $SPY zzzz brk.b nasdaq"}}))
        try:
            self.assertEqual(self.app.config["stocks"]["symbols"], ["AAPL", "SPY", "ZZZZ", "BRK-B", "^IXIC"])   # typed the way people type
            s, st = Client(self.base).call("/api/stocks")
            self.assertEqual(s, 200, st)
            self.assertEqual([q["symbol"] for q in st["quotes"]], ["AAPL", "SPY", "BRK-B", "^IXIC"])   # ZZZZ doesn't exist: left out
            aapl, spy, brk, nasdaq = st["quotes"]
            self.assertEqual((aapl["price"], aapl["change"], aapl["percent"]), (230.1, 2.62, 1.15))
            self.assertLess(spy["percent"], 0)
            self.assertEqual((nasdaq["label"], brk["label"]), ("Nasdaq", "BRK-B"))
            self.assertIn("ZZZZ", st["missing"][0])
            self.assertEqual(Client(self.base).call("/api/config")[1]["stocks"], {"enabled": True, "symbols": ["AAPL", "SPY", "ZZZZ", "BRK-B", "^IXIC"]})
            # Yahoo limiting us keeps the last prices on the bar instead of emptying it
            FakeYahoo.rate_limited = True
            src = self.app.sources["stocks"]
            src.fetched_at = 0
            src.error_at = 0
            Client(self.base).call("/api/stocks")                      # starts the refresh in the background, answers with the old prices
            for _ in range(50):
                if not src.refreshing:
                    break
                time.sleep(0.1)
            again = Client(self.base).call("/api/stocks")[1]
            self.assertTrue(again["stale"])
            self.assertIn("limiting", again["error"])
            self.assertEqual(len(again["quotes"]), 4)
            # The Test button
            FakeYahoo.rate_limited = False
            ok = self.signed_in().call("/admin/api/test/stocks", {})[1]
            self.assertTrue(ok["ok"], ok)
            self.assertIn("Yahoo answered", ok["message"])
        finally:
            server.load_yfinance = real
            FakeYahoo.rate_limited = False
            self.app.save(before)

    def test_stock_hiccups_are_retried_in_the_same_round(self):
        real = use_fake_yahoo()
        FakeYahoo.flaky = {"SPY": 1}                      # fails once, like Yahoo's cookie race on a cold start
        try:
            out = server.Stocks({"enabled": True, "symbols": ["AAPL", "SPY", "ZZZZ"]}, None).fetch()
            self.assertEqual([q["symbol"] for q in out["quotes"]], ["AAPL", "SPY"])      # SPY made it into the first round
            self.assertEqual(FakeYahoo.calls.count("SPY"), 2)
            self.assertEqual(FakeYahoo.calls.count("ZZZZ"), 2)                            # a symbol that doesn't exist: tried twice, then left out
            self.assertEqual(FakeYahoo.calls[0], "AAPL")                                  # the session is set up by one request first
            FakeYahoo.calls.clear()
            FakeYahoo.rate_limited = True
            out = server.Stocks({"enabled": True, "symbols": ["AAPL"]}, None)
            with self.assertRaises(server.UpstreamError):
                out.fetch()
            self.assertEqual(FakeYahoo.calls, ["AAPL"])                                   # no second try when Yahoo says slow down
        finally:
            FakeYahoo.flaky, FakeYahoo.rate_limited = {}, False
            server.load_yfinance = real

    def test_stock_prices_never_make_the_tv_wait(self):
        import threading
        before = server.copy.deepcopy(self.app.config)
        real = use_fake_yahoo()
        original = FakeYahoo.PRICES["AAPL"]
        try:
            self.app.save(server.apply_settings(before, {"stocks": {"enabled": True, "symbols": "AAPL"}}))
            self.assertIn("stocks", server.warm_names(self.app))               # warmed in the background from the start
            src = self.app.sources["stocks"]
            first = Client(self.base).call("/api/stocks")[1]
            self.assertEqual(first["quotes"][0]["price"], 230.1)
            FakeYahoo.PRICES["AAPL"] = (231.0, 227.48)                        # Yahoo has a new price, but is slow to say so
            FakeYahoo.gate = threading.Event()
            src.fetched_at = 0                                                 # the cached prices are now out of date
            t = time.time()
            old = Client(self.base).call("/api/stocks")[1]
            self.assertLess(time.time() - t, 2.0)                              # answered at once...
            self.assertEqual(old["quotes"][0]["price"], 230.1)                 # ...with the last prices
            FakeYahoo.gate.set()
            for _ in range(50):
                if not src.refreshing:
                    break
                time.sleep(0.1)
            self.assertEqual(Client(self.base).call("/api/stocks")[1]["quotes"][0]["price"], 231.0)   # the background refresh landed
        finally:
            FakeYahoo.gate = None
            FakeYahoo.PRICES["AAPL"] = original
            server.load_yfinance = real
            self.app.save(before)

    def test_old_finnhub_key_is_dropped_and_never_sent(self):
        c = self.signed_in()
        before = server.copy.deepcopy(self.app.config)
        legacy = server.copy.deepcopy(before)
        legacy["stocks"]["api_key"] = "OLD-FINNHUB-KEY"
        try:
            self.app.save(legacy)
            self.assertNotIn("OLD-FINNHUB-KEY", json.dumps(c.call("/admin/api/settings")[1]))   # even before it is cleaned up
            c.call("/admin/api/settings", {"stocks": {"enabled": False}})
            self.assertNotIn("api_key", self.app.config["stocks"])                               # the next save removes it
        finally:
            self.app.save(before)

    def test_sleep_override_reaches_the_launcher(self):
        c = self.signed_in()
        before = server.copy.deepcopy(self.app.config)
        self.app.save(server.apply_settings(before, {"schedule": {"enabled": False}}))
        try:
            third = lambda: Client(self.base).call("/api/screen-url")[1].decode().splitlines()[2]
            self.assertEqual(third(), "awake")
            s, st = c.call("/admin/api/screen/sleep", {"mode": "sleep", "hours": 1})
            self.assertEqual((s, st["asleep"], st["reason"]), (200, True, "manual"))
            self.assertEqual(third(), "asleep")
            self.assertTrue(c.call("/admin/api/status")[1]["screen"]["sleep"]["asleep"])
            s, st = c.call("/admin/api/screen/sleep", {"mode": "clear"})
            self.assertFalse(st["asleep"])
            self.assertEqual(third(), "awake")
            self.assertEqual(c.call("/admin/api/screen/sleep", {"mode": "sleep", "hours": 99})[0], 400)
            self.assertEqual(c.call("/admin/api/screen/sleep", {"mode": "nap"})[0], 400)
            self.assertEqual(Client(self.base).call("/admin/api/screen/sleep", {"mode": "clear"})[0], 401)
        finally:
            self.app.sleep_override = None
            self.app.save(before)

    def test_stock_search(self):
        c = self.signed_in()
        real = use_fake_yahoo()
        try:
            s, res = c.call("/admin/api/stocks/search?q=apple")
            self.assertEqual(s, 200, res)
            got = [(r["symbol"], r["type"]) for r in res["results"]]
            self.assertEqual(got, [("AAPL", "Stock · NASDAQ"), ("APLE", "Stock · NYSE"), ("VFIAX", "Mutual fund · Nasdaq"),
                                   ("^IXIC", "Index · Nasdaq GIDS"), ("BTC-USD", "Crypto · CCC")])   # futures and junk are left out
            self.assertEqual(res["results"][0]["name"], "Apple Inc.")
            self.assertEqual(c.call("/admin/api/stocks/search?q=a")[1]["results"], [])               # too short to ask Yahoo
            self.assertEqual(Client(self.base).call("/admin/api/stocks/search?q=apple")[0], 401)
        finally:
            server.load_yfinance = real

    def test_schedule_save_does_not_reload_the_tv(self):
        c = self.signed_in()
        before = server.copy.deepcopy(self.app.config)
        v1 = c.call("/api/version")[1]["version"]
        try:
            rules = [{"days": [0, 1, 2, 3, 4, 5, 6], "off": "23:00", "on": "07:00"}]
            self.assertEqual(c.call("/admin/api/settings", {"schedule": {"rules": rules}})[0], 200)
            self.assertEqual(self.app.config["schedule"]["rules"][0]["off"], "23:00")
            self.assertEqual(c.call("/api/version")[1]["version"], v1)          # the TV page isn't asked to reload
            c.call("/admin/api/settings", {"display": {"timeFormat": "24h"}})
            self.assertNotEqual(c.call("/api/version")[1]["version"], v1)       # looks do reload it
        finally:
            self.app.save(before)

    def test_backup_download(self):
        import io
        import tarfile
        c = self.signed_in()
        self.assertEqual(Client(self.base).call("/admin/api/backup")[0], 401)
        req = urllib.request.Request(self.base + "/admin/api/backup", headers={"Cookie": c.cookie})
        with urllib.request.urlopen(req) as r:
            self.assertIn("family-dashboard-backup-", r.headers["Content-Disposition"])
            names = tarfile.open(fileobj=io.BytesIO(r.read()), mode="r:gz").getnames()
        self.assertIn("data/config.json", names)

    # --- Home Assistant (against a fake one)

    def ha_url(self):
        return f"http://127.0.0.1:{self.up.server_port}/ha"

    def ha_connect(self, c, **extra):
        s, res = c.call("/admin/api/settings", {"ha": {"enabled": True, "url": self.ha_url(), "token": HA_TOKEN, **extra}})
        self.assertEqual(s, 200, res)

    def test_ha_connection_and_entities(self):
        c = self.signed_in()
        before = server.copy.deepcopy(self.app.config)
        try:
            self.ha_connect(c)
            ok = c.call("/admin/api/test/ha", {})[1]
            self.assertTrue(ok["ok"], ok)
            self.assertIn("Test House", ok["message"])
            bad = c.call("/admin/api/test/ha", {"ha": {"token": "wrong-token-wrong-token-wrong"}})[1]
            self.assertFalse(bad["ok"])
            self.assertIn("didn't accept", bad["message"])

            s, res = c.call("/admin/api/ha/entities")
            self.assertEqual(s, 200, res)
            by = {e["id"]: e for e in res["entities"]}
            self.assertEqual(by["lock.front_door"]["kind"], "lock")
            self.assertEqual(by["binary_sensor.garage_door"]["kind"], "opening")
            self.assertEqual(by["binary_sensor.front_motion"]["kind"], "motion")
            self.assertEqual(by["camera.front"]["kind"], "camera")
            self.assertEqual(by["alarm_control_panel.house"]["kind"], "alarm")
            self.assertEqual(by["sensor.temp"]["kind"], "sensor")  # everything is offered, not just security items
            self.assertFalse(by["sensor.temp"]["suggested"])
            self.assertTrue(by["lock.front_door"]["suggested"])
            self.assertEqual(res["entities"][0]["suggested"], True)  # suggested ones come first
            self.assertEqual(Client(self.base).call("/admin/api/ha/entities")[0], 401)

            shown = c.call("/admin/api/settings")[1]
            self.assertNotIn(HA_TOKEN, json.dumps(shown))         # the token is never sent back to a browser
            self.assertTrue(shown["ha"]["token"]["set"])
            self.assertTrue(self.app.config["ha"]["token"])
            # Pointing at a different Home Assistant drops the old token
            c.call("/admin/api/settings", {"ha": {"url": "http://elsewhere.example:8123"}})
            self.assertEqual(self.app.config["ha"]["token"], "")
        finally:
            self.app.save(before)

    def test_ha_status_tiles(self):
        c = self.signed_in()
        before = server.copy.deepcopy(self.app.config)
        tiles = [{"id": i, "name": n} for i, n in [
            ("lock.front_door", "Front door"), ("lock.back_door", "Back door"), ("binary_sensor.garage_door", "Garage"),
            ("cover.side_gate", "Side gate"), ("person.alex", "Alex"), ("person.sam", "Sam"), ("alarm_control_panel.house", "Alarm"),
            ("lock.gone", "Removed lock")]]
        try:
            self.ha_connect(c, entities=tiles, alert_minutes=10)
            s, st = Client(self.base).call("/api/home")
            self.assertEqual(s, 200, st)
            it = {i["id"]: i for i in st["items"]}
            self.assertEqual((it["lock.front_door"]["text"], it["lock.front_door"]["tone"]), ("Locked", "good"))
            self.assertEqual((it["lock.back_door"]["text"], it["lock.back_door"]["tone"]), ("Unlocked 20 min", "bad"))
            self.assertEqual((it["binary_sensor.garage_door"]["text"], it["binary_sensor.garage_door"]["icon"]), ("Open 20 min", "garage"))
            self.assertEqual(it["cover.side_gate"]["tone"], "good")
            self.assertEqual((it["person.alex"]["text"], it["person.sam"]["text"]), ("Home", "Away"))
            self.assertEqual(it["alarm_control_panel.house"]["text"], "Armed home")
            self.assertEqual(it["lock.gone"]["text"], "Unavailable")          # an item deleted in Home Assistant doesn't break the rest
            self.assertEqual(st["summary"]["tone"], "bad")
            self.assertIn("Back door", st["summary"]["text"])
            # With the long-open alert off the garage is just "open"; an unlocked door is still a problem
            self.ha_connect(c, alert_minutes=0)
            it = {i["id"]: i for i in Client(self.base).call("/api/home")[1]["items"]}
            self.assertEqual((it["binary_sensor.garage_door"]["text"], it["binary_sensor.garage_door"]["tone"]), ("Open", "warn"))
            self.assertEqual(it["lock.back_door"]["text"], "Unlocked")
        finally:
            self.app.save(before)

    def test_ha_snapshots_and_privacy(self):
        c = self.signed_in()
        before = server.copy.deepcopy(self.app.config)
        cams = [{"id": "camera.front", "name": "Front", "motion": ["binary_sensor.front_motion"]}]
        real = server.is_loopback
        try:
            self.ha_connect(c, cameras=cams, entities=[{"id": "lock.front_door", "name": "Front door"}])
            s, blob = Client(self.base).call("/api/ha/camera/camera.front.jpg")
            self.assertEqual((s, blob), (200, HA_JPEG))
            self.assertEqual(Client(self.base).call("/api/ha/camera/camera.other.jpg")[0], 404)   # only chosen cameras are proxied
            self.assertEqual(Client(self.base).call("/api/ha/camera/..%2F..%2Fconfig.jpg")[0], 404)
            # From another machine: the TV key or a settings session, never "Others can view"
            server.is_loopback = lambda ip: False
            self.app.config["display"]["shareOnNetwork"] = True
            anyone = Client(self.base)
            self.assertEqual(anyone.call("/api/weather")[0], 200)                        # ordinary feeds follow that setting
            for path in ("/api/home", "/api/cameras", "/api/ha/camera/camera.front.jpg"):
                self.assertEqual(anyone.call(path)[0], 401, path)
                self.assertEqual(c.call(path)[0], 200, path)
        finally:
            server.is_loopback = real
            self.app.config["display"]["shareOnNetwork"] = False
            self.app.save(before)

    def test_ha_snapshot_sizes(self):
        c = self.signed_in()
        before = server.copy.deepcopy(self.app.config)
        try:
            self.ha_connect(c, cameras=[{"id": "camera.front", "name": "Front", "motion": []}], entities=[])
            Upstream.proxy_paths.clear()
            self.assertEqual(c.call("/api/ha/camera/camera.front.jpg?w=480")[0], 200)
            self.assertEqual(c.call("/api/ha/camera/camera.front.jpg?w=99999")[0], 200)     # silly sizes mean "full size"
            self.assertEqual(c.call("/api/ha/camera/camera.front.jpg?w=abc")[0], 200)
            asked = [p.split("camera_proxy/")[1] for p in Upstream.proxy_paths]
            self.assertIn("camera.front?width=480&height=270", asked)
            self.assertEqual(sum("width=" in a for a in asked), 1)
            self.assertEqual(sum("width=" not in a for a in asked), 1)                      # both silly ones share one full-size fetch
        finally:
            self.app.save(before)

    def test_assistant_proxy_and_build_handoff(self):
        seen = {"key": None, "applied": None}
        sha = "a" * 40

        class FakeAssistant(BaseHTTPRequestHandler):
            def log_message(self, *a): pass
            def _send(self, obj, code=200):
                body = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            def do_GET(self):
                seen["key"] = self.headers.get("X-Assistant-Key")
                self._send({"signedIn": True, "busy": False, "messages": [], "changes": {"files": []},
                            "apply": {"status": "committed", "hash": sha}})
            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n) or b"{}")
                if self.path == "/applied":
                    seen["applied"] = body.get("hash")
                self._send({"ok": True, "got": body})

        fake = ThreadingHTTPServer(("127.0.0.1", 0), FakeAssistant)
        threading.Thread(target=fake.serve_forever, daemon=True).start()
        old_url = server.ASSISTANT_URL
        keyfile = self.app.assistant_dir / "assistant.key"
        try:
            anon = Client(self.base)
            self.assertEqual(anon.call("/admin/api/assistant/state")[0], 401)          # settings sign-in first
            c = self.signed_in()
            s, r = c.call("/admin/api/assistant/state")
            self.assertEqual((s, r.get("setup")), (503, False))                         # no key: not set up
            keyfile.write_text("secret-key\n")
            server.ASSISTANT_URL = "http://127.0.0.1:1"
            self.assertEqual(c.call("/admin/api/assistant/state")[0], 503)              # set up, but not running
            server.ASSISTANT_URL = f"http://127.0.0.1:{fake.server_port}"
            s, r = c.call("/admin/api/assistant/state")
            self.assertEqual(s, 200)
            self.assertEqual(seen["key"], "secret-key")
            self.assertEqual(r["apply"]["status"], "building")
            self.assertEqual((self.app.assistant_dir / "assistant-build/request").read_text().strip(), sha)
            self.assertEqual(seen["applied"], sha)
            s, r = c.call("/admin/api/assistant/ask", {"text": "hello"})
            self.assertEqual((s, r["got"]["text"]), (200, "hello"))
            self.assertEqual(c.call("/admin/api/assistant/rm-rf", {})[0], 404)          # only the known actions pass
        finally:
            server.ASSISTANT_URL = old_url
            keyfile.unlink(missing_ok=True)
            fake.shutdown()

    def test_ha_snapshot_shared_between_screens(self):
        c = self.signed_in()
        before = server.copy.deepcopy(self.app.config)
        try:
            self.ha_connect(c, cameras=[{"id": "camera.front", "name": "Front", "motion": []}], entities=[])
            self.app.ha.snaps.clear()
            Upstream.proxy_paths.clear()
            codes = []
            ts = [threading.Thread(target=lambda: codes.append(c.call("/api/ha/camera/camera.front.jpg?w=640")[0])) for _ in range(6)]
            [t.start() for t in ts]
            [t.join(10) for t in ts]
            self.assertEqual(codes, [200] * 6)
            self.assertEqual(len(Upstream.proxy_paths), 1)         # six screens at once, one picture from the camera
            time.sleep(server.SNAPSHOT_SHARE + 0.05)
            self.assertEqual(c.call("/api/ha/camera/camera.front.jpg?w=640")[0], 200)
            self.assertEqual(len(Upstream.proxy_paths), 2)         # and a fresh one shortly after, for the next frame
        finally:
            self.app.save(before)

    def test_ha_new_token_switches_it_on(self):
        base = server.deep_merge(server.DEFAULT_CONFIG, {})
        cfg = server.apply_settings(base, {"ha": {"url": "http://ha.example:8123", "token": "x" * 40}})
        self.assertTrue(cfg["ha"]["enabled"])
        off = server.apply_settings(base, {"ha": {"url": "http://ha.example:8123", "token": "x" * 40, "enabled": False}})
        self.assertFalse(off["ha"]["enabled"])

    def test_ha_motion_popup(self):
        c = self.signed_in()
        before = server.copy.deepcopy(self.app.config)
        cams = [{"id": "camera.front", "name": "Front", "motion": ["binary_sensor.front_motion"], "popup": True}]
        again = lambda: (setattr(self.app.sources["cameras"], "fetched_at", 0), Client(self.base).call("/api/cameras")[1])[1]
        try:
            self.ha_connect(c, cameras=cams, popup={"enabled": True, "seconds": 5})
            Upstream.ha_motion = "off"
            self.assertFalse(again()["cameras"][0]["active"])
            Upstream.ha_motion = "on"
            res = again()
            self.assertTrue(res["cameras"][0]["active"])
            self.assertTrue(res["popup"])
            self.assertIsNotNone(res["cameras"][0]["since"])
            Upstream.ha_motion = "off"
            self.assertTrue(again()["cameras"][0]["active"])                          # stays up for the pop-up time
            self.app.ha.motion_last["camera.front"] -= 100                           # ...then goes away
            res = again()
            self.assertFalse(res["cameras"][0]["active"])
            self.assertIsNone(res["cameras"][0]["since"])
            self.assertEqual(Client(self.base).call("/api/config")[1]["ha"], {"status": False, "cameras": True, "popup": True})
        finally:
            Upstream.ha_motion = "off"
            self.app.save(before)

    def test_status_has_info_for_the_about_page(self):
        st = self.signed_in().call("/admin/api/status")[1]
        self.assertEqual(st["info"]["version"], server.APP_VERSION)
        self.assertFalse(st["info"]["offsite"])
        self.assertIn("asleep", st["screen"]["sleep"])

    def test_polyline(self):
        pts = server.decode_polyline(POLY)
        self.assertEqual(pts, [(38.5, -120.2), (40.7, -120.95), (43.252, -126.453)])

    # --- access

    def test_lan_needs_sign_in_unless_shared(self):
        real = server.is_loopback
        server.is_loopback = lambda ip: False
        try:
            self.assertEqual(Client(self.base).call("/api/calendar")[0], 401)
            self.assertEqual(Client(self.base).call("/api/screen-url")[0], 401)
            c = self.signed_in()
            self.assertEqual(c.call("/api/calendar")[0], 200)
            self.assertEqual(c.call("/api/screen-url")[0], 403)  # only the TV itself
        finally:
            server.is_loopback = real

    # --- admin

    def test_admin_flow(self):
        c = self.signed_in()
        self.assertEqual(c.call("/admin/api/settings", {"display": {"timeFormat": "24h"}}, header=False)[0], 403)

        s, st = c.call("/admin/api/settings")
        raw = json.dumps(st)
        for secret in ("TOKEN", "KEY", "family.ics", "old.example/screen", "password_hash"):
            self.assertNotIn(secret, raw)
        self.assertTrue(st["tempest"]["token"]["set"])

        v1 = c.call("/api/version")[1]["version"]
        cals = [{"id": x["id"], "name": x["name"], "color": x["color"], "enabled": x["enabled"]} for x in st["calendar"]["calendars"]]
        cals[0]["name"] = "Alex T."
        s, res = c.call("/admin/api/settings", {"calendar": {"calendars": cals}, "display": {"timeFormat": "24h"}})
        self.assertEqual(s, 200, res)
        self.assertNotEqual(c.call("/api/version")[1]["version"], v1)
        saved = self.app.config["calendar"]["calendars"][0]
        self.assertEqual(saved["name"], "Alex T.")
        self.assertTrue(saved["ics_url"].endswith("/family.ics"))  # empty link on save keeps the old one
        self.assertEqual(self.app.config["tempest"]["token"], "TOKEN")
        self.assertEqual(Client(self.base).call("/api/config")[1]["timeFormat"], "24h")

        s, err = c.call("/admin/api/settings", {"drive": {"places": [{"name": "x", "address": "y"}] * 5}})
        self.assertEqual(s, 400)
        self.assertIn("4", err["error"])

        s, t = c.call("/admin/api/test/weather", {})
        self.assertTrue(t["ok"], t)
        s, t = c.call("/admin/api/test/calendar", {"ics_url": f"http://127.0.0.1:{self.up.server_port}/notcal.ics"})
        self.assertFalse(t["ok"])

    def test_login_lockout(self):
        self.signed_in()
        c = Client(self.base)
        for _ in range(5):
            self.assertEqual(c.call("/admin/api/login", {"password": "nope"})[0], 401)
        self.assertEqual(c.call("/admin/api/login", {"password": "correct horse"})[0], 429)
        self.app.sessions.failures.clear()

    # --- the TV screen

    def test_screen_url_and_reload(self):
        url, nonce, sleeping = Client(self.base).call("/api/screen-url")[1].decode().splitlines()
        self.assertEqual(url, f"http://localhost:{self.app.config.get('port', 8080)}/?mode=live")
        self.assertIn(sleeping, ("asleep", "awake"))
        c = self.signed_in()
        self.assertNotIn("screen", c.call("/admin/api/settings")[1])                  # an older version's setting is never offered
        self.assertTrue(c.call("/admin/api/screen/reload", {})[1]["restarted"])       # the launcher checked in just now
        nonce2 = Client(self.base).call("/api/screen-url")[1].decode().splitlines()[1]
        self.assertNotEqual(nonce, nonce2)
        self.app.kiosk_seen = 0
        self.assertFalse(c.call("/admin/api/screen/reload", {})[1]["restarted"])
        before = server.copy.deepcopy(self.app.config)
        try:
            self.app.save(server.apply_settings(self.app.config, {"schedule": {"enabled": True}}))
            self.assertNotIn("screen", self.app.config)                                 # dropped from disk on the next save
        finally:
            self.app.save(before)

    def test_google_account_calendars(self):
        c = self.signed_in()
        self.app.config["calendar"]["account"]["base_url"] = f"http://127.0.0.1:{self.up.server_port}/calendar/dav"
        s, err = c.call("/admin/api/calendar/refresh", {"email": "parent@example.com", "app_password": "wxyz wxyz wxyz wxyz"})
        self.assertEqual(s, 502)
        self.assertIn("didn't accept", err["error"])
        s, res = c.call("/admin/api/calendar/refresh", {"app_password": "abcd abcd abcd abcd"})
        self.assertEqual(s, 200, res)
        self.assertEqual((res["found"], res["added"]), (2, 2))
        google = [x for x in res["settings"]["calendar"]["calendars"] if x["source"] == "google"]
        self.assertEqual([x["name"] for x in google], ["Family", "Work"])
        self.assertEqual(google[0]["color"], "#9FE1E7")
        self.assertNotIn("abcd", json.dumps(res["settings"]))

        # Turn one off and rename the other; a refresh keeps those choices
        cals = [{"id": x["id"], "name": x["name"], "color": x["color"], "enabled": x["enabled"]} for x in res["settings"]["calendar"]["calendars"]]
        cals[0]["name"], cals[1]["enabled"] = "Family", False
        self.assertEqual(c.call("/admin/api/settings", {"calendar": {"calendars": cals}})[0], 200)
        Upstream.dav_calendars = ["family", "work", "soccer"]
        try:
            res = c.call("/admin/api/calendar/refresh", {})[1]
        finally:
            Upstream.dav_calendars = ["family", "work"]
        self.assertEqual(res["added"], 1)
        by = {x["name"]: x for x in res["settings"]["calendar"]["calendars"]}
        self.assertTrue(by["Family"]["enabled"])
        self.assertFalse(by["Work"]["enabled"])
        self.assertTrue(by["Soccer"]["enabled"])

        cal = c.call("/api/calendar")[1]
        mine = [e for e in cal["events"] if e["calendar"] == "Family"]
        self.assertTrue(any(e["title"] == "No School" for e in mine))
        self.assertFalse(any(e["calendar"] == "Work" for e in cal["events"]))
        # put things back for other tests
        restored = server.copy.deepcopy(self.app.config)
        restored["calendar"]["calendars"] = [x for x in restored["calendar"]["calendars"] if not x.get("href")]
        self.app.save(restored)

    def test_photos(self):
        c = self.signed_in()
        jpg = b"\xff\xd8\xff\xe0fakejpeg"
        req = urllib.request.Request(self.base + "/admin/api/photos/upload?name=beach.JPG", data=jpg,
                                     headers={"X-Dashboard": "1", "Cookie": c.cookie, "Content-Type": "image/jpeg"})
        with urllib.request.urlopen(req) as r:
            name = json.loads(r.read())["name"]
        self.assertTrue(name.endswith(".jpg"))
        self.assertEqual(Client(self.base).call("/api/photos")[1]["photos"], [name])
        self.assertEqual(Client(self.base).call(f"/photos/{name}")[1], jpg)
        self.assertEqual(Client(self.base).call("/photos/..%2Fconfig.json")[0], 404)
        bad = urllib.request.Request(self.base + "/admin/api/photos/upload?name=x.exe", data=b"MZ",
                                     headers={"X-Dashboard": "1", "Cookie": c.cookie})
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(bad)
        self.assertEqual(ctx.exception.code, 400)
        self.assertEqual(c.call("/admin/api/photos/delete", {"name": name})[1]["photos"], [])


class OffsiteTest(unittest.TestCase):
    """Off-site, the proxy is localhost: localhost earns nothing, the TV key does."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        cfg = Path(self.tmp.name) / "config.json"
        cfg.write_text(json.dumps({}))
        (Path(self.tmp.name) / "state").mkdir()
        self.app = server.App(server.Store(cfg), WEB, Path(self.tmp.name) / "state", offsite=True)
        self.srv = server.DashboardHTTPServer(("127.0.0.1", 0), server.make_handler(self.app))
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.srv.server_port}"

    def tearDown(self):
        self.srv.shutdown()
        self.tmp.cleanup()

    def get(self, path, headers=None):
        req = urllib.request.Request(self.base + path, headers=headers or {})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    def test_tv_key(self):
        self.assertEqual(self.get("/api/config")[0], 401)
        self.assertEqual(self.get("/api/screen-url")[0], 401)
        key = self.app.new_tv_key()
        self.assertNotIn(key, json.dumps(self.app.config))     # only its hash is kept
        self.assertEqual(self.get("/api/screen-url", {"Authorization": "Bearer nope"})[0], 401)
        s, body = self.get("/api/screen-url", {"Authorization": f"Bearer {key}"})
        self.assertEqual((s, body.decode().splitlines()[0]), (200, "/?mode=live"))
        self.assertTrue(self.app.kiosk_online())

        # The TV's browser trades the key for a cookie
        c = Client(self.base)
        self.assertEqual(c.call("/api/tv/pair", {"key": "wrong-key-wrong-key"})[0], 401)
        s, _ = c.call("/api/tv/pair", {"key": key})
        self.assertEqual(s, 200)
        self.assertTrue(c.cookie.startswith("fd_tv="))
        self.assertNotIn("Secure", c.raw_cookie)   # plain http in the house: a Secure cookie would be dropped
        self.assertEqual(c.call("/api/config")[0], 200)
        self.assertIn(b"TV key", c.call("/tv/")[1])

        # A second screen gets its own key; the first keeps working
        second = self.app.new_tv_key("Fire TV")
        self.assertNotEqual(key, second)
        self.assertEqual(c.call("/api/config")[0], 200)
        c2 = Client(self.base)
        self.assertEqual(c2.call("/api/tv/pair", {"key": second})[0], 200)
        self.assertEqual(c2.call("/api/config")[0], 200)
        names = [d["name"] for d in server.public_settings(self.app.config)["tv"]["devices"]]
        self.assertEqual(names, ["TV", "Fire TV"])
        self.assertNotIn(second, json.dumps(server.public_settings(self.app.config)))

        # Stopping one screen leaves the other
        dev = server.public_settings(self.app.config)["tv"]["devices"][1]["id"]
        self.app.revoke_tv_key(dev)
        self.assertEqual(c2.call("/api/config")[0], 401)
        self.assertEqual(c.call("/api/config")[0], 200)

    def test_each_screen_can_have_its_own_layout(self):
        shared = {"version": 1, "widgets": [{"type": "clock", "x": 0, "y": 0, "w": 300, "h": 100}]}
        mine = {"version": 1, "widgets": [{"type": "clock", "x": 500, "y": 40, "w": 400, "h": 120}]}
        self.app.save(server.apply_settings(self.app.config, {"layout": shared}))
        a, b = self.app.new_tv_key("Pi"), self.app.new_tv_key("Fire TV")
        ids = {d["name"]: d["id"] for d in server.public_settings(self.app.config)["tv"]["devices"]}

        def layout(key):
            return self.app.display_config(key)["layout"]["widgets"][0]["x"]
        self.assertEqual((layout(a), layout(b), layout(None)), (0, 0, 0))     # everyone follows the shared layout
        self.app.set_tv_layout(ids["Fire TV"], mine)
        self.assertEqual((layout(a), layout(b), layout(None)), (0, 500, 0))   # only the Fire TV changes
        s, body = self.get("/api/config", {"Authorization": f"Bearer {b}"})   # and it is what that screen is sent
        self.assertEqual(json.loads(body)["layout"]["widgets"][0]["x"], 500)
        with self.assertRaises(ValueError):
            self.app.set_tv_layout(ids["Fire TV"], {"widgets": [{"type": "clock", "x": 1900, "y": 0, "w": 400, "h": 100}]})
        with self.assertRaises(ValueError):
            self.app.set_tv_layout("nobody", mine)
        self.app.set_tv_layout(ids["Fire TV"], None)                          # back to following the shared one
        self.assertEqual(layout(b), 0)

    def test_first_key_becomes_an_ordinary_screen(self):
        cfg = server.deep_merge(server.DEFAULT_CONFIG, {"tv": {"key_hash": server.key_hash("old-key-old-key-old-key")}})
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "old.json"
            path.write_text(json.dumps(cfg))
            store = server.Store(path)
        self.assertEqual(store.data["tv"]["key_hash"], "")
        self.assertEqual([d["id"] for d in store.data["tv"]["devices"]], ["first"])

    def test_lockout_uses_forwarded_address(self):
        self.app.new_tv_key()
        for i in range(5):
            req = urllib.request.Request(self.base + "/api/tv/pair", data=b'{"key":"x"}',
                                         headers={"X-Dashboard": "1", "X-Forwarded-For": "client-a"})
            with self.assertRaises(urllib.error.HTTPError):
                urllib.request.urlopen(req)
        self.assertTrue(self.app.sessions.locked_out("client-a"))   # the test client is loopback: a proxy here
        self.assertFalse(self.app.sessions.locked_out("127.0.0.1"))


class MapsBudgetTest(unittest.TestCase):
    def test_cap_and_spread(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "maps.json"
            b = server.MapsBudget(path, 3, TZ)
            self.assertGreater(b.allowance(), 0)
            for _ in range(3):
                b.spend()
            self.assertFalse(b.can_spend())
            with self.assertRaises(server.UpstreamError):
                b.spend()
            self.assertEqual(server.MapsBudget(path, 3, TZ).status()["used"], 3)   # survives a restart
            b.s["month"] = "1999-01"                                               # a new month starts fresh
            self.assertTrue(b.can_spend())
            # 900 a month: a day may use what's left divided by the days left
            b = server.MapsBudget(Path(d) / "m2.json", 900, TZ)
            today = dt.datetime.now(TZ).date()
            days_left = ((today.replace(day=28) + dt.timedelta(days=4)).replace(day=1) - today).days
            self.assertAlmostEqual(b.allowance(), 900 / days_left)


class SleepScheduleTest(unittest.TestCase):
    """2026-11-23 is a Monday."""
    RULES = {"enabled": True, "rules": [
        {"days": [0, 1, 2, 3, 4], "off": "22:00", "on": "06:45", "enabled": True},
        {"days": [5, 6], "off": "23:30", "on": "08:30", "enabled": True}]}

    def at(self, day, h, m=0):
        return dt.datetime(2026, 11, day, h, m, tzinfo=TZ)

    def check(self, when, asleep, change, sched=None, override=None):
        st = server.sleep_status(sched or self.RULES, when, override)
        self.assertEqual(st["asleep"], asleep, when)
        self.assertEqual(dt.datetime.fromisoformat(st["change"]) if st["change"] else None, change, when)
        return st

    def test_weekday_and_weekend_nights(self):
        self.check(self.at(23, 12), False, self.at(23, 22))              # Monday noon: next sleep tonight
        self.check(self.at(23, 23), True, self.at(24, 6, 45))            # Monday night
        self.check(self.at(24, 6), True, self.at(24, 6, 45))             # still asleep at 6 on Tuesday
        self.check(self.at(24, 6, 45), False, self.at(24, 22))           # wakes on the dot
        self.check(self.at(27, 22, 30), True, self.at(28, 6, 45))        # Friday night ends Saturday morning
        self.check(self.at(28, 12), False, self.at(28, 23, 30))          # Saturday uses the later weekend time
        self.check(self.at(28, 23, 45), True, self.at(29, 8, 30))
        self.check(self.at(29, 23, 45), True, self.at(30, 8, 30))        # Sunday night runs into Monday morning
        self.check(self.at(30, 7), True, self.at(30, 8, 30))

    def test_touching_rules_are_one_stretch(self):
        sched = {"enabled": True, "rules": [{"days": [0], "off": "22:00", "on": "23:00", "enabled": True},
                                           {"days": [0], "off": "23:00", "on": "06:00", "enabled": True}]}
        self.check(self.at(23, 22, 30), True, self.at(24, 6), sched)

    def test_off_switches_and_overrides(self):
        self.check(self.at(23, 23), False, None, {"enabled": False, "rules": self.RULES["rules"]})
        one_off = {"enabled": True, "rules": [dict(self.RULES["rules"][0], enabled=False)]}
        self.check(self.at(23, 23), False, None, one_off)
        now = self.at(23, 12)
        future = {"mode": "sleep", "until": (now + dt.timedelta(hours=2)).timestamp()}
        st = self.check(now, True, now + dt.timedelta(hours=2), override=future)
        self.assertEqual(st["reason"], "manual")
        wake = {"mode": "wake", "until": (self.at(23, 22, 30)).timestamp()}
        self.check(self.at(23, 22, 15), False, self.at(23, 22, 30), override=wake)        # awake past the scheduled bedtime
        expired = {"mode": "sleep", "until": (now - dt.timedelta(minutes=1)).timestamp()}
        self.check(now, False, self.at(23, 22), override=expired)                          # an old override is ignored


class MergeCopiesTest(unittest.TestCase):
    def test_merge(self):
        ev = lambda cal, title, start="2026-11-24T19:00:00-05:00": {"allDay": False, "start": start, "end": "2026-11-24T19:30:00-05:00",
                                                                     "title": title, "calendar": cal, "color": "#000000", "location": ""}
        out = server.merge_copies([ev("Parent", "Take out the trash"), ev("Casey", "Take out  the Trash"),
                                   ev("Riley", "Take out the trash"), ev("Riley", "Take out the trash", "2026-11-25T19:00:00-05:00")])
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0]["calendars"], ["Parent", "Casey", "Riley"])
        self.assertEqual(out[0]["calendar"], "Parent")


class HADescribeTest(unittest.TestCase):
    def say(self, eid, state, attrs=None, minutes=0, alert=0):
        kind = server.ha_kind(eid, attrs)
        return server.ha_describe(kind, state, minutes, alert, attrs, eid.split(".")[0])

    def test_wording(self):
        d = self.say("light.kitchen", "on")
        self.assertEqual((d["text"], d["icon"]), ("On", "bulb"))
        self.assertEqual(self.say("switch.fan", "off")["tone"], "neutral")
        self.assertEqual(self.say("sensor.temp", "21.0", {"unit_of_measurement": "°F"})["text"], "21°F")
        low = self.say("sensor.phone", "12", {"device_class": "battery", "unit_of_measurement": "%"})
        self.assertEqual((low["text"], low["tone"]), ("Low 12%", "warn"))
        self.assertEqual(self.say("binary_sensor.leak", "on", {"device_class": "moisture"})["tone"], "bad")
        self.assertEqual(self.say("binary_sensor.smoke", "off", {"device_class": "smoke"})["text"], "OK")
        self.assertEqual(self.say("climate.hall", "heat", {"current_temperature": 68, "hvac_action": "heating"})["text"], "68° · Heating")
        self.assertEqual(self.say("device_tracker.phone", "home")["text"], "Home")
        self.assertEqual(self.say("media_player.tv", "playing")["text"], "Playing")
        self.assertEqual(self.say("light.kitchen", "unavailable")["text"], "Unavailable")

    def test_every_icon_exists_on_the_tv(self):
        js = (Path(__file__).resolve().parent.parent / "web" / "app.js").read_text(encoding="utf-8")
        for kind, eid, attrs in [("power", "light.a", {}), ("power", "switch.a", {}), ("climate", "climate.a", {}), ("media", "media_player.a", {}),
                                 ("sensor", "sensor.a", {}), ("binary", "binary_sensor.a", {"device_class": "moisture"}),
                                 ("binary", "binary_sensor.b", {"device_class": "smoke"})]:
            for state in ("on", "off", "21"):
                icon = server.ha_describe(kind, state, 0, 0, attrs, eid.split(".")[0])["icon"]
                self.assertIn(f"    {icon}: '", js, icon)


class SettingsValidationTest(unittest.TestCase):
    base = server.deep_merge(server.DEFAULT_CONFIG, {})

    def bad(self, patch, words):
        with self.assertRaises(ValueError) as ctx:
            server.apply_settings(self.base, patch)
        self.assertIn(words, str(ctx.exception))

    def test_calendar_first_day(self):
        self.assertEqual(self.base["display"]["calStart"], "today")
        c = server.apply_settings(self.base, {"display": {"calStart": "sunday"}})
        self.assertEqual(c["display"]["calStart"], "sunday")
        self.bad({"display": {"calStart": "monday"}}, "First day shown")

    def test_rejects(self):
        self.bad({"calendar": {"calendars": [{"name": "A", "color": "red"}]}}, "colour")
        self.bad({"calendar": {"calendars": [{"name": "A", "ics_url": "file:///etc/passwd"}]}}, "web link")
        self.bad({"tempest": {"station_id": "abc"}}, "Station ID")
        self.bad({"timezone": "Mars/Base"}, "time zone")
        self.bad({"drive": {"active_hours": ["25:00", "06:00"]}}, "06:30")
        self.bad({"calendar": {"account": {"app_password": "password123"}}}, "16 letters")
        self.bad({"photos": {"folder": "relative/path"}}, "full path")
        self.bad({"stocks": {"symbols": ["AAPL", "rm -rf"]}}, "ticker")
        self.bad({"stocks": {"symbols": [f"T{i}" for i in range(31)]}}, "30")
        self.bad({"stocks": {"refresh_seconds": 5}}, "Stock refresh")
        self.bad({"drive": {"monthly_limit": -1}}, "Monthly")
        self.bad({"schedule": {"rules": [{"days": [], "off": "22:00", "on": "06:45"}]}}, "at least one day")
        self.bad({"schedule": {"rules": [{"days": [7], "off": "22:00", "on": "06:45"}]}}, "at least one day")
        self.bad({"schedule": {"rules": [{"days": [1], "off": "22:00", "on": "22:00"}]}}, "same moment")
        self.bad({"schedule": {"rules": [{"days": [1], "off": "25:00", "on": "06:45"}]}}, "06:30")
        self.bad({"schedule": {"rules": [{"days": [1], "off": "22:00", "on": "06:45"}] * 15}}, "14")
        self.bad({"display": {"bgColor": "navy"}}, "#0B1626")
        self.bad({"display": {"background": "video"}}, "Background")
        self.bad({"display": {"bgPhoto": "../../etc/passwd"}}, "photo from the list")
        self.bad({"display": {"photoDim": 95}}, "darkening")
        self.bad({"display": {"bgGradient": {"a": "#000000", "b": "blue"}}}, "#0B1626")

    def test_webcal_becomes_https(self):
        c = server.apply_settings(self.base, {"calendar": {"calendars": [{"name": "A", "ics_url": "webcal://x.com/a.ics"}]}})
        self.assertEqual(c["calendar"]["calendars"][0]["ics_url"], "https://x.com/a.ics")

    def test_schedule_and_background_are_saved_clean(self):
        c = server.apply_settings(self.base, {
            "schedule": {"rules": [{"days": [4, 5, 5], "off": "23:00", "on": "08:00"}]},
            "display": {"background": "gradient", "bgColor": "#0b1626", "appearance": "dark", "photoDim": 20, "bgPhoto": "a1.jpg",
                        "bgGradient": {"a": "#112233", "b": "#445566", "angle": 45}}})
        r = c["schedule"]["rules"][0]
        self.assertEqual((r["days"], r["enabled"], len(r["id"]) >= 1), ([4, 5], True, True))
        self.assertEqual((c["display"]["bgColor"], c["display"]["bgGradient"]["angle"], c["display"]["bgPhoto"]), ("#0B1626", 45, "a1.jpg"))

    def test_camera_wall_options(self):
        box = lambda **kw: {"type": "camwall", "x": 40, "y": 40, "w": 400, "h": 300, **kw}
        ok = server.apply_settings(self.base, {"layout": {"widgets": [box(options={"cameras": ["camera.a", "camera.b"]})]}})
        self.assertEqual(ok["layout"]["widgets"][0]["options"], {"cameras": ["camera.a", "camera.b"]})
        self.bad({"layout": {"widgets": [box(options={"cameras": ["camera.a"] * 5})]}}, "four cameras")
        self.bad({"layout": {"widgets": [box(options={"cameras": ["lock.front"]})]}}, "four cameras")

    def test_home_assistant_settings_are_checked(self):
        self.bad({"ha": {"url": "ftp://x"}}, "http")
        self.bad({"ha": {"token": "short"}}, "long-lived")
        self.bad({"ha": {"entities": [{"id": "automation.kitchen"}]}}, "can't be shown")
        self.bad({"ha": {"entities": [{"id": "Lock.Front"}]}}, "isn't a Home Assistant item")
        self.bad({"ha": {"entities": [{"id": "lock.a"}] * 25}}, "24")
        self.bad({"ha": {"cameras": [{"id": "lock.front_door"}]}}, "can't be shown")
        self.bad({"ha": {"cameras": [{"id": f"camera.c{i}"} for i in range(9)]}}, "8")
        self.bad({"ha": {"cameras": [{"id": "camera.a", "motion": ["lock.x"]}]}}, "can't be shown")
        self.bad({"ha": {"cameras": [{"id": "camera.a", "motion": ["binary_sensor.a"] * 4}]}}, "3 motion")
        self.bad({"ha": {"popup": {"seconds": 1}}}, "Pop-up time")
        self.bad({"layout": {"widgets": [{"type": "camera", "x": 0, "y": 0, "w": 400, "h": 200, "options": {"camera": "lock.x"}}]}}, "camera from the list")
        c = server.apply_settings(self.base, {"ha": {"url": "http://ha.example:8123/", "token": "x" * 40,
                                                      "cameras": [{"id": "camera.a", "name": "A", "motion": ["binary_sensor.m"]}]}})
        self.assertEqual((c["ha"]["url"], c["ha"]["cameras"][0]["popup"]), ("http://ha.example:8123", True))
        self.assertEqual(server.apply_settings(c, {"ha": {"url": "http://other.example:8123"}})["ha"]["token"], "")

    def test_ticker_names_are_what_yahoo_calls_them(self):
        n = server.normalize_symbol
        self.assertEqual([n(x) for x in ("aapl", "$MSFT", "BRK.B", "bf.b", "NASDAQ", "SP500", "^gspc", "SAP.DE", "BTC-USD", "EURUSD=X")],
                         ["AAPL", "MSFT", "BRK-B", "BF-B", "^IXIC", "^GSPC", "^GSPC", "SAP.DE", "BTC-USD", "EURUSD=X"])
        for ok in ("AAPL", "BRK-B", "^GSPC", "SAP.DE", "BTC-USD", "EURUSD=X", "GC=F"):
            self.assertTrue(server.SYMBOL.match(ok), ok)
        for bad in ("rm -rf", "", "A" * 20, "AA PL", "drop;table"):
            self.assertFalse(server.SYMBOL.match(bad), bad)
        self.assertEqual([server.stock_label(x) for x in ("^IXIC", "^GSPC", "BTC-USD", "AAPL", "^TNX")], ["Nasdaq", "S&P 500", "BTC", "AAPL", "TNX"])

    def test_home_assistant_wording(self):
        d = server.ha_describe
        self.assertEqual(d("lock", "locked", 5, 10)["tone"], "good")
        self.assertEqual((d("lock", "unlocked", 3, 10)["text"], d("lock", "unlocked", 3, 10)["tone"]), ("Unlocked", "bad"))
        self.assertEqual(d("lock", "unlocked", 130, 10)["text"], "Unlocked 2 h")
        self.assertEqual(d("lock", "jammed", 0, 10)["tone"], "bad")
        self.assertEqual((d("opening", "on", 4, 10)["text"], d("opening", "on", 4, 10)["tone"]), ("Open", "warn"))
        self.assertEqual(d("opening", "on", 15, 10)["tone"], "bad")
        self.assertEqual(d("opening", "off", 15, 10)["text"], "Closed")
        self.assertEqual(d("alarm", "armed_away", 0, 10)["text"], "Armed away")
        self.assertEqual(d("alarm", "triggered", 0, 10)["tone"], "bad")
        self.assertEqual(d("person", "home", 0, 10)["text"], "Home")
        self.assertEqual(d("lock", "unavailable", 0, 10)["text"], "Unavailable")

    def test_layout(self):
        box = lambda t, **kw: {"type": t, "x": 40, "y": 40, "w": 400, "h": 200, **kw}
        ok = server.apply_settings(self.base, {"layout": {"widgets": [
            box("clock", scale="auto"), box("calendar", scale=1.2, options={"days": 21}), box("ticker", options={"speed": 120}, visible=False)]}})
        w = {x["type"]: x for x in ok["layout"]["widgets"]}
        self.assertEqual((w["calendar"]["options"], w["calendar"]["scale"]), ({"days": 21}, 1.2))
        self.assertEqual((w["ticker"]["visible"], w["ticker"]["options"]), (False, {"speed": 120}))
        self.assertEqual(w["clock"]["scale"], "auto")
        self.assertIsNone(server.apply_settings(ok, {"layout": None})["layout"])      # reset to the built-in arrangement
        self.bad({"layout": {"widgets": [box("clock", x=1700)]}}, "doesn't fit")
        self.bad({"layout": {"widgets": [box("clock", w=20)]}}, "width")
        self.bad({"layout": {"widgets": [box("clock"), box("clock")]}}, "twice")
        self.bad({"layout": {"widgets": [box("toaster")]}}, "Widget")
        self.bad({"layout": {"widgets": [box("calendar", options={"days": 9})]}}, "Days shown")
        self.bad({"layout": {"widgets": [box("clock", scale=9)]}}, "text size")
        self.bad({"layout": {"widgets": "nope"}}, "list its widgets")

    def test_passwords(self):
        h = server.hash_password("correct horse")
        self.assertTrue(server.check_password("correct horse", h))
        self.assertFalse(server.check_password("wrong", h))
        self.assertFalse(server.check_password("x", "garbage"))


if __name__ == "__main__":
    unittest.main()
