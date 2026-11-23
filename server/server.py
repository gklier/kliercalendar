#!/usr/bin/env python3
"""Family Dashboard server.

Runs on the Pi behind the TV. It serves two pages and keeps every key and
private calendar link on the Pi:

  /         The TV dashboard
  /admin/   Settings for the family (password protected)

Data the TV reads:
  /api/weather    Tempest better_forecast, normalised
  /api/calendar   Google Calendar secret iCal links, expanded
  /api/drive      Always-on drive times (Google Routes route matrix)
  /api/route      Leave-by time for one calendar event's location
  /api/home       Status tiles from Home Assistant (locks, doors, alarm, people); TV and settings only
  /api/cameras    Which cameras have motion, for the pop-up; TV and settings only
  /api/ha/camera/<id>.jpg   A still from a chosen camera, through this server; TV and settings only
  /api/config     Display settings (no secrets)
  /api/version    Changes whenever settings are saved; the TV reloads on change

The kiosk launcher on the Pi asks /api/screen-url which page the TV should show
and whether the screen should be asleep. "Reload the TV" restarts the browser.

Off-site (DASHBOARD_OFFSITE=1, or --offsite): the server runs somewhere else,
usually in Docker behind an HTTPS proxy, and the Pi only displays it. Requests
from localhost are no longer trusted (the proxy is localhost); the TV proves
itself with the TV key made in Settings → On the TV.

Dependencies: Python 3.9+, icalendar, recurring-ical-events (see requirements.txt).
"""

from __future__ import annotations

import argparse
import copy
import datetime as dt
import hashlib
import hmac
import http.client
import ipaddress
import json
import logging
import math
import mimetypes
import os
import platform
import re
import secrets
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

log = logging.getLogger("dashboard")

HERE = Path(__file__).resolve().parent
DEFAULT_WEB_ROOT = HERE.parent / "web"

TEMPEST_URL = "https://swd.weatherflow.com/swd/rest/better_forecast"
MATRIX_URL = "https://routes.googleapis.com/distanceMatrix/v2:computeRouteMatrix"
ROUTES_URL = "https://routes.googleapis.com/directions/v2:computeRoutes"
APP_VERSION = "2.1"
# Yahoo's symbols: AAPL, BRK-B, SAP.DE, BTC-USD, EURUSD=X, and indexes like ^GSPC
SYMBOL = re.compile(r"^[\^A-Z0-9][A-Z0-9.\-:=^]{0,14}$")
# What people type for the things they mean, and Yahoo's name for them
SYMBOL_ALIASES = {"NASDAQ": "^IXIC", "SP500": "^GSPC", "SPX": "^GSPC", "DOW": "^DJI", "DJI": "^DJI", "RUSSELL": "^RUT", "VIX": "^VIX"}
SYMBOL_LABELS = {"^GSPC": "S&P 500", "^DJI": "Dow", "^IXIC": "Nasdaq", "^RUT": "Russell 2000", "^VIX": "VIX", "^NDX": "Nasdaq 100"}
# Google's original CalDAV address. It still accepts app passwords; the newer
# apidata.googleusercontent.com one only takes OAuth.
GOOGLE_DAV = "https://www.google.com/calendar/dav"
PHOTO_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}
USER_AGENT = "family-dashboard/2.0"
PLACE_ICONS = {"home", "work", "school", "cart", "heart", "fitness", "plane", "star", "pin"}
HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")
HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


class UpstreamError(Exception):
    """An upstream returned something we cannot use."""


# --------------------------------------------------------------------------
# HTTP helpers
# --------------------------------------------------------------------------

def http_request(url: str, *, data: bytes | None = None, headers: dict | None = None,
                 timeout: float = 20.0, method: str | None = None, max_bytes: int = 30_000_000) -> bytes:
    """One outbound request. `timeout` limits each wait; the whole answer must also arrive within twice that
    (a service that drips a byte at a time can't hold a worker), and within `max_bytes`."""
    req = urllib.request.Request(url, data=data, method=method, headers={"User-Agent": USER_AGENT, **(headers or {})})
    host = urllib.parse.urlsplit(url).netloc
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            deadline = time.monotonic() + max(timeout * 2, 10)
            chunks, size = [], 0
            while True:
                chunk = resp.read1(65536)
                if not chunk:
                    return b"".join(chunks)
                size += len(chunk)
                if size > max_bytes:
                    raise UpstreamError(f"{host} sent more than {max_bytes // 1_000_000} MB")
                if time.monotonic() > deadline:
                    raise UpstreamError(f"{host} took too long to answer")
                chunks.append(chunk)
    except urllib.error.HTTPError as e:
        if e.code == 401 and "calendar/dav" in url:
            raise UpstreamError("Google didn't accept the email and app password. Check both, then try again.") from None
        body = e.read()[:300].decode("utf-8", "replace")
        raise UpstreamError(f"HTTP {e.code} from {urllib.parse.urlsplit(url).netloc}: {body}") from None
    except urllib.error.URLError as e:
        raise UpstreamError(f"Cannot reach {urllib.parse.urlsplit(url).netloc}: {e.reason}") from None
    except TimeoutError:
        raise UpstreamError(f"{host} took too long to answer") from None
    except (http.client.HTTPException, OSError, ValueError) as e:    # cut off mid-answer, reset, bad redirect or the like
        raise UpstreamError(f"{host} sent a broken answer ({type(e).__name__})") from None


def http_json(url: str, **kw) -> Any:
    raw = http_request(url, **kw)
    try:
        return json.loads(raw)
    except ValueError:
        raise UpstreamError(f"Invalid JSON from {urllib.parse.urlsplit(url).netloc}") from None


def google_error(obj: Any) -> str | None:
    if isinstance(obj, dict) and isinstance(obj.get("error"), dict):
        return obj["error"].get("message") or str(obj["error"])
    return None


# --------------------------------------------------------------------------
# Settings
# --------------------------------------------------------------------------

DEFAULT_CONFIG: dict = {
    "host": "0.0.0.0",
    "port": 8080,
    "timezone": "UTC",
    "home": {"label": "Home", "address": ""},
    "tempest": {
        "station_id": "", "token": "", "station_name": "Tempest",
        "units": {"temp": "f", "wind": "mph", "precip": "in", "pressure": "inhg", "distance": "mi"},
        "refresh_seconds": 120,
    },
    "calendar": {"refresh_seconds": 600,
                 "account": {"email": "", "app_password": "", "base_url": GOOGLE_DAV},
                 "calendars": []},
    "drive": {
        "api_key": "", "units": "mi", "refresh_seconds": 600, "monthly_limit": 900,
        "active_hours": ["06:00", "22:00"], "leave_buffer_minutes": 5, "places": [],
    },
    "display": {
        "timeFormat": "12h", "perf": "auto", "shareOnNetwork": False,
        "nightDim": {"enabled": True, "start": "22:30", "end": "06:00", "level": 0.6},
        "dailyReload": "03:30", "burnInShift": True,
        "background": "sky", "photoMinutes": 10,
        "bgPhoto": "", "bgColor": "#0B1626", "photoDim": 30, "appearance": "auto", "bgPreset": "",
        "bgGradient": {"a": "#1B2A49", "b": "#4B2E6F", "angle": 160},
    },
    "photos": {"folder": ""},
    "stocks": {"enabled": False, "symbols": [], "refresh_seconds": 30},
    # When the TV sleeps: rules by weekday (0 = Monday), applied by the TV launcher within seconds
    "schedule": {"enabled": True, "rules": [
        {"id": "nightly", "days": [0, 1, 2, 3, 4, 5, 6], "off": "22:00", "on": "06:45", "enabled": True}]},
    # Free layout: None keeps the built-in arrangement; see _layout() for the shape
    "layout": None,
    # Home Assistant: locks, doors and the like as status tiles, and cameras as a tile or a motion pop-up
    "ha": {"enabled": False, "url": "", "token": "", "entities": [], "cameras": [],
           "popup": {"enabled": True, "seconds": 30}, "alert_minutes": 10, "refresh_seconds": 10},
    "tv": {"key_hash": "", "devices": []},
    "admin": {"password_hash": ""},
}


def deep_merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        out[k] = deep_merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


class Store:
    def __init__(self, path: Path, seed: Path | None = None):
        self.path = path
        self.lock = threading.Lock()
        self.boot = secrets.token_hex(3)
        self.saves = 0
        # First run starts from the family's seed settings, if present
        source = path if path.exists() else seed if seed and seed.exists() else None
        try:
            text = source.read_text(encoding="utf-8-sig").strip() if source else ""
            raw = json.loads(text) if text else {}          # an empty file holds nothing to lose: start fresh
        except ValueError as e:
            # Not guessing: starting fresh would overwrite whatever is recoverable in the file
            sys.exit(f"Settings file is not valid JSON ({source}): {e}")
        if not isinstance(raw, dict):
            sys.exit(f"Settings file should hold a JSON object ({source}), not {type(raw).__name__}")
        self.data = deep_merge(DEFAULT_CONFIG, self._well_shaped(DEFAULT_CONFIG, raw))
        tv = self.data["tv"]
        if tv["key_hash"]:
            if not any(isinstance(d, dict) and d.get("hash") == tv["key_hash"] for d in tv["devices"]):
                tv["devices"].insert(0, {"id": "first", "name": "First TV key", "hash": tv["key_hash"], "made": ""})
            tv["key_hash"] = ""
        tv["devices"] = [d for d in tv["devices"] if isinstance(d, dict) and isinstance(d.get("id"), str) and isinstance(d.get("hash"), str)]
        for cal in self.data["calendar"]["calendars"]:
            cal.setdefault("id", secrets.token_hex(4))
            cal.setdefault("enabled", True)
            cal.setdefault("color", "#0A84FF")
            cal.setdefault("ics_url", "")

    @staticmethod
    def _well_shaped(default: dict, raw: dict, path: str = "") -> dict:
        """Drop settings whose type is wrong (a section that is a number, a list that is text) so they fall back to
        their defaults instead of crashing the server later."""
        out = {}
        for key, value in raw.items():
            want = default.get(key)
            if isinstance(want, dict) and not isinstance(value, dict) or isinstance(want, list) and not isinstance(value, list):
                log.warning("Ignoring the setting %s%s in the settings file: wrong type", path, key)
                continue
            out[key] = Store._well_shaped(want, value, f"{path}{key}.") if isinstance(want, dict) else value
        return out

    @property
    def version(self) -> str:
        return f"{self.boot}-{self.saves}"

    def save(self, data: dict, bump: bool = True) -> None:
        with self.lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=2))
            os.chmod(tmp, 0o600)
            os.replace(tmp, self.path)
            self.data = data
            if bump:
                self.saves += 1          # the TV reloads when this changes


# Fields the admin page may change, with validation. Secrets are never sent back
# to the browser; an empty value on save means "keep the current one".

def _str(v: Any, name: str, limit: int = 300) -> str:
    if not isinstance(v, str):
        raise ValueError(f"{name} must be text")
    v = v.strip()
    if len(v) > limit:
        raise ValueError(f"{name} is too long")
    return v


def _hhmm(v: Any, name: str) -> str:
    v = _str(v, name, 5)
    if not HHMM.match(v):
        raise ValueError(f"{name} must look like 06:30")
    return v


def _int(v: Any, name: str, lo: int, hi: int) -> int:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not lo <= v <= hi:
        raise ValueError(f"{name} must be a number from {lo} to {hi}")
    return int(v)


def _choice(v: Any, name: str, options: set) -> str:
    if v not in options:
        raise ValueError(f"{name} must be one of: {', '.join(str(o) for o in sorted(options))}")
    return v


ENTITY_ID = re.compile(r"^[a-z0-9_]+\.[a-z0-9_]+$")
# Anything Home Assistant has that makes sense as a tile on a TV
HA_STATUS_DOMAINS = {"lock", "cover", "binary_sensor", "alarm_control_panel", "person", "device_tracker", "light", "switch",
                     "climate", "fan", "sensor", "media_player", "vacuum", "input_boolean"}
HA_SUGGESTED = {"lock", "cover", "alarm", "person", "opening", "motion", "camera"}    # the security-minded kinds, offered first
HA_TOKEN = re.compile(r"^[A-Za-z0-9._~+/=-]{20,2000}$")
BG_CHOICES = {"sky", "photos", "photo", "gradient", "color"}
APPEARANCE = {"auto", "light", "dark"}
PHOTO_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,100}$")
LAYOUT_TYPES = ("clock", "weather", "drive", "calendar", "focus", "ticker", "home", "camera", "camwall")


def normalize_symbol(sym: str) -> str:
    """AAPL, $aapl, BRK.B and NASDAQ all become what Yahoo calls them: AAPL, AAPL, BRK-B and ^IXIC."""
    sym = sym.strip().upper().lstrip("$")
    if sym in SYMBOL_ALIASES:
        return SYMBOL_ALIASES[sym]
    m = re.fullmatch(r"([A-Z]{1,5})\.([AB])", sym)       # US class shares: Yahoo writes a dash, not a dot
    return f"{m.group(1)}-{m.group(2)}" if m else sym


def stock_label(sym: str) -> str:
    if sym in SYMBOL_LABELS:
        return SYMBOL_LABELS[sym]
    if sym.endswith("-USD") and len(sym) <= 9:
        return sym[:-4]                                    # BTC-USD shows as BTC
    return sym.lstrip("^")


_YF_LOCK = threading.Lock()
_YF = None


def load_yfinance():
    """yfinance is imported when first needed: it brings in pandas and numpy (about 100 MB), which nothing else here uses.
    Its small cache folder is made once, here, because its worker threads would otherwise race to create it."""
    global _YF
    with _YF_LOCK:
        if _YF is None:
            import yfinance
            cache = Path(os.environ.get("YF_CACHE_DIR") or Path.home() / ".cache" / "family-dashboard-yfinance")
            try:
                cache.mkdir(parents=True, exist_ok=True)
                yfinance.set_tz_cache_location(str(cache))
            except Exception as e:  # noqa: BLE001 — a cache is only a speed-up
                log.warning("yfinance cache folder not usable: %s", e)
            _YF = yfinance
        return _YF


