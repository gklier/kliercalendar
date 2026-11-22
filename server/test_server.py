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


DAV_HOME = "/calendar/dav/parent%40example.com/user/"


def multistatus(*responses):
    return ('<?xml version="1.0"?><D:multistatus xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav" '
            'xmlns:A="http://apple.com/ns/ical/">' + "".join(responses) + "</D:multistatus>").encode()


class Upstream(BaseHTTPRequestHandler):
    posts = []
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
        if self.path.startswith("/search"):
            q = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            assert q["token"] == ["FINN"]
            rows = [("APPLE INC", "AAPL", "Common Stock"), ("APPLE HOSPITALITY REIT", "APLE", "REIT"),
                    ("APPLE INC", "APC.DE", "Common Stock"), ("AMERICAN FUNDS INVESTMENT CO OF AMERICA A", "AIVSX", "Open-End Fund"),
                    ("BERKSHIRE HATHAWAY INC-CL B", "BRK.B", "Common Stock"), ("junk", "not a ticker!", "")]
            return self._reply(200, json.dumps({"count": len(rows), "result": [
                {"description": d, "displaySymbol": sy, "symbol": sy, "type": t} for d, sy, t in rows]}).encode())
        if self.path.startswith("/finnhub"):
            q = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            assert q["token"] == ["FINN"]
            quotes = {"AAPL": {"c": 230.1, "d": 2.62, "dp": 1.1517, "pc": 227.48, "t": 1},
                      "SPY": {"c": 590.0, "d": -7.4, "dp": -1.2387, "pc": 597.4, "t": 1}}
            return self._reply(200, json.dumps(quotes.get(q["symbol"][0], {"c": 0, "d": None, "dp": None, "pc": 0})).encode())
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
            "screen": {"mode": "dakboard", "dakboard_url": "https://dakboard.com/screen/abc"},
        }))
        cls.state = Path(cls.tmp.name) / "state"
        cls.state.mkdir()
        cls.app = server.App(server.Store(cfg_path), WEB, cls.state)
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), server.make_handler(cls.app))
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
        self.app.save(server.apply_settings(before, {"stocks": {"enabled": True, "api_key": "FINN", "symbols": "aapl, $SPY zzzz"}}))
        self.app.config["stocks"]["base_url"] = f"http://127.0.0.1:{self.up.server_port}/finnhub"
        try:
            s, st = Client(self.base).call("/api/stocks")
            self.assertEqual(s, 200, st)
            self.assertEqual([q["symbol"] for q in st["quotes"]], ["AAPL", "SPY"])  # ZZZZ has no price: left out
            aapl, spy = st["quotes"]
            self.assertEqual((aapl["price"], aapl["change"], aapl["percent"]), (230.1, 2.62, 1.15))
            self.assertLess(spy["percent"], 0)
            self.assertIn("ZZZZ", st["missing"][0])
            self.assertEqual(Client(self.base).call("/api/config")[1]["stocks"], {"enabled": True, "symbols": ["AAPL", "SPY", "ZZZZ"]})
            settings = self.signed_in().call("/admin/api/settings")[1]
            self.assertNotIn("FINN", json.dumps(settings))
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
        before = server.copy.deepcopy(self.app.config)
        s, err = c.call("/admin/api/stocks/search?q=apple")
        self.assertEqual(s, 502)
        self.assertIn("Finnhub key", err["error"])
        self.app.save(server.apply_settings(before, {"stocks": {"api_key": "FINN"}}))
        self.app.config["stocks"]["search_url"] = f"http://127.0.0.1:{self.up.server_port}/search"
        try:
            s, res = c.call("/admin/api/stocks/search?q=apple")
            self.assertEqual(s, 200, res)
            got = [(r["symbol"], r["free"]) for r in res["results"]]
            self.assertEqual(got, [("AAPL", True), ("APLE", True), ("BRK.B", True), ("AIVSX", False), ("APC.DE", False)])
            self.assertEqual(res["results"][0]["name"], "APPLE INC")
            self.assertEqual(c.call("/admin/api/stocks/search?q=a")[1]["results"], [])   # too short to ask Finnhub
            self.assertEqual(Client(self.base).call("/admin/api/stocks/search?q=apple")[0], 401)
        finally:
            self.app.save(before)

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
        for secret in ("TOKEN", "KEY", "family.ics", "dakboard.com/screen", "password_hash"):
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
        self.assertEqual(url, "https://dakboard.com/screen/abc")
        self.assertIn(sleeping, ("asleep", "awake"))
        c = self.signed_in()
        s, res = c.call("/admin/api/settings", {"screen": {"mode": "dashboard"}})
        self.assertTrue(res["screen"]["restarted"])  # the launcher checked in just now
        url2, nonce2, _ = Client(self.base).call("/api/screen-url")[1].decode().splitlines()
        self.assertIn("localhost", url2)
        self.assertNotEqual(nonce, nonce2)
        self.app.kiosk_seen = 0
        self.assertFalse(c.call("/admin/api/screen/reload", {})[1]["restarted"])
        self.app.save(server.apply_settings(self.app.config, {"screen": {"mode": "dakboard"}}))

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
        cfg.write_text(json.dumps({"screen": {"mode": "dashboard", "dakboard_url": "https://dakboard.com/screen/abc"}}))
        (Path(self.tmp.name) / "state").mkdir()
        self.app = server.App(server.Store(cfg), WEB, Path(self.tmp.name) / "state", offsite=True)
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), server.make_handler(self.app))
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

        # A new key retires the old one
        self.app.new_tv_key()
        self.assertEqual(c.call("/api/config")[0], 401)

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


class SettingsValidationTest(unittest.TestCase):
    base = server.deep_merge(server.DEFAULT_CONFIG, {})

    def bad(self, patch, words):
        with self.assertRaises(ValueError) as ctx:
            server.apply_settings(self.base, patch)
        self.assertIn(words, str(ctx.exception))

    def test_rejects(self):
        self.bad({"calendar": {"calendars": [{"name": "A", "color": "red"}]}}, "colour")
        self.bad({"calendar": {"calendars": [{"name": "A", "ics_url": "file:///etc/passwd"}]}}, "web link")
        self.bad({"tempest": {"station_id": "abc"}}, "Station ID")
        self.bad({"timezone": "Mars/Base"}, "time zone")
        self.bad({"drive": {"active_hours": ["25:00", "06:00"]}}, "06:30")
        self.bad({"screen": {"mode": "netflix"}}, "TV shows")
        self.bad({"calendar": {"account": {"app_password": "password123"}}}, "16 letters")
        self.bad({"photos": {"folder": "relative/path"}}, "full path")
        self.bad({"stocks": {"symbols": ["AAPL", "rm -rf"]}}, "ticker")
        self.bad({"stocks": {"symbols": [f"T{i}" for i in range(31)]}}, "30")
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
