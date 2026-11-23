/* Demo data, generated relative to "now", in the exact shapes the Pi server
   returns. The TV labels it "Demo data" so it is never mistaken for real. */
(function () {
  "use strict";

  const ymd = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  const startOfDay = (d) => { const x = new Date(d); x.setHours(0, 0, 0, 0); return x; };
  const addDays = (d, n) => { const x = new Date(d); x.setDate(x.getDate() + n); return x; };
  const at = (day, h, m) => { const x = new Date(day); x.setHours(h, m || 0, 0, 0); return x; };
  const hash = (s) => [...s].reduce((a, c) => (a * 31 + c.charCodeAt(0)) >>> 0, 7);

  // ---------- Weather ----------

  const DAYS = [
    ["partly", 64, 45, 0], ["rainy", 61, 53, 70], ["rainy", 60, 51, 60], ["partly", 72, 43, 0],
    ["clear", 66, 55, 0], ["partly", 73, 51, 10], ["cloudy", 57, 41, 20], ["clear", 54, 35, 0],
    ["partly", 58, 38, 0], ["possibly-rainy", 62, 47, 40],
  ];
  const TEXT = { clear: "Clear", partly: "Partly Cloudy", cloudy: "Cloudy", rainy: "Rain Likely", "possibly-rainy": "Rain Possible" };
  const icon = (k, night) => k === "clear" ? (night ? "clear-night" : "clear-day")
    : k === "partly" ? (night ? "partly-cloudy-night" : "partly-cloudy-day")
    : k === "possibly-rainy" ? (night ? "possibly-rainy-night" : "possibly-rainy-day") : k;

  function weather(now) {
    const today = startOfDay(now);
    const night = now.getHours() < 7 || now.getHours() >= 18;
    const h = now.getHours();
    const [k, hi, lo] = DAYS[0];
    const temp = Math.round(lo + (hi - lo) * (Math.cos(((h - 15) / 24) * 2 * Math.PI) + 1) / 2);
    return {
      station: "Backyard station",
      units: { temp: "F", wind: "mph", precip: "in" },
      current: {
        temp, feelsLike: temp - 1, conditions: TEXT[k], icon: icon(k, night),
        humidity: 71, windAvg: 4, windGust: 9, windDir: "SW", precipToday: 0, uv: night ? 0 : 3,
        pressure: 29.7, pressureTrend: "steady",
      },
      daily: DAYS.map(([kind, high, low, pop], i) => {
        const d = addDays(today, i);
        return { date: ymd(d), high, low, icon: icon(kind, false), conditions: TEXT[kind], pop,
          sunrise: Math.floor(at(d, 6, 59).getTime() / 1000), sunset: Math.floor(at(d, 17, 6).getTime() / 1000) };
      }),
      updated: Math.floor(now.getTime() / 1000) - 45,
    };
  }

  // ---------- Calendar ----------

  const CALENDARS = [
    { name: "Jordan", color: "#FF9F0A" },
    { name: "Sam", color: "#FF375F" },
    { name: "Alex", color: "#30D158" },
    { name: "Riley", color: "#64D2FF" },
    { name: "Casey", color: "#BF5AF2" },
    { name: "Taylor", color: "#FFD60A" },
    { name: "Kids", color: "#0A84FF" },
    { name: "US holidays", color: "#8E8E93" },
  ];
  const colorOf = (n) => CALENDARS.find((c) => c.name === n).color;

  const PLACES = {
    rehab: "Riverside Clinic, 20 Clinic Road, Springfield",
    store: "Corner Market, 11 Market Street, Springfield",
    church: "Community Hall, 85 Hill Street, Springfield",
    pool: "Community Pool, 128 Camp Drive, Springfield",
    eye: "Clear View Optometry, 150 Oak Avenue, Springfield",
    ortho: "Bright Smiles Dental, 340 Elm Way, Springfield",
    school: "Central High School, 310 School Road, Springfield",
  };

  function timed(title, cal, day, h, m, mins, location) {
    const s = at(day, h, m);
    return { title, calendar: cal, color: colorOf(cal), allDay: false, location: location || "",
      start: s.toISOString(), end: new Date(s.getTime() + (mins || 60) * 60000).toISOString() };
  }

  function allDay(title, cal, day, days) {
    return { title, calendar: cal, color: colorOf(cal), allDay: true, location: "", start: ymd(day), end: ymd(addDays(day, days || 1)) };
  }

  function thanksgiving(year) {
    const first = new Date(year, 10, 1);
    return new Date(year, 10, 1 + ((4 - first.getDay() + 7) % 7) + 21);
  }

  function calendar(now) {
    const today = startOfDay(now);
    const events = [];
    for (let i = -1; i < 15; i++) {
      const d = addDays(today, i);
      const w = d.getDay();
      if (w === 1 || w === 3 || w === 5) events.push(timed("Physio", "Jordan", d, 8, 0, 60, PLACES.rehab));
      if (w === 2 || w === 4 || w === 6) events.push(timed("Shop shift", "Alex", d, 16, 0, 300, PLACES.store));
      if (w === 1 || w === 3 || w === 6) events.push(timed("Shop shift", "Riley", d, 17, 0, 300, PLACES.store));
      if (w === 0 || w === 2) events.push(timed("Shop shift", "Casey", d, 10, 0, 240, PLACES.store));
      if (w === 2) events.push(timed("Take out trash and recycling", "Kids", d, 20, 0, 15));
      if (w === 0) events.push(timed("Youth Group", "Kids", d, 17, 30, 90, PLACES.church));
      if (w === 1 || w === 3) events.push(timed("Swim practice", "Riley", d, 6, 0, 75, PLACES.pool));
      if (w === 2 || w === 4) events.push(timed("Swim practice", "Casey", d, 6, 0, 75, PLACES.pool));
    }
    const rel = (n) => addDays(today, n);
    events.push(timed("Eye appointment", "Sam", rel(2), 8, 30, 60, PLACES.eye));
    events.push(timed("Club meeting", "Sam", rel(1), 19, 0, 90, PLACES.school));
    events.push(timed("Nutrition consult", "Jordan", rel(3), 9, 0, 60));
    events.push(timed("Chiropractor", "Sam", rel(4), 10, 0, 45));
    events.push(timed("Town meeting", "Riley", rel(5), 19, 0, 90, PLACES.school));
    events.push(timed("Flight home", "Taylor", rel(4), 15, 20, 140, "Regional Airport, Springfield"));
    events.push(timed("Cancel trial subscription", "Sam", rel(4), 11, 0, 15));
    events.push(timed("Choir rehearsal", "Jordan", rel(8), 18, 15, 90));

    // A couple of things later today so "Next up" always has something to show
    if (now.getHours() < 20) {
      const soon = new Date(now.getTime() + 100 * 60000);
      soon.setMinutes(Math.ceil(soon.getMinutes() / 15) * 15, 0, 0);
      events.push(timed("Dentist", "Casey", today, soon.getHours(), soon.getMinutes(), 45, PLACES.ortho));
      if (soon.getHours() < 19) events.push(timed("Bring ice for the party", "Sam", today, soon.getHours() + 2, 30, 30));
    }

    const tg = thanksgiving(today.getFullYear());
    if (tg >= addDays(today, -1) && tg < addDays(today, 15)) {
      events.push(allDay("Thanksgiving Day", "US holidays", tg));
      events.push(allDay("No School", "Kids", addDays(tg, -1), 3));
      events.push(allDay("Black Friday", "US holidays", addDays(tg, 1)));
      events.push(timed("Thanksgiving lunch", "Sam", addDays(tg, -5), 13, 30, 120));
    }
    events.push(allDay("Science fair", "Casey", rel(0)));
    return { calendars: CALENDARS, events, updated: Math.floor(now.getTime() / 1000) - 90 };
  }

  // ---------- Drive ----------

  const ALWAYS = [
    { name: "Office", icon: "work", base: 31, mi: 24.6 },
    { name: "Taylor", icon: "heart", base: 801, mi: 874 },
    { name: "Bank", icon: "pin", base: 6, mi: 2.4 },
    { name: "Airport", icon: "plane", base: 39, mi: 31.5 },
  ];

  function rush(now) {
    const h = now.getHours() + now.getMinutes() / 60;
    const wk = now.getDay() > 0 && now.getDay() < 6;
    return wk ? Math.max(Math.exp(-((h - 8) ** 2) / 1.2), Math.exp(-((h - 17.5) ** 2) / 1.8)) : 0.1;
  }

  function drive(now) {
    const r = rush(now);
    return {
      places: ALWAYS.map((p, i) => ({
        name: p.name, icon: p.icon, typicalMinutes: p.base, distance: p.mi, unit: "mi", status: "ok",
        minutes: Math.round(p.base * (1 + r * (0.15 + (i % 3) * 0.12)) + (i === 1 ? 20 : 0)),
      })),
      updated: Math.floor(now.getTime() / 1000) - 60,
    };
  }

  function route(location, startIso, now) {
    const typical = 12 + (hash(location) % 24);
    const delay = Math.round(typical * (0.08 + rush(now) * 0.35));
    const minutes = typical + delay;
    const leave = new Date(new Date(startIso).getTime() - (minutes + 5) * 60000);
    return {
      status: "ok", minutes, typicalMinutes: typical, distance: Math.round(typical * 0.62 * 10) / 10, unit: "mi",
      via: ["US-15 N", "US-64 E", "NC-751 N"][hash(location) % 3],
      slow: delay >= 3 ? Math.round(delay * 0.4 * 10) / 10 : 0, jam: delay >= 6 ? 0.4 : 0,
      bufferMinutes: 5, leaveBy: leave.toISOString(), checked: Math.floor(now.getTime() / 1000),
    };
  }

  // Prices wander a little each refresh so the ticker can be seen updating
  const STOCKS = { AAPL: 227.48, MSFT: 431.2, NVDA: 141.9, GOOGL: 176.35, AMZN: 201.12, SPY: 597.4, VTI: 296.8, TSLA: 338.6, "BRK.B": 471.05, KO: 62.4 };
  function stocks(now) {
    const t = now.getTime() / 60000;
    return {
      quotes: Object.entries(STOCKS).map(([symbol, prev], i) => {
        const pct = Math.round((Math.sin(t / 7 + i * 1.7) * 1.8 + Math.sin(t / 2.3 + i) * 0.4) * 100) / 100;
        const price = Math.round(prev * (1 + pct / 100) * 100) / 100;
        return { symbol, price, change: Math.round((price - prev) * 100) / 100, percent: pct };
      }),
      updated: Math.floor(now.getTime() / 1000), refresh: 15,
    };
  }

  // Sample Home Assistant tiles and cameras, for the layout editor's preview and ?mode=demo
  function home() {
    const t = (name, text, tone, icon) => ({ id: name, name, kind: "x", state: "", minutes: 0, text, tone, icon });
    return {
      items: [t("Front door", "Locked", "good", "lock"), t("Back door", "Locked", "good", "lock"), t("Garage", "Closed", "good", "garage"),
              t("Alarm", "Armed home", "good", "shield"), t("Alex", "Home", "good", "person"), t("Sam", "Away", "neutral", "person")],
      summary: { text: "All secure", tone: "good" }, updated: Math.floor(Date.now() / 1000),
    };
  }
  function cameras(now) {
    const burst = /[?&]popup=1/.test(location.search) || Math.floor(now.getTime() / 20000) % 9 === 0;   // a pretend motion event now and then
    return {
      cameras: [{ id: "camera.front", name: "Front door", active: burst, popup: true, since: null },
                { id: "camera.driveway", name: "Driveway", active: false, popup: true, since: null },
                { id: "camera.backyard", name: "Backyard", active: false, popup: true, since: null },
                { id: "camera.garage", name: "Garage", active: false, popup: true, since: null }],
      popup: true, seconds: 30, updated: Math.floor(now.getTime() / 1000),
    };
  }
  function cameraImage(id) {
    const t = Date.now() / 1000, x = 120 + 60 * Math.sin(t / 2.2), name = id.replace("camera.", "").replace(/_/g, " ");
    const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 640 360"><defs><linearGradient id="g" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#2B3A4F"/><stop offset="1" stop-color="#101822"/></linearGradient></defs>`
      + `<rect width="640" height="360" fill="url(#g)"/><rect x="0" y="250" width="640" height="110" fill="#1B2430"/><circle cx="${x}" cy="215" r="22" fill="#7A8CA5"/><rect x="${x - 18}" y="237" width="36" height="70" rx="12" fill="#7A8CA5"/>`
      + `<text x="20" y="38" font-family="sans-serif" font-size="22" fill="#FFFFFF" opacity=".8">Sample picture: ${name}</text></svg>`;
    return "data:image/svg+xml;utf8," + encodeURIComponent(svg);
  }

  window.Demo = { weather, calendar, drive, route, stocks, home, cameras, cameraImage };
})();