def _colour(v: Any, name: str) -> str:
    if not isinstance(v, str) or not HEX.match(v):
        raise ValueError(f"{name} must look like #0B1626")
    return v.upper()


def _layout(v: Any) -> dict | None:
    """The TV's free layout: every widget is a box on the 1920 x 1080 screen."""
    if v is None:
        return None
    if not isinstance(v, dict) or not isinstance(v.get("widgets"), list):
        raise ValueError("The layout must list its widgets")
    if len(v["widgets"]) > len(LAYOUT_TYPES):
        raise ValueError("Too many widgets in the layout")
    seen, out = set(), []
    for w in v["widgets"]:
        if not isinstance(w, dict):
            raise ValueError("Each widget must be an object")
        t = _choice(w.get("type"), "Widget", set(LAYOUT_TYPES))
        if t in seen:
            raise ValueError(f"The {t} widget appears twice")
        seen.add(t)
        x, y = _int(w.get("x"), f"{t} left edge", 0, 1880), _int(w.get("y"), f"{t} top edge", 0, 1040)
        ww, hh = _int(w.get("w"), f"{t} width", 80, 1920), _int(w.get("h"), f"{t} height", 40, 1080)
        if x + ww > 1920 or y + hh > 1080:
            raise ValueError(f"The {t} widget doesn't fit on the screen")
        item: dict = {"type": t, "x": x, "y": y, "w": ww, "h": hh, "visible": bool(w.get("visible", True))}
        sc = w.get("scale", "auto")
        if sc != "auto":
            if isinstance(sc, bool) or not isinstance(sc, (int, float)) or not 0.5 <= sc <= 2.5:
                raise ValueError(f"The {t} text size must be Auto or between 50% and 250%")
            sc = round(float(sc), 2)
        item["scale"] = sc
        o = w.get("options") or {}
        if not isinstance(o, dict):
            raise ValueError("Widget options must be an object")
        if t == "calendar":
            item["options"] = {"days": _choice(o.get("days", 14), "Days shown", {7, 14, 21, 28})}
        elif t == "ticker":
            item["options"] = {"speed": _int(o.get("speed", 90), "Ticker speed", 30, 240)}
        elif t == "camera":
            cam = o.get("camera", "")
            if cam and (not isinstance(cam, str) or not ENTITY_ID.match(cam) or not cam.startswith("camera.")):
                raise ValueError("Pick a camera from the list")
            item["options"] = {"camera": cam}
        elif t == "camwall":
            cams = o.get("cameras", [])
            if (not isinstance(cams, list) or len(cams) > 4
                    or any(not isinstance(x, str) or not x.startswith("camera.") or not ENTITY_ID.match(x) for x in cams)):
                raise ValueError("Pick up to four cameras from the list")
            item["options"] = {"cameras": cams}
        out.append(item)
    return {"version": 1, "widgets": out}


def _ha_entity(e: Any, i: int, domains: set, what: str) -> dict:
    if not isinstance(e, dict) or not isinstance(e.get("id"), str) or not ENTITY_ID.match(e["id"]):
        raise ValueError(f"{what} {i} isn't a Home Assistant item")
    if e["id"].split(".", 1)[0] not in domains:
        raise ValueError(f"{e['id']} can't be shown here")
    return {"id": e["id"], "name": _str(e.get("name", ""), f"{what} {i} name", 40)}


def _ha(patch: dict, cur: dict) -> dict:
    c = copy.deepcopy(cur)
    if "enabled" in patch:
        c["enabled"] = bool(patch["enabled"])
    if "url" in patch:
        url = _url(patch["url"], "Home Assistant address").rstrip("/")
        if url and urllib.parse.urlsplit(url).scheme not in ("http", "https"):
            raise ValueError("The Home Assistant address must start with http:// or https://")
        if c["url"] != url:
            c["token"] = ""                     # a token belongs to one Home Assistant
        c["url"] = url
    if patch.get("token"):
        tok = _str(patch["token"], "Home Assistant token", 2000)
        if not HA_TOKEN.match(tok):
            raise ValueError("That doesn't look like a long-lived access token. Copy the whole token from your Home Assistant profile.")
        c["token"] = tok
        if "enabled" not in patch:
            c["enabled"] = True                  # a new token means "use it"; nobody connects Home Assistant to leave it off
    if "entities" in patch:
        ents = patch["entities"]
        if not isinstance(ents, list) or len(ents) > 24:
            raise ValueError("Up to 24 status tiles fit on the TV")
        c["entities"] = [_ha_entity(e, i, HA_STATUS_DOMAINS, "Status tile") for i, e in enumerate(ents, 1)]
    if "cameras" in patch:
        cams = patch["cameras"]
        if not isinstance(cams, list) or len(cams) > 8:
            raise ValueError("Up to 8 cameras are supported")
        out = []
        for i, cam in enumerate(cams, 1):
            item = _ha_entity(cam, i, {"camera"}, "Camera")
            sensors = cam.get("motion", [])
            if not isinstance(sensors, list) or len(sensors) > 3:
                raise ValueError(f"Camera {i} can use up to 3 motion sensors")
            item["motion"] = [_ha_entity({"id": x}, i, {"binary_sensor"}, "Motion sensor")["id"] for x in sensors]
            item["popup"] = bool(cam.get("popup", True))
            out.append(item)
        c["cameras"] = out
    if "popup" in patch:
        pu = patch["popup"] if isinstance(patch["popup"], dict) else {}
        c["popup"] = {"enabled": bool(pu.get("enabled", c["popup"]["enabled"])),
                      "seconds": _int(pu.get("seconds", c["popup"]["seconds"]), "Pop-up time", 5, 300)}
    if "alert_minutes" in patch:
        c["alert_minutes"] = _int(patch["alert_minutes"], "Alert after", 0, 1440)
    if "refresh_seconds" in patch:
        c["refresh_seconds"] = _int(patch["refresh_seconds"], "Refresh", 5, 300)
    return c


def _schedule_rules(rules: Any) -> list:
    if not isinstance(rules, list) or len(rules) > 14:
        raise ValueError("Up to 14 sleep times are supported")
    out = []
    for i, r in enumerate(rules, 1):
        if not isinstance(r, dict):
            raise ValueError(f"Sleep time {i} must be an object")
        days = r.get("days")
        if (not isinstance(days, list) or not days
                or any(isinstance(d, bool) or not isinstance(d, int) or not 0 <= d <= 6 for d in days)):
            raise ValueError(f"Sleep time {i} needs at least one day")
        off, on = _hhmm(r.get("off"), f"Sleep time {i} start"), _hhmm(r.get("on"), f"Sleep time {i} end")
        if off == on:
            raise ValueError(f"Sleep time {i}: the screen can't go to sleep and wake at the same moment")
        rid = r.get("id") if isinstance(r.get("id"), str) and re.fullmatch(r"[A-Za-z0-9]{1,12}", r["id"]) else secrets.token_hex(3)
        out.append({"id": rid, "days": sorted(set(days)), "off": off, "on": on, "enabled": bool(r.get("enabled", True))})
    return out


def _url(v: str, name: str) -> str:
    v = _str(v, name, 2000)
    if v and urllib.parse.urlsplit(v).scheme not in ("http", "https", "webcal"):
        raise ValueError(f"{name} must be a web link starting with https://")
    return "https" + v[6:] if v.startswith("webcal") else v


def _shape_check(default: dict, patch: dict, path: str = "") -> None:
    """A section must arrive as a section; the validators below deal with each field (some, like symbols, take text or a list)."""
    for key, value in patch.items():
        want = default.get(key)
        if isinstance(want, dict) and not isinstance(value, dict):
            raise ValueError(f"The {path}{key} settings must be an object")
        if isinstance(want, dict):
            _shape_check(want, value, f"{path}{key} ")


def apply_settings(current: dict, patch: dict) -> dict:
    """Return a new config with the admin's changes applied, or raise ValueError."""
    if not isinstance(patch, dict):
        raise ValueError("Settings must be an object")
    _shape_check(DEFAULT_CONFIG, patch)
    try:
        return _apply_settings(current, patch)
    except (TypeError, AttributeError, KeyError, IndexError, OverflowError) as e:
        # The work is done on a copy, so nothing was half-changed. Say no, rather than crash the request.
        log.warning("Settings in an unexpected shape (%s: %s)", type(e).__name__, e)
        raise ValueError("Those settings aren't in the shape the dashboard expects") from None


def _apply_settings(current: dict, patch: dict) -> dict:
    c = copy.deepcopy(current)
    c["stocks"].pop("api_key", None)        # an old Finnhub key: prices come from Yahoo now, so don't keep a secret nobody uses

    if "timezone" in patch:
        tz = _str(patch["timezone"], "Time zone", 60)
        try:
            ZoneInfo(tz)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError(f"Unknown time zone: {tz}") from None
        c["timezone"] = tz

    if "home" in patch:
        h = patch["home"]
        if "address" in h:
            c["home"]["address"] = _str(h["address"], "Home address")
        if "label" in h:
            c["home"]["label"] = _str(h["label"], "Home name", 40) or "Home"

    if "tempest" in patch:
        t = patch["tempest"]
        if "station_id" in t:
            sid = _str(str(t["station_id"]), "Station ID", 12)
            if sid and not sid.isdigit():
                raise ValueError("Station ID is the number from your station's page, like 12345")
            c["tempest"]["station_id"] = sid
        if t.get("token"):
            c["tempest"]["token"] = _str(t["token"], "Tempest token", 100)
        if "station_name" in t:
            c["tempest"]["station_name"] = _str(t["station_name"], "Station name", 60) or "Tempest"
        if "units" in t:
            u = t["units"]
            c["tempest"]["units"].update({
                "temp": _choice(u.get("temp", c["tempest"]["units"]["temp"]), "Temperature unit", {"f", "c"}),
                "wind": _choice(u.get("wind", c["tempest"]["units"]["wind"]), "Wind unit", {"mph", "kph", "mps", "kts"}),
                "precip": _choice(u.get("precip", c["tempest"]["units"]["precip"]), "Rain unit", {"in", "mm"}),
            })

    if "calendar" in patch and "account" in patch["calendar"]:
        acc = patch["calendar"]["account"]
        if "email" in acc:
            email = _str(acc["email"], "Google account email", 120)
            if email and "@" not in email:
                raise ValueError("Enter the full email address, like name@example.com")
            if email != c["calendar"]["account"]["email"]:
                c["calendar"]["account"]["app_password"] = ""  # a password belongs to one account
            c["calendar"]["account"]["email"] = email
        if acc.get("app_password"):
            pw = _str(acc["app_password"], "App password", 40).replace(" ", "")
            if len(pw) != 16 or not pw.isalpha():
                raise ValueError("An app password is 16 letters, shown by Google in four groups of four")
            c["calendar"]["account"]["app_password"] = pw

    if "calendar" in patch and "calendars" in patch["calendar"]:
        old = {cal["id"]: cal for cal in c["calendar"]["calendars"]}
        cals = []
        incoming = patch["calendar"]["calendars"]
        if not isinstance(incoming, list) or len(incoming) > 40:
            raise ValueError("Up to 40 calendars are supported")
        for i, cal in enumerate(incoming, 1):
            prev = old.get(cal.get("id"), {})
            name = _str(cal.get("name", ""), f"Calendar {i} name", 40)
            if not name:
                raise ValueError(f"Calendar {i} needs a name")
            color = cal.get("color", "#0A84FF")
            if not isinstance(color, str) or not HEX.match(color):
                raise ValueError(f"{name}: colour must look like #0A84FF")
            entry = {"id": prev.get("id") or secrets.token_hex(4), "name": name, "color": color.upper(),
                     "enabled": bool(cal.get("enabled", True))}
            if prev.get("href"):  # from the Google account; only Refresh can add or move these
                entry["href"] = prev["href"]
            else:
                entry["ics_url"] = _url(cal["ics_url"], f"{name} link") if cal.get("ics_url") else prev.get("ics_url", "")
            cals.append(entry)
        c["calendar"]["calendars"] = cals

    if "drive" in patch:
        d = patch["drive"]
        if d.get("api_key"):
            c["drive"]["api_key"] = _str(d["api_key"], "Google Maps key", 100)
        if "refresh_seconds" in d:
            c["drive"]["refresh_seconds"] = _int(d["refresh_seconds"], "Drive refresh", 300, 3600)
        if "monthly_limit" in d:
            c["drive"]["monthly_limit"] = _int(d["monthly_limit"], "Monthly Google Maps limit", 0, 100000)
        if "leave_buffer_minutes" in d:
            c["drive"]["leave_buffer_minutes"] = _int(d["leave_buffer_minutes"], "Leave-by buffer", 0, 60)
        if "active_hours" in d:
            ah = d["active_hours"]
            if not isinstance(ah, list) or len(ah) != 2:
                raise ValueError("Active hours need a start and an end")
            c["drive"]["active_hours"] = [_hhmm(ah[0], "Active hours start"), _hhmm(ah[1], "Active hours end")]
        if "places" in d:
            places = d["places"]
            if not isinstance(places, list) or len(places) > 4:
                raise ValueError("Up to 4 always-on places fit on the TV")
            c["drive"]["places"] = []
            for i, p in enumerate(places, 1):
                name, addr = _str(p.get("name", ""), f"Place {i} name", 40), _str(p.get("address", ""), f"Place {i} address")
                if not name or not addr:
                    raise ValueError(f"Place {i} needs a name and an address")
                c["drive"]["places"].append({"name": name, "address": addr,
                                             "icon": _choice(p.get("icon", "pin"), f"{name} icon", PLACE_ICONS)})

    if "display" in patch:
        s = patch["display"]
        disp = c["display"]
        if "timeFormat" in s:
            disp["timeFormat"] = _choice(s["timeFormat"], "Clock style", {"12h", "24h"})
        if "perf" in s:
            disp["perf"] = _choice(s["perf"], "Render quality", {"auto", "lite", "full"})
        if "shareOnNetwork" in s:
            disp["shareOnNetwork"] = bool(s["shareOnNetwork"])
        if "background" in s:
            disp["background"] = _choice(s["background"], "Background", BG_CHOICES)
        if "appearance" in s:
            disp["appearance"] = _choice(s["appearance"], "Appearance", APPEARANCE)
        if "bgColor" in s:
            disp["bgColor"] = _colour(s["bgColor"], "Background colour")
        if "bgGradient" in s:
            g = s["bgGradient"] if isinstance(s["bgGradient"], dict) else {}
            disp["bgGradient"] = {"a": _colour(g.get("a"), "Gradient start colour"), "b": _colour(g.get("b"), "Gradient end colour"),
                                  "angle": _int(g.get("angle", 160), "Gradient angle", 0, 359)}
        if "bgPhoto" in s:
            name = _str(s["bgPhoto"], "Background photo", 100)
            if name and not (PHOTO_NAME.match(name) and Path(name).suffix.lower() in PHOTO_TYPES):
                raise ValueError("Pick a photo from the list")
            disp["bgPhoto"] = name
        if "photoDim" in s:
            disp["photoDim"] = _int(s["photoDim"], "Photo darkening", 0, 80)
        if "bgPreset" in s:
            disp["bgPreset"] = _str(s["bgPreset"], "Background preset", 40)
        if "photoMinutes" in s:
            disp["photoMinutes"] = _int(s["photoMinutes"], "Change photo every", 1, 1440)
        if "nightDim" in s:
            n = s["nightDim"]
            disp["nightDim"] = {
                "enabled": bool(n.get("enabled", disp["nightDim"]["enabled"])),
                "start": _hhmm(n.get("start", disp["nightDim"]["start"]), "Dim start"),
                "end": _hhmm(n.get("end", disp["nightDim"]["end"]), "Dim end"),
                "level": round(_int(round(float(n.get("level", disp["nightDim"]["level"])) * 100), "Dim level", 0, 90) / 100, 2),
            }

    if "schedule" in patch:
        sc = patch["schedule"]
        if not isinstance(sc, dict):
            raise ValueError("The schedule must be an object")
        if "enabled" in sc:
            c["schedule"]["enabled"] = bool(sc["enabled"])
        if "rules" in sc:
            c["schedule"]["rules"] = _schedule_rules(sc["rules"])

    if "layout" in patch:
        c["layout"] = _layout(patch["layout"])

    if "ha" in patch:
        if not isinstance(patch["ha"], dict):
            raise ValueError("Home Assistant settings must be an object")
        c["ha"] = _ha(patch["ha"], c["ha"])

    if "stocks" in patch:
        st = patch["stocks"]
        if "enabled" in st:
            c["stocks"]["enabled"] = bool(st["enabled"])
        if "refresh_seconds" in st:
            c["stocks"]["refresh_seconds"] = _int(st["refresh_seconds"], "Stock refresh", 15, 600)
        if "symbols" in st:
            syms = st["symbols"]
            if isinstance(syms, str):
                syms = re.split(r"[\s,]+", syms)
            if not isinstance(syms, list):
                raise ValueError("Tickers must be a list, like AAPL, MSFT, SPY")
            out = []
            for sym in syms:
                sym = normalize_symbol(_str(sym, "Ticker", 15))
                if not sym:
                    continue
                if not SYMBOL.match(sym):
                    raise ValueError(f"{sym} doesn't look like a ticker. Use symbols like AAPL, BRK-B or ^GSPC")
                if sym not in out:
                    out.append(sym)
            if len(out) > 30:
                raise ValueError("Up to 30 tickers fit in the bar")
            c["stocks"]["symbols"] = out

    if "photos" in patch and "folder" in patch["photos"]:
        folder = _str(patch["photos"]["folder"], "Photo folder", 500)
        if folder and not (folder.startswith("/") and Path(folder).is_dir()):
            raise ValueError("Photo folder must be a full path to a folder on the Pi, like /media/usb/Family")
        c["photos"]["folder"] = folder

    c.pop("screen", None)                   # the old "which screen" choice: the TV always shows this dashboard now

    return c


def mask(value: str) -> dict:
    return {"set": bool(value), "hint": f"…{value[-4:]}" if len(value) > 8 else ""}


def public_settings(c: dict) -> dict:
    """Settings as the admin page sees them: everything except secrets."""
    out = copy.deepcopy(c)
    out.pop("admin", None)
    devices = [{"id": d["id"], "name": str(d.get("name", "TV")), "made": d.get("made", ""), "layout": d.get("layout")}
               for d in c["tv"].get("devices", []) if isinstance(d, dict)]
    out["tv"] = {"key": {"set": bool(devices), "hint": ""}, "devices": devices}
    out["tempest"]["token"] = mask(c["tempest"]["token"])
    out["drive"]["api_key"] = mask(c["drive"]["api_key"])
    out["stocks"].pop("api_key", None)      # never send an old Finnhub key to a browser
    out["ha"]["token"] = mask(c["ha"]["token"])
    out.pop("screen", None)
    acc = out["calendar"]["account"]
    acc["app_password"] = {"set": bool(c["calendar"]["account"]["app_password"]), "hint": ""}
    acc.pop("base_url", None)
    for cal in out["calendar"]["calendars"]:
        link = cal.pop("ics_url", "")
        cal["source"] = "google" if cal.pop("href", None) else "link"
        cal["link"] = {"set": bool(link), "host": urllib.parse.urlsplit(link).netloc if link else ""}
    return out


# --------------------------------------------------------------------------
# Admin password + sessions
# --------------------------------------------------------------------------

def hash_password(pw: str, salt: bytes | None = None, iters: int = 200_000) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, iters)
    return f"pbkdf2_sha256${iters}${salt.hex()}${digest.hex()}"


def check_password(pw: str, stored: str) -> bool:
    try:
        _, iters, salt, digest = stored.split("$")
        test = hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt), int(iters))
        return hmac.compare_digest(test.hex(), digest)
    except (ValueError, AttributeError):
        return False


class Sessions:
    # shortcut: sessions live in memory, so a server restart signs everyone out; persist if that annoys anyone
    TTL = 30 * 86400

    def __init__(self):
        self.tokens: dict[str, float] = {}
        self.failures: dict[str, list[float]] = {}
        self.lock = threading.Lock()

    def create(self) -> str:
        token = secrets.token_urlsafe(32)
        with self.lock:
            self.tokens[token] = time.time() + self.TTL
        return token

    def valid(self, token: str | None) -> bool:
        with self.lock:
            exp = self.tokens.get(token or "")
            return bool(exp and exp > time.time())

    def drop(self, token: str | None) -> None:
        with self.lock:
            self.tokens.pop(token or "", None)

    def locked_out(self, ip: str) -> bool:
        recent = [t for t in self.failures.get(ip, []) if t > time.time() - 300]
        self.failures[ip] = recent
        return len(recent) >= 5

    def fail(self, ip: str) -> None:
        self.failures.setdefault(ip, []).append(time.time())


# --------------------------------------------------------------------------
# Cache with stale-on-error
# --------------------------------------------------------------------------

class Source:
    def __init__(self, name: str, ttl: float, fetch: Callable[[], dict]):
        self.name = name
        self.ttl = ttl
        self.fetch = fetch
        self.data: dict | None = None
        self.fetched_at = 0.0
        self.error: str | None = None
        self.error_at = 0.0
        self.lock = threading.Lock()

    def get(self) -> dict:
        with self.lock:
            fresh = self.data is not None and time.time() - self.fetched_at < self.ttl
            backing_off = self.error is not None and time.time() - self.error_at < 60
            if not fresh and not backing_off:
                try:
                    self.data = self.fetch()
                    self.fetched_at = time.time()
                    self.error = None
                except Exception as e:  # noqa: BLE001 — any failure becomes "stale"
                    self.error = str(e) or e.__class__.__name__
                    self.error_at = time.time()
                    log.warning("%s refresh failed: %s", self.name, self.error)
            if self.data is None:
                raise UpstreamError(self.error or f"{self.name} has no data yet")
            out = dict(self.data)
            if self.error:
                out["stale"] = True
                out["error"] = self.error
            return out

    def status(self) -> dict:
        return {"ok": self.error is None and self.data is not None,
                "fetchedAt": int(self.fetched_at) or None, "error": self.error}


# --------------------------------------------------------------------------
# Tempest
# --------------------------------------------------------------------------

UNIT_LABEL = {"f": "F", "c": "C", "mph": "mph", "kph": "km/h", "kts": "kn", "mps": "m/s",
              "in": "in", "mm": "mm", "cm": "cm"}


def _num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


class Weather:
    def __init__(self, cfg: dict, tz: ZoneInfo):
        self.cfg = cfg
        self.tz = tz

    def url(self) -> str:
        units = {"temp": "f", "wind": "mph", "precip": "in", "pressure": "inhg", "distance": "mi"}
        units.update(self.cfg.get("units") or {})
        q = {"station_id": str(self.cfg["station_id"]), "token": self.cfg["token"],
             **{f"units_{k}": v for k, v in units.items()}}
        return f"{self.cfg.get('base_url', TEMPEST_URL)}?{urllib.parse.urlencode(q)}"

    def fetch(self) -> dict:
        if not self.cfg.get("station_id") or not self.cfg.get("token"):
            raise UpstreamError("Add your Tempest station ID and token in Settings → Weather")
        return self.normalise(http_json(self.url()))

    def normalise(self, raw: dict) -> dict:
        status = raw.get("status") or {}
        if status.get("status_code") not in (None, 0):
            raise UpstreamError(f"Tempest: {status.get('status_message', 'error')}")
        cur = raw.get("current_conditions") or {}
        units = raw.get("units") or {}
        daily = []
        for d in (raw.get("forecast") or {}).get("daily") or []:
            start = d.get("day_start_local")
            if not isinstance(start, (int, float)):
                continue
            daily.append({
                # day_start_local is local midnight as an epoch; +6h guards against DST edges
                "date": dt.datetime.fromtimestamp(start + 6 * 3600, self.tz).date().isoformat(),
                "high": _num(d.get("air_temp_high")), "low": _num(d.get("air_temp_low")),
                "icon": d.get("icon") or "cloudy", "conditions": d.get("conditions") or "",
                "pop": _num(d.get("precip_probability")) or 0,
                "sunrise": d.get("sunrise"), "sunset": d.get("sunset"),
            })
        return {
            "station": self.cfg.get("station_name") or raw.get("location_name") or "Tempest",
            "units": {"temp": UNIT_LABEL.get(units.get("units_temp", "f"), "F"),
                      "wind": UNIT_LABEL.get(units.get("units_wind", "mph"), "mph"),
                      "precip": UNIT_LABEL.get(units.get("units_precip", "in"), "in")},
            "current": {
                "temp": _num(cur.get("air_temperature")), "feelsLike": _num(cur.get("feels_like")),
                "conditions": cur.get("conditions") or "", "icon": cur.get("icon") or "cloudy",
                "humidity": _num(cur.get("relative_humidity")), "windAvg": _num(cur.get("wind_avg")),
                "windGust": _num(cur.get("wind_gust")), "windDir": cur.get("wind_direction_cardinal") or "",
                "precipToday": _num(cur.get("precip_accum_local_day")), "uv": _num(cur.get("uv")),
                "pressure": _num(cur.get("sea_level_pressure")), "pressureTrend": cur.get("pressure_trend") or "",
            },
            "daily": daily[:10],
            "updated": int(time.time()),
        }


# --------------------------------------------------------------------------
# Google Calendar (secret iCal links)
# --------------------------------------------------------------------------

# --------------------------------------------------------------------------
# Google account over CalDAV (email + app password)
# --------------------------------------------------------------------------

DAV_NS = {"d": "DAV:", "c": "urn:ietf:params:xml:ns:caldav", "a": "http://apple.com/ns/ical/"}


def _dav(method: str, url: str, account: dict, body: str, depth: str) -> "Any":
    import base64
    import xml.etree.ElementTree as ET
    cred = base64.b64encode(f"{account['email']}:{account['app_password'].replace(' ', '')}".encode()).decode()
    raw = http_request(url, data=body.encode(), method=method, timeout=30, headers={
        "Authorization": f"Basic {cred}", "Depth": depth, "Content-Type": "application/xml; charset=utf-8"})
    try:
        return ET.fromstring(raw)
    except ET.ParseError:
        raise UpstreamError("Google's calendar server sent something unexpected") from None


def discover_calendars(account: dict) -> list[dict]:
    """Every calendar in the account: [{href, name, color}]."""
    if not account.get("email") or not account.get("app_password"):
        raise UpstreamError("Enter the Google account's email and app password")
    base = account.get("base_url") or GOOGLE_DAV
    principal = f"{base}/{urllib.parse.quote(account['email'])}/user/"
    root = _dav("PROPFIND", principal, account,
                '<d:propfind xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav"><d:prop><c:calendar-home-set/></d:prop></d:propfind>', "0")
    home = root.find(".//c:calendar-home-set/d:href", DAV_NS)
    home_url = urllib.parse.urljoin(principal, home.text.strip()) if home is not None and home.text else principal
    root = _dav("PROPFIND", home_url, account,
                '<d:propfind xmlns:d="DAV:" xmlns:a="http://apple.com/ns/ical/"><d:prop>'
                '<d:displayname/><d:resourcetype/><a:calendar-color/></d:prop></d:propfind>', "1")
    out = []
    for resp in root.findall("d:response", DAV_NS):
        if resp.find(".//d:resourcetype/c:calendar", DAV_NS) is None:
            continue
        href = resp.findtext("d:href", "", DAV_NS).strip()
        name = (resp.findtext(".//d:displayname", "", DAV_NS) or "").strip()
        color = (resp.findtext(".//a:calendar-color", "", DAV_NS) or "").strip()[:7]
        out.append({"href": urllib.parse.urljoin(home_url, href), "name": name or "Calendar",
                    "color": color.upper() if HEX.match(color) else ""})
    if not out:
        raise UpstreamError("Signed in, but Google listed no calendars for this account")
    return out


def caldav_objects(account: dict, href: str, start: dt.datetime, end: dt.datetime) -> list[bytes]:
    """iCalendar text of every event that touches [start, end)."""
    f = lambda t: t.astimezone(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    root = _dav("REPORT", href, account,
                '<c:calendar-query xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">'
                '<d:prop><c:calendar-data/></d:prop><c:filter><c:comp-filter name="VCALENDAR">'
                f'<c:comp-filter name="VEVENT"><c:time-range start="{f(start)}" end="{f(end)}"/>'
                '</c:comp-filter></c:comp-filter></c:filter></c:calendar-query>', "1")
    return [el.text.encode() for el in root.findall(".//c:calendar-data", DAV_NS) if el.text]


def merge_copies(events: list[dict]) -> list[dict]:
    """One event per thing on the family calendar. The same event often sits on
    several calendars (an invite to three of the kids, or "Take out the trash"
    copied to everyone's calendar); copies have the same title, start and end.
    The first calendar in Settings keeps the event; every calendar is listed."""
    out: list[dict] = []
    seen: dict[tuple, dict] = {}
    for ev in events:
        key = (ev["allDay"], ev["start"], ev["end"], " ".join(ev["title"].casefold().split()))
        first = seen.get(key)
        if first is None:
            ev = {**ev, "calendars": [ev["calendar"]], "colors": [ev["color"]]}
            seen[key] = ev
            out.append(ev)
            continue
        if ev["calendar"] not in first["calendars"]:
            first["calendars"].append(ev["calendar"])
            first["colors"].append(ev["color"])
        if not first["location"] and ev["location"]:
            first["location"] = ev["location"]
    return out


class Calendars:
    def __init__(self, calendars: list[dict], tz: ZoneInfo, days_back: int = 1, days_ahead: int = 16,
                 account: dict | None = None):
        self.account = account or {}
        self.calendars = [c for c in calendars if c.get("enabled", True) and (c.get("ics_url") or c.get("href"))]
        self.tz = tz
        self.days_back = days_back
        self.days_ahead = days_ahead
        self.status: dict[str, dict] = {}

    def fetch_one(self, cal: dict, start, end) -> list[dict]:
        import icalendar  # imported lazily so the rest of the server runs without it
        import recurring_ical_events

        if cal.get("href"):
            blobs = caldav_objects(self.account, cal["href"], start, end)
        else:
            blobs = [http_request(cal["ics_url"], timeout=30)]
        events = []
        for raw in blobs:
            try:
                parsed = icalendar.Calendar.from_ical(raw)
            except ValueError:
                raise UpstreamError("The link did not return a calendar. Copy the 'Secret address in iCal format'.") from None
            for occ in recurring_ical_events.of(parsed).between(start, end):
                ev = self._event(occ, cal)
                if ev:
                    events.append(ev)
        return events

    def fetch(self) -> dict:
        if not self.calendars:
            raise UpstreamError("Connect a Google account or turn on a calendar in Settings → Calendars")
        today = dt.datetime.now(self.tz).date()
        start = dt.datetime.combine(today - dt.timedelta(days=self.days_back), dt.time(), self.tz)
        end = dt.datetime.combine(today + dt.timedelta(days=self.days_ahead), dt.time(), self.tz)

        events, failed = [], 0
        for cal in self.calendars:
            try:
                got = self.fetch_one(cal, start, end)
                events += got
                self.status[cal["id"]] = {"ok": True, "count": len(got), "at": int(time.time())}
            except Exception as e:  # noqa: BLE001 — one bad calendar must not hide the others
                failed += 1
                self.status[cal["id"]] = {"ok": False, "error": str(e), "at": int(time.time())}
                log.warning("calendar %s failed: %s", cal.get("name"), e)
        if failed == len(self.calendars):
            raise UpstreamError("No calendar could be loaded. Check the links in Settings → Calendars.")

        events = merge_copies(events)
        events.sort(key=lambda e: (e["start"], e["title"]))
        return {
            "calendars": [{"name": c["name"], "color": c["color"]} for c in self.calendars],
            "events": events,
            "updated": int(time.time()),
        }

    def _event(self, occ, cal: dict) -> dict | None:
        if str(occ.get("STATUS", "")).upper() == "CANCELLED" or occ.get("DTSTART") is None:
            return None
        start = occ.get("DTSTART").dt
        if occ.get("DTEND") is not None:
            end = occ.get("DTEND").dt
        elif occ.get("DURATION") is not None:
            end = start + occ.get("DURATION").dt
        else:
            end = start + dt.timedelta(days=1) if not isinstance(start, dt.datetime) else start

        base = {"title": str(occ.get("SUMMARY") or "").strip() or "Busy",
                "calendar": cal["name"], "color": cal["color"],
                "location": " ".join(str(occ.get("LOCATION") or "").split())}

        if not isinstance(start, dt.datetime):  # all-day: date objects, end exclusive
            end = end.date() if isinstance(end, dt.datetime) else end
            if end <= start:
                end = start + dt.timedelta(days=1)
            return {**base, "allDay": True, "start": start.isoformat(), "end": end.isoformat()}

        start = self._aware(start)
        end = self._aware(end if isinstance(end, dt.datetime) else dt.datetime.combine(end, dt.time()))
        return {**base, "allDay": False, "start": start.astimezone(self.tz).isoformat(),
                "end": end.astimezone(self.tz).isoformat()}

    def _aware(self, d: dt.datetime) -> dt.datetime:
        # Floating times (no TZID) are interpreted in the household time zone
        return d if d.tzinfo is not None else d.replace(tzinfo=self.tz)


# --------------------------------------------------------------------------
# Drive times (Google Routes API)
# --------------------------------------------------------------------------

def hhmm_minutes(s: str) -> int:
    h, m = s.split(":")
    return int(h) * 60 + int(m)


def parse_duration(s: Any) -> float | None:
    if isinstance(s, str) and s.endswith("s"):
        try:
            return float(s[:-1])
        except ValueError:
            return None
    return None


def decode_polyline(encoded: str) -> list[tuple[float, float]]:
    """Google encoded polyline → [(lat, lng)]."""
    points, idx, lat, lng = [], 0, 0, 0
    while idx < len(encoded):
        for which in (0, 1):
            shift = result = 0
            while True:
                b = ord(encoded[idx]) - 63
                idx += 1
                result |= (b & 0x1F) << shift
                shift += 5
                if b < 0x20:
                    break
            delta = ~(result >> 1) if result & 1 else result >> 1
            if which == 0:
                lat += delta
            else:
                lng += delta
        points.append((lat / 1e5, lng / 1e5))
    return points


def metres(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * 6371000 * math.asin(math.sqrt(h))


def slow_stretches(polyline: str, intervals: list[dict]) -> dict:
    """Length of SLOW and TRAFFIC_JAM stretches along the route, in metres."""
    pts = decode_polyline(polyline) if polyline else []
    out = {"SLOW": 0.0, "TRAFFIC_JAM": 0.0}
    for iv in intervals or []:
        speed = iv.get("speed")
        if speed not in out:
            continue
        s, e = iv.get("startPolylinePointIndex", 0), iv.get("endPolylinePointIndex", 0)
        out[speed] += sum(metres(pts[i], pts[i + 1]) for i in range(s, min(e, len(pts) - 1)))
    return out


class MapsBudget:
    """A monthly cap on Google Maps requests (each matrix or route request is one
    read), kept on disk so restarts don't reset it. The month's reads are spread
    evenly: each day may use what's left divided by the days left, so a busy
    morning can't spend the rest of the month."""

    def __init__(self, path: Path, limit: int, tz: ZoneInfo):
        self.path, self.limit, self.tz = path, int(limit), tz
        self.lock = threading.Lock()
        try:
            self.s = json.loads(path.read_text())
        except (OSError, ValueError):
            self.s = {}
        self._roll()

    def _roll(self) -> None:
        today = dt.datetime.now(self.tz).date()
        month, day = today.strftime("%Y-%m"), today.isoformat()
        if self.s.get("month") != month:
            self.s = {"month": month, "used": 0}
        if self.s.get("day") != day:
            self.s.update(day=day, today=0, day_start_used=self.s["used"])

    def allowance(self) -> float:
        """Reads today may use."""
        today = dt.datetime.now(self.tz).date()
        nxt = (today.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
        days_left = (nxt - today).days
        return max(self.limit - self.s["day_start_used"], 0) / days_left

    def can_spend(self, share: float = 1.0) -> bool:
        with self.lock:
            self._roll()
            if self.s["used"] >= self.limit:
                return False
            return self.s["today"] < max(self.allowance() * share, 1 if share >= 1 else 0)

    def spend(self) -> None:
        with self.lock:
            self._roll()
            if self.s["used"] >= self.limit:
                raise UpstreamError(f"This month's {self.limit} Google Maps reads are used up. Drive times return on the 1st.")
            self.s["used"] += 1
            self.s["today"] += 1
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self.path.write_text(json.dumps(self.s))
            except OSError as e:
                log.warning("couldn't save Google Maps usage: %s", e)

    def status(self) -> dict:
        with self.lock:
            self._roll()
            return {"limit": self.limit, "used": self.s["used"], "today": self.s["today"],
                    "todayAllowance": round(self.allowance(), 1), "month": self.s["month"]}


class Drive:
    def __init__(self, cfg: dict, origin: str, tz: ZoneInfo, budget: MapsBudget | None = None):
        self.cfg = cfg
        self.origin = origin
        self.tz = tz
        self.budget = budget

    def _spend(self) -> None:
        if self.budget:
            self.budget.spend()

    def active_seconds_per_day(self) -> float:
        hours = self.cfg.get("active_hours")
        if not hours:
            return 86400.0
        s, e = (hhmm_minutes(x) for x in hours)
        return float(((e - s) % 1440 or 1440) * 60)

    def in_active_hours(self) -> bool:
        hours = self.cfg.get("active_hours")
        if not hours:
            return True
        now = dt.datetime.now(self.tz).time()
        s, e = (dt.time.fromisoformat(x) for x in hours)
        return s <= now < e if s <= e else now >= s or now < e

    def _check(self):
        if not self.cfg.get("api_key"):
            raise UpstreamError("Add a Google Maps key in Settings → Drive times")
        if not self.origin:
            raise UpstreamError("Add your home address in Settings → Drive times")

    def _dist(self, m: Any) -> float | None:
        if not isinstance(m, (int, float)):
            return None
        return round(m / (1000 if self.cfg.get("units") == "km" else 1609.344), 1)

    def fetch(self) -> dict:
        self._check()
        places = self.cfg.get("places") or []
        if not places:
            raise UpstreamError("Add places in Settings → Drive times")
        body = {
            "origins": [{"waypoint": {"address": self.origin}}],
            "destinations": [{"waypoint": {"address": p["address"]}} for p in places],
            "travelMode": "DRIVE", "routingPreference": "TRAFFIC_AWARE",
        }
        self._spend()
        rows = http_json(self.cfg.get("matrix_url", MATRIX_URL), data=json.dumps(body).encode(), headers={
            "Content-Type": "application/json", "X-Goog-Api-Key": self.cfg["api_key"],
            "X-Goog-FieldMask": "originIndex,destinationIndex,status,condition,distanceMeters,duration,staticDuration",
        })
        if google_error(rows):
            raise UpstreamError(f"Google Maps: {google_error(rows)}")
        if not isinstance(rows, list):
            raise UpstreamError("Google Maps returned an unexpected response")
        by_dest = {r.get("destinationIndex", 0): r for r in rows}
        out = []
        for i, p in enumerate(places):
            r = by_dest.get(i) or {}
            dur, static = parse_duration(r.get("duration")), parse_duration(r.get("staticDuration"))
            ok = r.get("condition") == "ROUTE_EXISTS" and dur is not None
            out.append({
                "name": p["name"], "icon": p.get("icon", "pin"),
                "minutes": round(dur / 60) if ok else None,
                "typicalMinutes": round(static / 60) if ok and static is not None else None,
                "distance": self._dist(r.get("distanceMeters")),
                "unit": "km" if self.cfg.get("units") == "km" else "mi",
                "status": "ok" if ok else "no_route",
            })
        return {"places": out, "updated": int(time.time())}

    def route(self, destination: str, arrive: dt.datetime | None) -> dict:
        """One trip with traffic detail, for the leave-by card."""
        self._check()
        body = {
            "origin": {"address": self.origin}, "destination": {"address": destination},
            "travelMode": "DRIVE", "routingPreference": "TRAFFIC_AWARE",
            "extraComputations": ["TRAFFIC_ON_POLYLINE"],
        }
        now = dt.datetime.now(dt.timezone.utc)
        if arrive:
            # Ask about traffic around when they'd actually leave, if that's still ahead
            depart = arrive.astimezone(dt.timezone.utc) - dt.timedelta(minutes=45)
            if depart > now + dt.timedelta(minutes=2):
                body["departureTime"] = depart.strftime("%Y-%m-%dT%H:%M:%SZ")
        self._spend()
        res = http_json(self.cfg.get("routes_url", ROUTES_URL), data=json.dumps(body).encode(), headers={
            "Content-Type": "application/json", "X-Goog-Api-Key": self.cfg["api_key"],
            "X-Goog-FieldMask": "routes.duration,routes.staticDuration,routes.distanceMeters,routes.description,"
                                "routes.polyline.encodedPolyline,routes.travelAdvisory.speedReadingIntervals",
        })
        if google_error(res):
            raise UpstreamError(f"Google Maps: {google_error(res)}")
        routes = (res or {}).get("routes") or []
        if not routes:
            return {"status": "no_route"}
        r = routes[0]
        dur = parse_duration(r.get("duration"))
        static = parse_duration(r.get("staticDuration"))
        if dur is None:
            return {"status": "no_route"}
        stretches = slow_stretches((r.get("polyline") or {}).get("encodedPolyline", ""),
                                   (r.get("travelAdvisory") or {}).get("speedReadingIntervals"))
        out = {
            "status": "ok", "minutes": max(1, round(dur / 60)),
            "typicalMinutes": round(static / 60) if static is not None else None,
            "distance": self._dist(r.get("distanceMeters")),
            "unit": "km" if self.cfg.get("units") == "km" else "mi",
            "via": r.get("description") or "",
            "slow": self._dist(stretches["SLOW"]), "jam": self._dist(stretches["TRAFFIC_JAM"]),
            "bufferMinutes": int(self.cfg.get("leave_buffer_minutes", 5)),
            "checked": int(time.time()),
        }
        if arrive:
            out["leaveBy"] = (arrive - dt.timedelta(minutes=out["minutes"] + out["bufferMinutes"])).isoformat()
        return out


class DriveSource(Source):
    """Outside active hours, keep serving the last result instead of paying for new ones.
    With a monthly cap, the always-on drive times use at most MATRIX_SHARE of each
    day's reads, spaced evenly through the active hours; leave-by times get the rest."""

    MATRIX_SHARE = 0.6

    def __init__(self, drive: Drive, ttl: float):
        super().__init__("drive", ttl, drive.fetch)
        self.drive = drive
        self.base_ttl = ttl

    def get(self) -> dict:
        if self.data is not None and not self.drive.in_active_hours():
            return dict(self.data)
        budget = self.drive.budget
        if budget:
            per_day = max(budget.allowance() * self.MATRIX_SHARE, 0.01)
            self.ttl = max(self.base_ttl, self.drive.active_seconds_per_day() / per_day)
            if self.data is not None and not budget.can_spend(self.MATRIX_SHARE):
                return {**self.data, "paused": "Today's share of the monthly Google Maps limit is used"}
        return super().get()


# --------------------------------------------------------------------------
# Stock ticker (Yahoo Finance, through the yfinance package)
# --------------------------------------------------------------------------

class Stocks:
    """Latest price and change for each ticker, from Yahoo Finance. It needs no key, but it isn't an official
    service: prices can be delayed (up to about 15 minutes on some exchanges) and Yahoo can change or limit
    it without notice. Each symbol is its own request, so the refresh is stretched with the number of tickers."""

    def __init__(self, cfg: dict, tz: ZoneInfo):
        self.cfg = cfg
        self.tz = tz
        self.last: dict[str, dict] = {}   # symbol -> last good quote, kept through a failed refresh

    def interval(self) -> float:
        n = len(self.cfg.get("symbols") or [])
        base = max(float(self.cfg.get("refresh_seconds", 30)), n * 2.5)
        # US markets (with pre- and after-hours) run 4 AM to 8 PM Eastern on weekdays
        et = dt.datetime.now(ZoneInfo("America/New_York"))
        open_now = et.weekday() < 5 and 4 <= et.hour < 20
        return base if open_now else max(base, 900)

    def quote(self, sym: str) -> dict:
        yf = load_yfinance()
        try:
            info = yf.Ticker(sym).fast_info
            price, prev = _num(info["lastPrice"]), _num(info["previousClose"])
        except Exception as e:  # noqa: BLE001 — yfinance raises KeyError, rate-limit errors and network errors alike
            if "RateLimit" in e.__class__.__name__:
                raise UpstreamError("Yahoo is limiting requests for a moment. The last prices stay on the bar.") from None
            raise UpstreamError(f"Yahoo has no price for {sym}") from None
        if not price or price != price:                     # nothing, zero or NaN
            raise UpstreamError(f"Yahoo has no price for {sym}")
        change = price - prev if prev and prev == prev else 0.0
        pct = change / prev * 100 if prev and prev == prev else 0.0
        return {"symbol": sym, "label": stock_label(sym), "price": round(price, 4), "change": round(change, 4),
                "percent": round(pct, 2), "at": int(time.time())}

    def fetch(self) -> dict:
        if not self.cfg.get("enabled"):
            raise UpstreamError("The stock bar is off. Turn it on in Settings → Stocks")
        symbols = list(dict.fromkeys(normalize_symbol(x) for x in (self.cfg.get("symbols") or [])))
        if not symbols:
            raise UpstreamError("Add some tickers in Settings → Stocks")
        from concurrent.futures import ThreadPoolExecutor

        def one(sym):
            try:
                self.last[sym] = self.quote(sym)
            except UpstreamError as e:
                return str(e)
            return None

        # The first request sets up Yahoo's session (cookie and crumb). Doing it alone first avoids a race that
        # makes a few symbols fail at random on a cold start; then the rest go in parallel, and each failure gets one more try.
        result = {symbols[0]: one(symbols[0])}
        with ThreadPoolExecutor(max_workers=4) as pool:
            result.update(zip(symbols[1:], pool.map(one, symbols[1:])))
        for sym in symbols:
            if result[sym] and "limiting" not in result[sym]:     # don't push on when Yahoo has asked us to slow down
                result[sym] = one(sym)
        errors = [result[s] for s in symbols if result[s]]
        quotes = [self.last[s] for s in symbols if s in self.last]
        if not quotes:
            raise UpstreamError(errors[0] if errors else "No stock prices yet")
        out = {"quotes": quotes, "updated": int(time.time()), "refresh": round(self.interval())}
        if errors:
            out["missing"] = errors[:5]
        if len(errors) == len(symbols):          # every symbol failed this round: the prices shown are old, so say so
            out["stale"], out["error"] = True, errors[0]
        return out


class StockSource(Source):
    """Prices take Yahoo about ten seconds for a handful of tickers, so the TV is never made to wait for them:
    once there are prices, a stale answer is served at once and the refresh happens in the background."""

    def __init__(self, stocks: Stocks):
        super().__init__("stocks", stocks.interval(), stocks.fetch)
        self.stocks = stocks
        self.refreshing = False

    def _refresh(self) -> None:
        try:
            Source.get(self)
        except UpstreamError:
            pass                                # already recorded as the source's error
        finally:
            self.refreshing = False

    def get(self) -> dict:
        self.ttl = self.stocks.interval()
        if self.data is None:
            return super().get()                # the very first load has to wait
        if time.time() - self.fetched_at >= self.ttl and not self.refreshing:
            self.refreshing = True
            threading.Thread(target=self._refresh, daemon=True).start()
        out = dict(self.data)
        if self.error:
            out["stale"], out["error"] = True, self.error
        return out


# --------------------------------------------------------------------------
# Home Assistant (locks, doors, cameras)
# --------------------------------------------------------------------------

def ha_kind(entity_id: str, attrs: dict | None) -> str:
    domain = entity_id.split(".", 1)[0]
    dc = (attrs or {}).get("device_class")
    if domain in ("lock", "cover"):
        return domain
    if domain == "alarm_control_panel":
        return "alarm"
    if domain in ("person", "device_tracker"):
        return "person"
    if domain in ("light", "switch", "fan", "input_boolean"):
        return "power"
    if domain in ("climate", "vacuum", "sensor"):
        return domain
    if domain == "media_player":
        return "media"
    if domain == "binary_sensor":
        if dc in ("door", "window", "garage_door", "opening"):
            return "opening"
        if dc in ("motion", "occupancy", "presence", "moving"):
            return "motion"
        return "binary"
    return "other"


def _span(minutes: int) -> str:
    return f"{minutes} min" if minutes < 90 else f"{round(minutes / 60)} h"


def _number(state: str) -> str:
    try:
        v = float(state)
    except ValueError:
        return state
    return str(int(v)) if v == int(v) else f"{v:.1f}"


def ha_describe(kind: str, state: str, minutes: int, alert: int, attrs: dict | None = None, domain: str = "") -> dict:
    """What a tile says and how worried it should look: tone is good, warn, bad or neutral."""
    attrs = attrs or {}
    tone, text, icon = "neutral", state.replace("_", " ").capitalize(), "dot"
    dc = attrs.get("device_class")
    if state in ("unavailable", "unknown", "missing"):
        return {"text": "Unavailable", "tone": "neutral", "icon": "dot"}
    late = bool(alert) and minutes >= alert
    if kind == "lock":
        table = {"locked": ("Locked", "good"), "unlocked": ("Unlocked", "bad"), "locking": ("Locking", "warn"),
                 "unlocking": ("Unlocking", "warn"), "jammed": ("Jammed", "bad"), "open": ("Open", "bad"), "opening": ("Opening", "warn")}
        text, tone = table.get(state, (text, "neutral"))
        icon = "lock" if state in ("locked", "locking") else "unlock"
        if state in ("unlocked", "open") and late:
            text = f"{text} {_span(minutes)}"
    elif kind in ("opening", "cover"):
        is_open = state in ("on", "open", "opening", "closing")
        icon = "garage" if dc in ("garage_door", "garage", "gate") else "window" if dc == "window" else "door"
        text, tone = ("Open", "bad" if late else "warn") if is_open else ("Closed", "good")
        if state in ("opening", "closing"):
            text, tone = state.capitalize(), "warn"
        elif is_open and late:
            text = f"Open {_span(minutes)}"
    elif kind == "alarm":
        icon = "shield"
        if state.startswith("armed_"):
            text, tone = "Armed " + state[6:].replace("_", " "), "good"
        elif state == "disarmed":
            text, tone = "Off", "neutral"
        elif state in ("arming", "pending"):
            text, tone = state.capitalize(), "warn"
        elif state == "triggered":
            text, tone = "TRIGGERED", "bad"
    elif kind == "person":
        icon = "person"
        text, tone = ("Home", "good") if state == "home" else ("Away", "neutral") if state == "not_home" else (state.replace("_", " ").title(), "neutral")
    elif kind == "motion":
        icon = "motion"
        text, tone = ("Motion", "warn") if state == "on" else ("Clear", "neutral")
    elif kind == "power":
        icon = "bulb" if domain == "light" else "plug"
        text, tone = ("On", "warn") if state == "on" else ("Off", "neutral")
    elif kind == "climate":
        icon = "thermo"
        cur, action = attrs.get("current_temperature"), str(attrs.get("hvac_action") or "")
        text = (f"{_number(str(cur))}°" if cur is not None else "") + (f" · {action.capitalize()}" if action not in ("", "idle", "off") else "")
        text = text.strip(" ·") or ("Off" if state == "off" else state.replace("_", " ").capitalize())
    elif kind == "media":
        icon = "tv"
        text = {"playing": "Playing", "paused": "Paused", "idle": "Idle", "off": "Off", "standby": "Standby", "on": "On", "buffering": "Buffering"}.get(state, text)
    elif kind == "vacuum":
        text, tone = {"cleaning": ("Cleaning", "warn"), "docked": ("Docked", "good"), "returning": ("Returning", "neutral"),
                      "paused": ("Paused", "neutral"), "error": ("Error", "bad")}.get(state, (text, "neutral"))
    elif kind == "sensor":
        icon = "gauge"
        unit = str(attrs.get("unit_of_measurement") or "")
        text = f"{_number(state)}{'' if unit in ('°F', '°C', '%') else ' '}{unit}".strip()
        if dc == "battery":
            try:
                if float(state) <= 20:
                    text, tone = f"Low {text}", "warn"
            except ValueError:
                pass
    elif kind == "binary":
        on = state == "on"
        if dc == "moisture":
            icon, (text, tone) = "drop", (("Wet", "bad") if on else ("Dry", "good"))
        elif dc in ("smoke", "gas", "carbon_monoxide", "safety", "tamper", "problem"):
            icon, (text, tone) = "alert", ((dc.replace("_", " ").capitalize() if dc != "problem" else "Problem", "bad") if on else ("OK", "good"))
        elif dc == "battery":
            text, tone = ("Low", "warn") if on else ("OK", "good")
        elif dc == "lock":                                        # Home Assistant's lock-type sensor is "on" when unlocked
            icon, (text, tone) = ("unlock", ("Unlocked", "bad")) if on else ("lock", ("Locked", "good"))
        elif dc == "connectivity":
            text, tone = ("Online", "good") if on else ("Offline", "warn")
        else:
            text, tone = ("On", "warn") if on else ("Off", "neutral")
    return {"text": text, "tone": tone, "icon": icon}


class HomeAssistant:
    """Reads Home Assistant's REST API with a long-lived token the server keeps to itself.
    The TV never sees the token: pictures and states come through this server."""

    def __init__(self, cfg: dict, tz: ZoneInfo):
        self.cfg, self.tz = cfg, tz
        self.lock = threading.Lock()
        self.snaps: dict[str, tuple[float, bytes, str]] = {}
        self.motion_since: dict[str, float] = {}
        self.motion_last: dict[str, float] = {}

    def _get(self, path: str, timeout: float = 8.0, max_bytes: int = 30_000_000) -> bytes:
        base, token = self.cfg.get("url", ""), self.cfg.get("token", "")
        if not base or not token:
            raise UpstreamError("Connect Home Assistant in Settings → Smart home")
        try:
            return http_request(base.rstrip("/") + path, headers={"Authorization": f"Bearer {token}"}, timeout=timeout, max_bytes=max_bytes)
        except UpstreamError as e:
            if "HTTP 401" in str(e):
                raise UpstreamError("Home Assistant didn't accept the token. Make a new long-lived token in your Home Assistant profile.") from None
            raise

    def _json(self, path: str) -> Any:
        try:
            return json.loads(self._get(path))
        except ValueError:
            raise UpstreamError("Home Assistant sent something unexpected. Check the address.") from None

    def check(self) -> str:
        self._get("/api/")
        cfg = self._json("/api/config")
        return f"Connected to {cfg.get('location_name') or 'Home Assistant'} (Home Assistant {cfg.get('version', '?')})."

    def entities(self) -> list:
        """Everything Home Assistant has that can show as a tile, plus the cameras. `suggested` marks the
        security-minded ones (locks, doors, alarm, people, motion, cameras), which the picker offers first."""
        out = []
        rows = self._json("/api/states")
        if not isinstance(rows, list):
            raise UpstreamError("Home Assistant sent something unexpected. Check the address.")
        for st in rows:
            if not isinstance(st, dict):
                continue
            eid = st.get("entity_id")
            attrs = st.get("attributes") if isinstance(st.get("attributes"), dict) else {}
            if not isinstance(eid, str) or not ENTITY_ID.match(eid):
                continue
            domain = eid.split(".", 1)[0]
            kind = "camera" if domain == "camera" else ha_kind(eid, attrs)
            if kind == "other":
                continue
            out.append({"id": eid, "name": str(attrs.get("friendly_name") or eid)[:60], "domain": domain, "kind": kind,
                        "state": str(st.get("state", ""))[:30], "suggested": kind in HA_SUGGESTED and domain != "device_tracker"})
        out.sort(key=lambda x: (not x["suggested"], x["kind"], x["name"].lower()))
        return out[:3000]

    def _state(self, eid: str) -> dict | None:
        try:
            return self._json(f"/api/states/{eid}")
        except UpstreamError as e:
            if "HTTP 404" in str(e):
                return None
            raise

    def status(self) -> dict:
        """The status tiles: one line per chosen item, and a summary of whether anything needs attention."""
        from concurrent.futures import ThreadPoolExecutor
        ents = self.cfg.get("entities") or []
        if not self.cfg.get("enabled"):
            raise UpstreamError("Home Assistant is switched off. Turn it on in Settings → Smart home")
        if not ents:
            raise UpstreamError("No Home Assistant items are chosen. Add some in Settings → Smart home")
        alert = int(self.cfg.get("alert_minutes", 10))
        with ThreadPoolExecutor(max_workers=6) as pool:
            states = list(pool.map(lambda e: self._state(e["id"]), ents))
        now = dt.datetime.now(dt.timezone.utc)          # read after the states, so "for N minutes" never rounds down early
        items = []
        for e, st in zip(ents, states):
            attrs = (st or {}).get("attributes") or {}
            state = str((st or {}).get("state", "missing"))
            minutes = 0
            try:
                changed = dt.datetime.fromisoformat(str(st["last_changed"]).replace("Z", "+00:00"))
                minutes = max(int((now - changed).total_seconds() // 60), 0)
            except (KeyError, TypeError, ValueError):
                pass
            kind = ha_kind(e["id"], attrs)
            items.append({"id": e["id"], "name": e["name"] or str(attrs.get("friendly_name") or e["id"]), "kind": kind,
                          "state": state, "minutes": minutes, **ha_describe(kind, state, minutes, alert, attrs, e["id"].split(".", 1)[0])})
        bad = [i for i in items if i["tone"] == "bad"]
        secure_kinds = [i for i in items if i["kind"] in ("lock", "opening", "cover")]
        if bad:
            names = ", ".join(i["name"] for i in bad[:2]) + (f" and {len(bad) - 2} more" if len(bad) > 2 else "")
            summary = {"text": f"Needs attention: {names}", "tone": "bad"}
        elif any(i["tone"] == "warn" for i in secure_kinds):
            summary = {"text": "Something is open", "tone": "warn"}
        elif secure_kinds:
            summary = {"text": "All secure", "tone": "good"}
        else:
            summary = {"text": "", "tone": "neutral"}
        return {"items": items, "summary": summary, "updated": int(time.time())}

    def cameras_state(self) -> dict:
        """Which cameras have motion right now (or had it within the pop-up time), for the pop-up."""
        cams = self.cfg.get("cameras") or []
        if not self.cfg.get("enabled"):
            raise UpstreamError("Home Assistant is switched off. Turn it on in Settings → Smart home")
        if not cams:
            raise UpstreamError("No cameras are chosen. Add some in Settings → Smart home")
        grace = int(self.cfg["popup"]["seconds"])
        now = time.time()
        out = []
        for cam in cams:
            on = False
            for sid in cam.get("motion") or []:
                st = self._state(sid)
                on = on or bool(st and st.get("state") == "on")
            with self.lock:
                if on:
                    self.motion_since.setdefault(cam["id"], now)
                    self.motion_last[cam["id"]] = now
                active = on or (now - self.motion_last.get(cam["id"], 0) < grace and cam["id"] in self.motion_last)
                if not active:
                    self.motion_since.pop(cam["id"], None)
                since = self.motion_since.get(cam["id"])
            out.append({"id": cam["id"], "name": cam["name"] or cam["id"], "active": bool(active), "since": int(since) if since else None,
                        "popup": bool(cam.get("popup", True))})
        return {"cameras": out, "popup": bool(self.cfg["popup"]["enabled"]), "seconds": grace, "updated": int(now)}

    def snapshot(self, camera_id: str, width: int | None = None) -> tuple[bytes, str]:
        """A still picture from one of the chosen cameras, shared for a second so many viewers don't hammer the camera.
        With a width, Home Assistant is asked for a picture that size: a 4K camera's full snapshot is far more
        than a tile on a Pi needs."""
        if camera_id not in {c["id"] for c in (self.cfg.get("cameras") or [])} or not self.cfg.get("enabled"):
            raise PermissionError("That camera isn't on the dashboard")
        key = f"{camera_id}|{width or 0}"
        with self.lock:
            hit = self.snaps.get(key)
        if hit and time.time() - hit[0] < 1.0:
            return hit[1], hit[2]
        path = f"/api/camera_proxy/{camera_id}" + (f"?width={width}&height={round(width * 9 / 16)}" if width else "")
        blob = self._get(path, timeout=10, max_bytes=8_000_000)
        ctype = "image/png" if blob[:4] == b"\x89PNG" else "image/jpeg"
        with self.lock:
            self.snaps[key] = (time.time(), blob, ctype)
        return blob, ctype


VIRTUAL = re.compile(r"(https?://|zoom\.us|meet\.google|teams\.microsoft|webex)", re.I)


# --------------------------------------------------------------------------
# App
# --------------------------------------------------------------------------

PALETTE = ["#0A84FF", "#FF375F", "#30D158", "#FF9F0A", "#BF5AF2", "#64D2FF", "#FFD60A", "#FF453A", "#5E5CE6", "#8E8E93"]


def sleep_status(sched: dict, now: dt.datetime, override: dict | None = None) -> dict:
    """Is the TV's screen meant to be asleep right now, and until when?
    Rules sleep from `off` until `on` (the next morning if `on` is earlier) on the
    weekdays listed (0 = Monday); `override` is a manual sleep or wake with an end time."""
    if override and override.get("until", 0) > now.timestamp():
        until = dt.datetime.fromtimestamp(override["until"], now.tzinfo)
        return {"asleep": override["mode"] == "sleep", "reason": "manual", "change": until.isoformat()}
    tz = now.tzinfo
    spans = []
    if sched.get("enabled", True):
        for r in sched.get("rules", []):
            if not r.get("enabled", True):
                continue
            h, m = (int(x) for x in r["off"].split(":"))
            length = dt.timedelta(minutes=(hhmm_minutes(r["on"]) - hhmm_minutes(r["off"])) % 1440 or 1440)
            for back in range(-1, 9):                      # yesterday's sleep may still be running
                day = now.date() + dt.timedelta(days=back)
                if day.weekday() in r["days"]:
                    start = dt.datetime.combine(day, dt.time(h, m), tz)
                    spans.append((start, start + length))
    merged: list = []
    for st, en in sorted(spans):                           # touching or overlapping rules become one stretch
        if merged and st <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], en))
        else:
            merged.append((st, en))
    for st, en in merged:
        if st <= now < en:
            return {"asleep": True, "reason": "schedule", "change": en.isoformat()}
        if st > now:
            return {"asleep": False, "reason": "awake", "change": st.isoformat()}
    return {"asleep": False, "reason": "awake", "change": None}


def key_hash(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


class App:
    def __init__(self, store: Store, web_root: Path, state_dir: Path, offsite: bool = False):
        self.offsite = offsite
        self.store = store
        self.web_root = web_root.resolve()
        self.state_dir = state_dir
        self.sessions = Sessions()
        self.screen_nonce = 0          # bumped by "Reload the TV"
        self.kiosk_seen = 0.0          # last time the TV launcher checked in
        self.tv_frames: dict = {}      # frame rate the TV's page measured, for Settings
        self.started = time.time()
        self.sleep_override: dict | None = None   # manual "sleep now" / "wake now"; not saved, so a restart clears it
        self.routes: dict[str, tuple[float, dict]] = {}
        self.route_lock = threading.Lock()
        self.build()

    @property
    def config(self) -> dict:
        return self.store.data

    def build(self) -> None:
        c = self.config
        self.tz = ZoneInfo(c["timezone"])
        self.weather = Weather(c["tempest"], self.tz)
        self.calendars = Calendars(c["calendar"]["calendars"], self.tz, account=c["calendar"]["account"])
        self.maps_budget = MapsBudget(self.state_dir / "maps-usage.json", c["drive"].get("monthly_limit", 900), self.tz)
        self.drive = Drive(c["drive"], c["home"]["address"], self.tz, self.maps_budget)
        self.ha = HomeAssistant(c["ha"], self.tz)
        self.sources = {
            "weather": Source("weather", c["tempest"].get("refresh_seconds", 120), self.weather.fetch),
            "calendar": Source("calendar", c["calendar"].get("refresh_seconds", 600), self.calendars.fetch),
            "drive": DriveSource(self.drive, c["drive"].get("refresh_seconds", 600)),
            "stocks": StockSource(Stocks(c["stocks"], self.tz)),
            "home": Source("home", c["ha"].get("refresh_seconds", 10), self.ha.status),
            "cameras": Source("cameras", 3, self.ha.cameras_state),     # polled by the TV, so only runs while it's watching
        }
        with self.route_lock:
            self.routes.clear()

    def save(self, new_config: dict, quiet: bool = False) -> None:
        """quiet: keep the version the TV watches, so it doesn't reload (for things it never reads, like the sleep schedule)"""
        self.store.save(new_config, bump=not quiet)
        self.build()

    def backup(self) -> bytes:
        """A tar.gz of the data folder: settings (with every key), photos and counters."""
        import io
        import tarfile
        root = self.store.path.parent
        buf = io.BytesIO()
        total = 0
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            for f in sorted(root.rglob("*")):
                if f.is_file() and not f.name.endswith(".tmp"):
                    total += f.stat().st_size
                    if total > 200_000_000:
                        raise ValueError("The data folder is over 200 MB (photos?). Back it up from the server itself instead.")
                    tar.add(f, arcname=f"data/{f.relative_to(root)}")
        return buf.getvalue()

    def device_for(self, key: str | None) -> dict | None:
        if not key or not isinstance(key, str):
            return None
        mine = key_hash(key)
        for d in self.config["tv"].get("devices", []):
            if isinstance(d, dict) and d.get("hash") and hmac.compare_digest(mine, d["hash"]):
                return d
        return None

    def set_tv_layout(self, device_id: str, layout: Any) -> None:
        """Give one screen its own layout (None: it follows the shared one again)."""
        checked = _layout(layout)
        new = copy.deepcopy(self.config)
        for d in new["tv"]["devices"]:
            if isinstance(d, dict) and d.get("id") == device_id:
                if checked is None:
                    d.pop("layout", None)
                else:
                    d["layout"] = checked
                break
        else:
            raise ValueError("No screen has that key")
        self.save(new)

    def display_config(self, key: str | None = None) -> dict:
        c = self.config
        own = (self.device_for(key) or {}).get("layout")      # a screen with its own layout gets that one
        return {**c["display"], "units": {"temp": c["tempest"]["units"]["temp"].upper(),
                                          "wind": c["tempest"]["units"]["wind"],
                                          "precip": c["tempest"]["units"]["precip"]},
                "home": c["home"]["label"], "version": self.store.version, "layout": own or c["layout"],
                "ha": {"status": bool(c["ha"]["enabled"] and c["ha"]["entities"]),
                       "cameras": bool(c["ha"]["enabled"] and c["ha"]["cameras"]),
                       "popup": bool(c["ha"]["popup"]["enabled"])},
                "stocks": {"enabled": bool(c["stocks"]["enabled"] and c["stocks"]["symbols"]),
                           "symbols": c["stocks"]["symbols"]}}

    def route_for(self, destination: str, arrive_iso: str | None) -> dict:
        destination = " ".join(destination.split())[:300]
        if not destination or VIRTUAL.search(destination):
            return {"status": "no_route"}
        # Only route to places on the family calendar, so the key can't be spent on anything else
        cal = self.sources["calendar"].data or {}
        if destination not in {e.get("location") for e in cal.get("events", [])}:
            raise PermissionError("That place is not on the calendar")
        arrive = None
        if arrive_iso:
            try:
                arrive = dt.datetime.fromisoformat(arrive_iso)
            except ValueError:
                raise ValueError("arrive must be an ISO time") from None
            if arrive.tzinfo is None:
                arrive = arrive.replace(tzinfo=self.tz)
        key = f"{destination}|{arrive_iso or ''}"
        # Traffic matters most close to leaving: check every 5 min within 2 hours, else every 20
        soon = arrive is None or arrive - dt.datetime.now(self.tz) < dt.timedelta(hours=2)
        ttl = 300 if soon else 1200
        if self.maps_budget.limit < 5000:  # a tight monthly cap: check less often
            ttl *= 3
        with self.route_lock:
            hit = self.routes.get(key)
            if hit and time.time() - hit[0] < ttl:
                return hit[1]
        if not self.maps_budget.can_spend():
            # Over today's share of the monthly limit: keep the last answer, however old
            return {**hit[1], "paused": True} if hit else {"status": "no_route", "reason": "limit"}
        result = self.drive.route(destination, arrive)
        with self.route_lock:
            self.routes = {k: v for k, v in self.routes.items() if time.time() - v[0] < 6 * 3600}
            self.routes[key] = (time.time(), result)
        return result

    # --- Google account calendars

    def refresh_calendar_list(self) -> dict:
        """Ask Google for every calendar in the account and merge it into settings:
        known calendars keep their name, colour and on/off; new ones arrive switched on."""
        found = discover_calendars(self.config["calendar"]["account"])
        c = copy.deepcopy(self.config)
        old = {cal.get("href"): cal for cal in c["calendar"]["calendars"] if cal.get("href")}
        links = [cal for cal in c["calendar"]["calendars"] if not cal.get("href")]
        merged, added = [], 0
        for i, f in enumerate(found):
            if f["href"] in old:
                merged.append(old[f["href"]])
            else:
                added += 1
                merged.append({"id": secrets.token_hex(4), "name": f["name"][:40], "href": f["href"],
                               "color": f["color"] or PALETTE[i % len(PALETTE)], "enabled": True})
        c["calendar"]["calendars"] = merged + links
        self.save(c)
        return {"found": len(found), "added": added, "removed": len(set(old) - {f["href"] for f in found})}

    # --- family photos

    def photo_dir(self) -> Path:
        folder = self.config["photos"].get("folder")
        return Path(folder) if folder else self.state_dir.parent / "photos"

    def photos(self) -> list[str]:
        try:
            return sorted(p.name for p in self.photo_dir().iterdir()
                          if p.suffix.lower() in PHOTO_TYPES and not p.name.startswith("."))
        except OSError:
            return []

    def photo_path(self, name: str) -> Path | None:
        if "/" in name or "\\" in name or name.startswith(".") or Path(name).suffix.lower() not in PHOTO_TYPES:
            return None
        p = self.photo_dir() / name
        return p if p.is_file() else None

    # --- the TV screen

    def screen_url(self) -> str:
        """This dashboard's own page. On the Pi that's a full localhost link; off-site
        it's a path the launcher opens through /tv on the server's address."""
        return "/?mode=live" if self.offsite else f"http://localhost:{self.config.get('port', 8080)}/?mode=live"

    def tv_key_ok(self, key: str | None) -> bool:
        """Any TV key still on the list works. Each screen has its own, so one can be stopped alone."""
        if not key or not isinstance(key, str):
            return False
        mine = key_hash(key)
        tv = self.config["tv"]
        stored = [tv["key_hash"]] + [d.get("hash", "") for d in tv.get("devices", []) if isinstance(d, dict)]
        ok = False
        for h in stored:
            if h and hmac.compare_digest(mine, h):
                ok = True
        return ok

    def new_tv_key(self, name: str = "TV") -> str:
        """Make a key for one more screen. The others keep working."""
        name = " ".join(str(name or "TV").split())[:40] or "TV"
        new = copy.deepcopy(self.config)
        devices = [d for d in new["tv"].get("devices", []) if isinstance(d, dict)]
        if len(devices) >= 12:
            raise ValueError("That's plenty of TV keys. Stop one you no longer use first.")
        key = secrets.token_urlsafe(24)
        devices.append({"id": secrets.token_hex(4), "name": name, "hash": key_hash(key),
                        "made": dt.datetime.now(self.tz).strftime("%Y-%m-%d")})
        new["tv"]["devices"] = devices
        self.store.save(new)  # no rebuild needed
        return key

    def revoke_tv_key(self, device_id: str) -> None:
        """Stop one screen's key; a screen using it signs out, the rest carry on."""
        new = copy.deepcopy(self.config)
        if device_id == "first":
            new["tv"]["key_hash"] = ""
        else:
            new["tv"]["devices"] = [d for d in new["tv"].get("devices", []) if not (isinstance(d, dict) and d.get("id") == device_id)]
        self.store.save(new)

    def sleep_now(self) -> dict:
        return sleep_status(self.config["schedule"], dt.datetime.now(self.tz), self.sleep_override)

    def set_sleep_override(self, mode: str, hours: float) -> dict:
        if mode == "clear":
            self.sleep_override = None
        elif mode in ("sleep", "wake"):
            if isinstance(hours, bool) or not isinstance(hours, (int, float)) or not 0.25 <= hours <= 24:
                raise ValueError("Choose between a quarter of an hour and 24 hours")
            self.sleep_override = {"mode": mode, "until": time.time() + hours * 3600}
        else:
            raise ValueError("Mode must be sleep, wake or clear")
        return self.sleep_now()

    def stock_search(self, q: str) -> list:
        """Look a company, fund or index up by name or ticker, through Yahoo's search."""
        q = " ".join(q.split())[:60]
        if len(q) < 2:
            return []
        try:
            found = load_yfinance().Search(q, max_results=20, news_count=0).quotes
        except Exception:  # noqa: BLE001 — a network or Yahoo problem, whatever its type
            raise UpstreamError("Yahoo's search isn't answering right now. Try again in a moment.") from None
        kinds = {"EQUITY": "Stock", "ETF": "ETF", "MUTUALFUND": "Mutual fund", "INDEX": "Index", "CRYPTOCURRENCY": "Crypto"}
        out = []
        for r in found or []:
            sym = str(r.get("symbol") or "").upper()
            kind = kinds.get(str(r.get("quoteType") or ""))
            if not kind or not SYMBOL.match(sym):               # futures, options and the like are noise on a ticker bar
                continue
            where = str(r.get("exchDisp") or r.get("exchange") or "")
            out.append({"symbol": sym, "name": str(r.get("shortname") or r.get("longname") or "")[:80],
                        "type": f"{kind} · {where}" if where else kind})
        return out[:20]

    def info(self) -> dict:
        return {"version": APP_VERSION, "python": platform.python_version(), "host": platform.node(),
                "offsite": self.offsite, "uptime": int(time.time() - self.started),
                "timezone": self.config["timezone"], "dataDir": str(self.store.path.parent),
                "port": self.config.get("port", 8080), "now": dt.datetime.now(self.tz).isoformat()}

    def kiosk_online(self) -> bool:
        return time.time() - self.kiosk_seen < 30

    def reload_tv(self) -> dict:
        """The launcher on the Pi checks /api/screen-url every few seconds and
        reopens the browser when the answer changes."""
        self.screen_nonce += 1
        if self.kiosk_online():
            return {"restarted": True}
        return {"restarted": False, "reason": "The TV launcher hasn't checked in. It starts after the Pi reboots with the launcher installed."}

    def status(self) -> dict:
        cal_status = []
        for cal in self.config["calendar"]["calendars"]:
            st = self.calendars.status.get(cal["id"])
            if not cal.get("enabled", True):
                st = {"ok": None, "note": "Hidden"}
            elif not cal.get("ics_url") and not cal.get("href"):
                st = {"ok": None, "note": "Needs its link"}
            cal_status.append({"id": cal["id"], **(st or {"ok": None, "note": "Not loaded yet"})})
        return {
            "sources": {k: s.status() for k, s in self.sources.items()},
            "calendars": cal_status,
            "screen": {"kiosk": self.kiosk_online(), "frames": self.tv_frames or None,
                       "sleep": self.sleep_now()},
            "info": self.info(),
            "photos": len(self.photos()),
            "maps": self.maps_budget.status(),
            "version": self.store.version,
        }

    def test(self, kind: str, patch: dict) -> dict:
        """Try a connection with the values on the admin form, without saving them."""
        c = apply_settings(self.config, patch or {})
        tz = ZoneInfo(c["timezone"])
        try:
            if kind == "weather":
                w = Weather(c["tempest"], tz).fetch()
                return {"ok": True, "message": f"Connected. It's {round(w['current']['temp'] or 0)}°{w['units']['temp']} at {w['station']}."}
            if kind == "calendar":
                cal_id = (patch or {}).get("id")
                cal = next((x for x in c["calendar"]["calendars"] if x["id"] == cal_id), None)
                if cal is None and patch.get("ics_url"):
                    cal = {"id": "test", "name": "Test", "color": "#0A84FF", "ics_url": _url(patch["ics_url"], "Link")}
                if not cal or not (cal.get("ics_url") or cal.get("href")):
                    return {"ok": False, "message": "Paste the calendar's link first."}
                cals = Calendars([cal], tz, account=c["calendar"]["account"])
                today = dt.datetime.now(tz).date()
                got = cals.fetch_one(cal, dt.datetime.combine(today, dt.time(), tz),
                                     dt.datetime.combine(today + dt.timedelta(days=14), dt.time(), tz))
                return {"ok": True, "message": f"Connected. {len(got)} events in the next two weeks."}
            if kind == "drive":
                d = Drive(c["drive"], c["home"]["address"], tz, self.maps_budget)
                places = c["drive"]["places"] or [{"name": "Test", "address": c["home"]["address"], "icon": "pin"}]
                d.cfg = {**d.cfg, "places": places}
                res = d.fetch()
                found = [p for p in res["places"] if p["status"] == "ok"]
                return {"ok": bool(found), "message": f"Connected. Found routes to {len(found)} of {len(places)} places."
                        if found else "Google answered, but found no route. Check the addresses."}
            if kind == "ha":
                return {"ok": True, "message": HomeAssistant(c["ha"], tz).check()}
            if kind == "stocks":
                st = Stocks({**c["stocks"], "enabled": True}, tz)
                if not st.cfg.get("symbols"):
                    return {"ok": False, "message": "Add at least one ticker first."}
                q = st.quote(normalize_symbol(st.cfg["symbols"][0]))
                return {"ok": True, "message": f"Yahoo answered. {q['label']} is {q['price']:,.2f} ({q['percent']:+.2f}%)."}
        except (UpstreamError, ValueError) as e:
            return {"ok": False, "message": str(e)}
        raise ValueError("Unknown test")


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------

def is_private(ip: str) -> bool:
    """Loopback or a Docker bridge: where a reverse proxy on this machine connects from."""
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return a.is_loopback or (a.version == 4 and a in ipaddress.ip_network("172.16.0.0/12"))


def is_loopback(ip: str) -> bool:
    try:
        return ipaddress.ip_address(ip).is_loopback
    except ValueError:
        return False


def make_handler(app: App):
    class Handler(BaseHTTPRequestHandler):
        server_version = "FamilyDashboard/2.0"
        timeout = 30            # a client that stops talking is let go, so it can't hold a thread for ever

        def log_message(self, fmt, *args):
            log.debug("%s - %s", self.address_string(), fmt % args)

        # --- plumbing

        def _send(self, status: int, body: bytes, ctype: str, cache: str = "no-store", headers: dict | None = None):
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", cache)
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json(self, status: int, obj: Any, headers: dict | None = None):
            self._send(status, json.dumps(obj).encode(), "application/json; charset=utf-8", headers=headers)

        def _cookie(self, name: str = "fd_session") -> str | None:
            for part in (self.headers.get("Cookie") or "").split(";"):
                k, _, v = part.strip().partition("=")
                if k == name:
                    return v
            return None

        @property
        def ip(self) -> str:
            # Behind a proxy on the same machine, the proxy appends the real address last.
            # Only a proxy may say so: a browser connecting directly can't set it.
            if app.offsite and self.headers.get("X-Forwarded-For") and is_private(self.client_address[0]):
                return self.headers["X-Forwarded-For"].split(",")[-1].strip()
            return self.client_address[0]

        def admin(self) -> bool:
            return app.sessions.valid(self._cookie())

        def tv(self) -> bool:
            """The TV itself: the Pi's own browser (on the Pi), or a TV key (off-site)."""
            if not app.offsite and is_loopback(self.client_address[0]):
                return True
            auth = self.headers.get("Authorization") or ""
            key = auth[7:].strip() if auth.startswith("Bearer ") else self._cookie("fd_tv")
            return app.tv_key_ok(key)

        def cookie_attrs(self, max_age: int) -> str:
            # Secure only when the request really came over HTTPS (through a proxy);
            # a server elsewhere in the house is plain http, and browsers drop
            # Secure cookies there.
            https = app.offsite and self.headers.get("X-Forwarded-Proto", "").lower() == "https"
            return f"Path=/; HttpOnly; SameSite=Strict; Max-Age={max_age}" + ("; Secure" if https else "")

        def body(self) -> Any:
            n = int(self.headers.get("Content-Length") or 0)
            if n < 0 or n > 256_000:
                raise ValueError("Request too large")
            try:
                return json.loads(self.rfile.read(n) or b"{}")
            except ValueError:
                raise ValueError("Request body must be JSON") from None

        # --- routing

        def _guarded(self, work):
            try:
                work()
            except (BrokenPipeError, ConnectionResetError, TimeoutError):
                pass                                        # the other end went away
            except OSError as e:
                log.error("Disk problem handling %s: %s", self.path.split("?")[0], e)
                try:
                    self._json(500, {"error": f"The server couldn't write to its data folder ({e.strerror or 'disk error'}). Nothing was changed."})
                except Exception:  # noqa: BLE001
                    pass
            except Exception:  # noqa: BLE001 — never leave a browser or the TV without an answer
                log.exception("Unexpected error handling %s %s", self.command, self.path.split("?")[0])
                try:
                    self._json(500, {"error": "Something went wrong on the server. It has been logged."})
                except Exception:  # noqa: BLE001
                    pass

        def do_HEAD(self):
            self.do_GET()

        def do_GET(self):
            self._guarded(self._get)

        def do_POST(self):
            self._guarded(self._post)

        def _get(self):
            url = urllib.parse.urlsplit(self.path)
            path, q = url.path, urllib.parse.parse_qs(url.query)

            if path.startswith("/admin/api/"):
                return self.admin_get(path[len("/admin/api/"):], q)
            if path == "/admin":
                return self._send(301, b"", "text/plain", headers={"Location": "/admin/"})

            if path.startswith("/api/") or path == "/healthz" or path.startswith("/photos/"):
                if not (self.tv() or self.admin() or app.config["display"]["shareOnNetwork"]):
                    return self._json(401, {"error": "Sign in at /admin to view the dashboard from this device."})
                # Lock states and camera pictures are for the TV and the settings only, never "Others can view"
                if (path in ("/api/home", "/api/cameras") or path.startswith("/api/ha/")) and not (self.tv() or self.admin()):
                    return self._json(401, {"error": "Only the TV or a signed-in settings session can see this."})
                return self.api_get(path, q)
            return self.static(path)

        def _post(self):
            url = urllib.parse.urlsplit(self.path)
            path = url.path
            if not path.startswith("/admin/api/") and path != "/api/tv/pair":
                return self._json(404, {"error": "Not found"})
            # Browsers can't send this header cross-site without a preflight we never answer
            if self.headers.get("X-Dashboard") != "1":
                return self._json(403, {"error": "Missing request header"})
            if path == "/api/tv/pair":
                return self.pair()
            if path == "/admin/api/photos/upload":
                return self.upload(urllib.parse.parse_qs(url.query).get("name", [""])[0])
            try:
                return self.admin_post(path[len("/admin/api/"):], self.body())
            except ValueError as e:
                return self._json(400, {"error": str(e)})

        def api_get(self, path: str, q: dict):
            if path == "/healthz":
                return self._json(200, app.status())
            if path.startswith("/photos/"):
                p = app.photo_path(urllib.parse.unquote(path[8:]))
                if not p:
                    return self._json(404, {"error": "No such photo"})
                return self._send(200, p.read_bytes(), PHOTO_TYPES[p.suffix.lower()], "public, max-age=86400")
            if path.startswith("/api/ha/camera/"):
                cid = urllib.parse.unquote(path[len("/api/ha/camera/"):])
                cid = cid[:-4] if cid.endswith(".jpg") else cid
                w = (q.get("w") or [""])[0]
                try:
                    blob, ctype = app.ha.snapshot(cid, int(w) if w.isdigit() and 160 <= int(w) <= 1920 else None)
                except PermissionError as e:
                    return self._json(404, {"error": str(e)})
                except UpstreamError as e:
                    return self._json(502, {"error": str(e)})
                return self._send(200, blob, ctype)
            name = path[5:]
            if name == "config":
                auth = self.headers.get("Authorization") or ""
                return self._json(200, app.display_config(auth[7:].strip() if auth.startswith("Bearer ") else self._cookie("fd_tv")))
            if name == "version":
                f = (q.get("fps") or [""])[0]
                if f.isdigit() and self.tv():
                    g = (q.get("glass") or [""])[0]
                    app.tv_frames = {"fps": min(int(f), 240), "perf": (q.get("perf") or [""])[0][:8], "at": int(time.time()),
                                     "withGlass": min(int(g), 240) if g.isdigit() else None}
                return self._json(200, {"version": app.store.version})
            if name == "screen-url":
                if not self.tv():
                    return self._json(403, {"error": "Only the TV itself can ask this"})
                app.kiosk_seen = time.time()
                asleep = "asleep" if app.sleep_now()["asleep"] else "awake"
                return self._send(200, f"{app.screen_url()}\n{app.screen_nonce}\n{asleep}\n".encode(), "text/plain; charset=utf-8")
            if name == "photos":
                return self._json(200, {"photos": app.photos()})
            if name == "route":
                try:
                    return self._json(200, app.route_for((q.get("to") or [""])[0], (q.get("arrive") or [None])[0]))
                except PermissionError as e:
                    return self._json(403, {"error": str(e)})
                except ValueError as e:
                    return self._json(400, {"error": str(e)})
                except UpstreamError as e:
                    return self._json(502, {"error": str(e)})
            src = app.sources.get(name)
            if not src:
                return self._json(404, {"error": "Unknown endpoint"})
            try:
                return self._json(200, src.get())
            except UpstreamError as e:
                return self._json(502, {"error": str(e)})

        def pair(self):
            """The TV's browser trades the TV key (kept in the page address after #,
            which never leaves the Pi) for a cookie, then opens the dashboard."""
            if app.sessions.locked_out(self.ip):
                return self._json(429, {"error": "Too many tries. Wait five minutes."})
            try:
                key = self.body().get("key")
            except (ValueError, AttributeError):
                key = None
            if not isinstance(key, str) or not app.tv_key_ok(key):
                app.sessions.fail(self.ip)
                return self._json(401, {"error": "That TV key isn't right. Make a new one in Settings → On the TV."})
            return self._json(200, {"ok": True},
                              headers={"Set-Cookie": f"fd_tv={key}; {self.cookie_attrs(400 * 86400)}"})

        def admin_get(self, name: str, q: dict | None = None):
            has_pw = bool(app.config["admin"]["password_hash"])
            if name == "session":
                return self._json(200, {"setupNeeded": not has_pw, "signedIn": self.admin()})
            if not self.admin():
                return self._json(401, {"error": "Sign in first"})
            if name == "settings":
                return self._json(200, public_settings(app.config))
            if name == "status":
                return self._json(200, app.status())
            if name == "backup":
                try:
                    blob = app.backup()
                except ValueError as e:
                    return self._json(400, {"error": str(e)})
                stamp = dt.datetime.now(app.tz).strftime("%Y-%m-%d")
                return self._send(200, blob, "application/gzip",
                                  headers={"Content-Disposition": f'attachment; filename="family-dashboard-backup-{stamp}.tar.gz"'})
            if name == "ha/entities":
                try:
                    return self._json(200, {"entities": app.ha.entities()})
                except UpstreamError as e:
                    return self._json(502, {"error": str(e)})
            if name == "stocks/search":
                try:
                    return self._json(200, {"results": app.stock_search((q or {}).get("q", [""])[0])})
                except UpstreamError as e:
                    return self._json(502, {"error": str(e)})
            return self._json(404, {"error": "Not found"})

        def admin_post(self, name: str, data: Any):
            if not isinstance(data, dict):
                raise ValueError("Request body must be an object")
            cookie_attrs = self.cookie_attrs(2592000)

            if name in ("setup", "login"):
                if app.sessions.locked_out(self.ip):
                    return self._json(429, {"error": "Too many tries. Wait five minutes and try again."})
                pw = data.get("password")
                if not isinstance(pw, str):
                    raise ValueError("Enter a password")
                stored = app.config["admin"]["password_hash"]
                if name == "setup":
                    if stored:
                        return self._json(409, {"error": "A password is already set. Sign in instead."})
                    if len(pw) < 8:
                        raise ValueError("Use at least 8 characters")
                    new = copy.deepcopy(app.config)
                    new["admin"]["password_hash"] = hash_password(pw)
                    app.store.save(new)  # no rebuild needed: only the password changed
                elif not stored or not check_password(pw, stored):
                    app.sessions.fail(self.ip)
                    return self._json(401, {"error": "That password didn't match."})
                token = app.sessions.create()
                return self._json(200, {"ok": True}, headers={"Set-Cookie": f"fd_session={token}; {cookie_attrs}"})

            if not self.admin():
                return self._json(401, {"error": "Sign in first"})

            if name == "logout":
                app.sessions.drop(self._cookie())
                return self._json(200, {"ok": True}, headers={"Set-Cookie": "fd_session=; Path=/; Max-Age=0"})
            if name == "password":
                if not check_password(data.get("current", ""), app.config["admin"]["password_hash"]):
                    return self._json(401, {"error": "Your current password didn't match."})
                if not isinstance(data.get("new"), str) or len(data["new"]) < 8:
                    raise ValueError("Use at least 8 characters")
                new = copy.deepcopy(app.config)
                new["admin"]["password_hash"] = hash_password(data["new"])
                app.store.save(new)
                return self._json(200, {"ok": True})
            if name == "settings":
                app.save(apply_settings(app.config, data), quiet=set(data) <= {"schedule"})
                return self._json(200, {"ok": True, "settings": public_settings(app.config)})
            if name == "screen/reload":
                return self._json(200, app.reload_tv())
            if name == "screen/sleep":
                return self._json(200, app.set_sleep_override(str(data.get("mode", "")), data.get("hours", 2)))
            if name == "tv/key":
                # Shown once; only its hash is kept
                return self._json(200, {"key": app.new_tv_key(data.get("name") or "TV"), "name": " ".join(str(data.get("name") or "TV").split())[:40],
                                        "settings": public_settings(app.config)})
            if name == "tv/layout":
                app.set_tv_layout(str(data.get("id", "")), data.get("layout"))
                return self._json(200, {"settings": public_settings(app.config)})
            if name == "tv/revoke":
                app.revoke_tv_key(str(data.get("id", "")))
                return self._json(200, {"settings": public_settings(app.config)})
            if name == "calendar/refresh":
                if data.get("email") or data.get("app_password"):
                    app.save(apply_settings(app.config, {"calendar": {"account": {
                        k: data[k] for k in ("email", "app_password") if data.get(k)}}}))
                try:
                    result = app.refresh_calendar_list()
                except UpstreamError as e:
                    return self._json(502, {"error": str(e)})
                app.sources["calendar"].fetched_at = 0  # load events now, not in 10 minutes
                return self._json(200, {**result, "settings": public_settings(app.config)})
            if name == "photos/delete":
                p = app.photo_path(str(data.get("name", "")))
                if not p:
                    return self._json(404, {"error": "No such photo"})
                try:
                    p.unlink()
                except OSError as e:
                    return self._json(400, {"error": f"Couldn't delete it: {e.strerror}"})
                return self._json(200, {"photos": app.photos()})
            if name.startswith("test/"):
                return self._json(200, app.test(name[5:], data))
            return self._json(404, {"error": "Not found"})

        def upload(self, name: str):
            if not self.admin():
                return self._json(401, {"error": "Sign in first"})
            ext = Path(name).suffix.lower()
            if ext not in PHOTO_TYPES:
                return self._json(400, {"error": "Photos must be JPEG, PNG or WebP"})
            n = int(self.headers.get("Content-Length") or 0)
            if not 0 < n <= 15_000_000:
                return self._json(400, {"error": "Each photo must be under 15 MB"})
            body = self.rfile.read(n)
            # Name from content, so uploading the same photo twice doesn't make a copy
            safe = hashlib.sha256(body).hexdigest()[:16] + ext
            folder = app.photo_dir()
            try:
                folder.mkdir(parents=True, exist_ok=True)
                (folder / safe).write_bytes(body)
            except OSError as e:
                return self._json(400, {"error": f"Couldn't save to {folder}: {e.strerror}"})
            return self._json(200, {"name": safe, "photos": app.photos()})

        def static(self, path: str):
            rel = urllib.parse.unquote(path).lstrip("/") or "index.html"
            if "\x00" in rel:
                return self._json(404, {"error": "Not found"})
            target = (app.web_root / rel).resolve()
            if app.web_root not in target.parents and target != app.web_root:
                return self._json(403, {"error": "Forbidden"})
            if target.is_dir():
                target = target / "index.html"
            if not target.is_file():
                return self._json(404, {"error": "Not found"})
            ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype == "application/javascript":
                ctype += "; charset=utf-8"
            cache = "public, max-age=86400" if target.suffix == ".woff2" else "no-cache"
            self._send(200, target.read_bytes(), ctype, cache)

    return Handler


def warm_names(app: "App") -> list:
    """The feeds worth keeping fresh in the background, so the TV and the settings never wait for them."""
    c = app.config
    return (["weather", "calendar", "drive"]
            + (["stocks"] if c["stocks"]["enabled"] and c["stocks"]["symbols"] else []))


def keep_warm(app: App) -> None:
    """Refresh in the background so the TV and the settings page always have
    current data. Drive times are only paid for while the TV shows this dashboard."""
    while True:
        for name in warm_names(app):
            try:
                app.sources[name].get()
            except Exception:  # noqa: BLE001 — already logged and shown in status
                pass
        time.sleep(60)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="Family Dashboard server")
    ap.add_argument("--config", default=str(HERE / "config.json"))
    ap.add_argument("--web-root", default=str(DEFAULT_WEB_ROOT))
    ap.add_argument("--state-dir", default=str(HERE / "state"))
    ap.add_argument("--host")
    ap.add_argument("--port", type=int)
    ap.add_argument("--offsite", action="store_true", default=os.environ.get("DASHBOARD_OFFSITE", "") in ("1", "true", "yes"),
                    help="running away from the Pi (e.g. Docker behind HTTPS): don't trust localhost; the TV uses its key")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    mimetypes.add_type("font/woff2", ".woff2")
    mimetypes.add_type("application/javascript", ".js")

    store = Store(Path(args.config), HERE / "config.seed.json")
    state = Path(args.state_dir)
    state.mkdir(parents=True, exist_ok=True)
    app = App(store, Path(args.web_root), state, offsite=args.offsite)
    host = args.host or store.data.get("host", "0.0.0.0")
    port = args.port or int(store.data.get("port", 8080))
    store.data["port"] = port
    threading.Thread(target=keep_warm, args=(app,), daemon=True).start()
    httpd = ThreadingHTTPServer((host, port), make_handler(app))
    log.info("Family Dashboard on http://%s:%d (settings at /admin/)%s", host, port,
             " — off-site mode: the TV signs in with its key" if args.offsite else "")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
