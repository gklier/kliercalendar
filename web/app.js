/* Family Dashboard — TV runtime.
   Live data comes from the Pi server (/api/*); demo mode uses demo.js.
   Everything renders into a fixed 1920×1080 stage scaled to the screen. */
(function () {
  "use strict";

  const params = new URLSearchParams(location.search);
  const cfg = Object.assign({}, window.DASHBOARD_CONFIG || {});
  const $ = (id) => document.getElementById(id);

  const state = {
    mode: "demo",
    weather: null,
    calendar: null,
    drive: null,
    events: [],          // parsed calendar events
    routes: {},          // "location|start" -> { data, error, at, pending }
    photos: { list: [], i: 0, timer: null },
    errors: {},          // source -> { since, message }
    version: null,
    shift: { x: 0, y: 0 },
  };

  // ---------- Utilities ----------

  const startOfDay = (d) => { const x = new Date(d); x.setHours(0, 0, 0, 0); return x; };
  const addDays = (d, n) => { const x = new Date(d); x.setDate(x.getDate() + n); return x; };
  const sameDay = (a, b) => a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
  const parseYMD = (s) => { const [y, m, d] = s.split("-").map(Number); return new Date(y, m - 1, d); };
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const deg = (n) => (n == null || isNaN(n) ? "--" : `${Math.round(n)}°`);
  const is12h = () => cfg.timeFormat !== "24h";
  const hhmmToMin = (s) => { const [h, m] = String(s).split(":").map(Number); return h * 60 + (m || 0); };

  function fmtTime(d, compact) {
    if (!is12h()) return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
    const h = d.getHours() % 12 || 12, m = d.getMinutes();
    const ap = d.getHours() < 12 ? "AM" : "PM";
    const mm = m ? `:${String(m).padStart(2, "0")}` : "";
    return compact ? `${h}${mm}${ap === "AM" ? "a" : "p"}` : `${h}${mm} ${ap}`;
  }

  function fmtDuration(mins) {
    if (mins < 60) return `${mins} min`;
    const h = Math.floor(mins / 60), m = mins % 60;
    return m ? `${h} hr ${m} min` : `${h} hr`;
  }

  function ago(sec) {
    if (!sec) return "";
    const m = Math.round((Date.now() / 1000 - sec) / 60);
    return m < 1 ? "just now" : m < 60 ? `${m} min ago` : `${Math.round(m / 60)} hr ago`;
  }

  function isLight(hex) {
    const n = parseInt(String(hex).replace("#", ""), 16);
    if (isNaN(n)) return false;
    const ch = [(n >> 16) & 255, (n >> 8) & 255, n & 255].map((v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; });
    return 0.2126 * ch[0] + 0.7152 * ch[1] + 0.0722 * ch[2] > 0.45;
  }

  // An event on several calendars shows every calendar's colour, in stripes
  function paint(ev, dir) {
    const cs = (ev.colors && ev.colors.length ? ev.colors : [ev.color || "#0A84FF"]).slice(0, 4).map(esc);
    if (cs.length === 1) return `--c:${cs[0]}`;
    const step = 100 / cs.length;
    const stops = cs.map((c, i) => `${c} ${(i * step).toFixed(1)}% ${((i + 1) * step).toFixed(1)}%`).join(", ");
    return `--c:${cs[0]};--paint:linear-gradient(${dir || "180deg"}, ${stops})`;
  }
  const who = (ev) => (ev.calendars && ev.calendars.length ? ev.calendars : [ev.calendar]).join(", ");

  // ---------- Stage ----------

  function fit() {
    const s = Math.min(innerWidth / 1920, innerHeight / 1080);
    $("stage").style.transform = `translate(${state.shift.x}px, ${state.shift.y}px) scale(${s}) translate(-50%, -50%)`;
    $("stage").style.transformOrigin = "0 0";
  }

  function choosePerf() {
    const forced = params.get("perf") || cfg.perf;
    if (forced === "lite" || forced === "full") return forced;
    // A Pi 4 draws the glass fine; only boards with 1 GB or less (Pi Zero 2 W, Pi 3) need lite
    const lowMem = typeof navigator.deviceMemory === "number" && navigator.deviceMemory <= 1;
    return lowMem ? "lite" : "full";
  }

  // ---------- Free layout ----------

  // Settings → Layout can put every widget anywhere on the 1920 x 1080 screen. Widgets with a
  // fixed design (clock, weather, drive times, ticker) scale their content to the box they get;
  // the calendar and the day panel reflow instead.
  const WIDGETS = {
    clock: { el: () => document.querySelector(".clock"), base: [340, 160] },
    weather: { el: () => $("weather"), base: [660, 160] },
    drive: { el: () => $("drive"), base: [808, 160] },
    calendar: { el: () => document.querySelector(".calendar") },
    focus: { el: () => $("focus") },
    ticker: { el: () => $("ticker"), base: [1854, 50] },
  };
  const layoutOf = (type) => (cfg.layout && cfg.layout.widgets && cfg.layout.widgets.find((w) => w.type === type)) || null;
  const widgetOpt = (type, key, fallback) => { const w = layoutOf(type); return w && w.options && w.options[key] != null ? w.options[key] : fallback; };

  function widgetZoom(type) {
    const w = layoutOf(type), spec = WIDGETS[type];
    if (!w) return 1;
    if (w.scale != null && w.scale !== "auto") return Number(w.scale) || 1;
    if (!spec.base) return 1;
    return Math.max(0.5, Math.min(2.5, Math.min(w.w / spec.base[0], w.h / spec.base[1])));
  }

  function applyLayout() {
    const free = !!(cfg.layout && Array.isArray(cfg.layout.widgets));
    document.body.classList.toggle("free", free);
    for (const [type, spec] of Object.entries(WIDGETS)) {
      const el = spec.el();
      if (!el) continue;
      el.style.cssText = "";
      if (!free) continue;
      const w = layoutOf(type);
      if (!w || w.visible === false) { el.style.display = "none"; continue; }
      const k = widgetZoom(type);   // CSS zoom scales positions too, so divide them by it
      el.style.cssText = `position:absolute;left:${w.x / k}px;top:${w.y / k}px;width:${w.w / k}px;height:${w.h / k}px;zoom:${k};`;
    }
  }

  // ---------- Background ----------

  const customBg = () => cfg.background === "gradient" || cfg.background === "color";

  function applyBackground() {
    document.body.classList.toggle("bg-custom", customBg());
    const root = document.documentElement.style;
    root.setProperty("--photo-dim", String((cfg.photoDim == null ? 30 : cfg.photoDim) / 100));
    if (cfg.background === "gradient") {
      const g = cfg.bgGradient || {};
      root.setProperty("--sky", `linear-gradient(${g.angle == null ? 160 : g.angle}deg, ${g.a || "#1B2A49"}, ${g.b || "#4B2E6F"})`);
    } else if (cfg.background === "color") {
      root.setProperty("--sky", cfg.bgColor || "#0B1626");
    }
  }

  // ---------- Frame rate ----------

  // Counts frames in 5-second windows. With "Automatic" quality, if the glass
  // can't keep 30 fps (the ticker shows it most), the blur comes off.
  const fps = { value: null, low: 0 };
  function meterFrames() {
    let frames = 0, start = performance.now();
    const step = (t) => {
      frames++;
      if (t - start >= 5000) {
        fps.value = Math.round(frames * 1000 / (t - start));
        frames = 0; start = t;
        const auto = !(params.get("perf") || (cfg.perf && cfg.perf !== "auto"));
        if (auto && !document.body.classList.contains("lite") && !document.hidden) {
          fps.low = fps.value < 30 ? fps.low + 1 : 0;
          if (fps.low >= 2) { fps.glass = fps.value; document.body.classList.add("lite"); console.info(`[dashboard] ${fps.value} fps with glass; switching to lite`); }
        }
      }
      requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  }

  // ---------- Sky ----------

  // Dark skies at night, light ones between sunrise and sunset. Flat colours only.
  const SKY = {
    night: { sky: "#0B1626", a: "#1A3259", b: "#132A47", c: "#33496E" },
    dawn: { sky: "#36456E", a: "#8E5F86", b: "#4E5E91", c: "#E39A62" },
    dusk: { sky: "#33285A", a: "#8A4775", b: "#4E3A7C", c: "#DB7A45" },
    overcastNight: { sky: "#121A24", a: "#26313F", b: "#1A2430", c: "#2C3848" },
    day: { sky: "#CFE2F6", a: "#A9CBEF", b: "#E4EFFA", c: "#F6CF72" },
    overcast: { sky: "#DADFE5", a: "#C3CAD3", b: "#E9ECF0", c: "#B7C0CB" },
    rain: { sky: "#CBD5DF", a: "#AEBDCC", b: "#DCE3EA", c: "#9FB0C2" },
  };

  function sunTimes(now) {
    const w = state.weather;
    const t = w && w.daily && w.daily.find((d) => sameDay(parseYMD(d.date), now));
    // Without a forecast yet, assume 7 AM to 5:30 PM
    return t && t.sunrise && t.sunset
      ? { rise: t.sunrise * 1000, set: t.sunset * 1000 }
      : { rise: new Date(now).setHours(7, 0, 0, 0), set: new Date(now).setHours(17, 30, 0, 0) };
  }

  function paintSky(now) {
    const w = state.weather;
    const { rise, set } = sunTimes(now);
    const H = 3600000, ms = now.getTime();
    const sunUp = ms >= rise && ms < set;
    const ap = cfg.appearance || "auto";
    const light = ap === "auto" ? sunUp : ap === "light";      // Settings can pin the look to light or dark
    const kind = w && w.current ? window.Icons.classify(w.current.icon).kind : "clear";
    const wet = ["rain", "thunder", "sleet", "snow"].includes(kind), grey = ["cloudy", "fog"].includes(kind);
    let phase;
    if (light) phase = wet ? "rain" : grey ? "overcast" : "day";
    else if (wet || grey) phase = "overcastNight";
    else phase = ms < rise && ms >= rise - H ? "dawn" : ms >= set && ms < set + H ? "dusk" : "night";
    document.body.classList.toggle("light", light);
    if (customBg()) return;                                    // a chosen colour or gradient replaces the sky
    const p = SKY[phase], root = document.documentElement.style;
    root.setProperty("--sky", p.sky);
    root.setProperty("--orb-a", p.a);
    root.setProperty("--orb-b", p.b);
    root.setProperty("--orb-c", p.c);
  }

  // ---------- Family photos ----------

  async function loadPhotos() {
    if (cfg.background !== "photos" && cfg.background !== "photo") return;
    try {
      let list;
      if (cfg.background === "photo") {
        list = cfg.bgPhoto ? [cfg.bgPhoto] : [];
      } else {
        list = state.mode === "demo" ? (window.Demo.photos || []) : (await getJSON("api/photos")).photos;
        // Shuffle so the photos come round in a different order each time
        for (let i = list.length - 1; i > 0; i--) { const j = Math.floor(Math.random() * (i + 1)); [list[i], list[j]] = [list[j], list[i]]; }
      }
      state.photos.list = list;
      state.photos.i = 0;
    } catch (_) { /* keep the current list */ }
    document.body.classList.toggle("photos", state.photos.list.length > 0);
    if (state.photos.list.length && !state.photos.timer) nextPhoto();
  }

  function nextPhoto() {
    const ph = state.photos;
    clearTimeout(ph.timer);
    ph.timer = setTimeout(nextPhoto, Math.max(cfg.photoMinutes || 10, 1) * 60000);
    if (!ph.list.length) return;
    const name = ph.list[ph.i++ % ph.list.length];
    const img = new Image();
    img.onload = () => {
      const layers = [...document.querySelectorAll(".photo")];
      const back = layers.find((l) => !l.classList.contains("on")) || layers[0];
      back.style.backgroundImage = `url("${img.src}")`;
      layers.forEach((l) => l.classList.toggle("on", l === back));
    };
    img.src = /^(data:|https?:)/.test(name) ? name : `photos/${encodeURIComponent(name)}`;
  }

  // ---------- Clock + timers ----------

  function renderClock() {
    const now = new Date();
    $("clock-date").textContent = now.toLocaleDateString([], { weekday: "long", month: "long", day: "numeric" });
    if (is12h()) {
      $("clock-time").innerHTML = `${now.getHours() % 12 || 12}:${String(now.getMinutes()).padStart(2, "0")}<span class="ampm">${now.getHours() < 12 ? "AM" : "PM"}</span>`;
    } else {
      $("clock-time").textContent = fmtTime(now);
    }
  }

  function applyNightDim(now) {
    const n = cfg.nightDim;
    const m = now.getHours() * 60 + now.getMinutes();
    let on = false;
    if (n && n.enabled) {
      const s = hhmmToMin(n.start), e = hhmmToMin(n.end);
      on = s > e ? m >= s || m < e : m >= s && m < e;
      document.documentElement.style.setProperty("--night-dim", n.level == null ? 0.6 : n.level);
    }
    document.body.classList.toggle("night", on);
  }

  function tickMinute() {
    const now = new Date();
    renderClock();
    paintSky(now);
    applyNightDim(now);
    renderFocus();          // countdowns and "leave by" move every minute
    if (now.getMinutes() % 10 === 0) { renderCalendar(); renderStatus(); }
    const hm = `${String(now.getHours()).padStart(2, "0")}:${String(now.getMinutes()).padStart(2, "0")}`;
    if (cfg.dailyReload && hm === cfg.dailyReload) return location.reload();
    setTimeout(tickMinute, 60000 - (Date.now() % 60000) + 50);
  }

  // ---------- Weather ----------

  function renderWeather() {
    const el = $("weather");
    const w = state.weather;
    if (!w || !w.current) { el.innerHTML = `<p class="note">${waiting("weather", "Waiting for the weather station.")}</p>`; return; }
    const c = w.current, u = w.units || cfg.units || {};
    const today = (w.daily || []).find((d) => sameDay(parseYMD(d.date), new Date())) || {};
    const next = (w.daily || []).filter((d) => parseYMD(d.date) > startOfDay(new Date())).slice(0, 4);
    el.innerHTML = `
      <div class="now">
        <div class="now-temp">${deg(c.temp)}</div>
        ${window.Icons.weatherSVG(c.icon, { label: c.conditions })}
        <div class="now-text">
          <div class="now-cond">${esc(c.conditions)}</div>
          <div class="now-sub">Feels ${deg(c.feelsLike)}&ensp;H ${deg(today.high)}&ensp;L ${deg(today.low)}&ensp;Wind ${c.windAvg == null ? "--" : Math.round(c.windAvg)} ${esc(u.wind || "")} ${esc(c.windDir || "")}</div>
        </div>
      </div>
      <div class="days">${next.map((d) => `
        <div class="fday">
          <div class="fday-name">${parseYMD(d.date).toLocaleDateString([], { weekday: "short" })}</div>
          ${window.Icons.weatherSVG(d.icon, { label: d.conditions })}
          <div class="fday-pop">${d.pop >= 20 ? `${Math.round(d.pop / 10) * 10}%` : ""}</div>
          <div class="fday-t">${deg(d.high)}<span>${deg(d.low)}</span></div>
        </div>`).join("")}
      </div>`;
  }

  // ---------- Drive ----------

  function trafficNote(minutes, typical) {
    const delay = Math.max(minutes - (typical || minutes), 0);
    const ratio = minutes / Math.max(typical || minutes, 1);
    if (delay < 2 || ratio <= 1.1) return { dot: "var(--green)", text: "Light traffic" };
    return { dot: ratio <= 1.3 ? "var(--yellow)" : "var(--red)", text: `+${delay} min traffic` };
  }

  function renderDrive() {
    const el = $("drive");
    const d = state.drive;
    if (!d || !d.places || !d.places.length) { el.innerHTML = `<p class="note">${waiting("drive", "Add places in Settings to see drive times here.")}</p>`; return; }
    el.innerHTML = d.places.slice(0, 4).map((p) => {
      let mins = `--`, note = { dot: "var(--ink-3)", text: "No route found" };
      if (p.status === "ok" && p.minutes != null) {
        const h = Math.floor(p.minutes / 60), m = p.minutes % 60;
        mins = h ? `${h}<small>hr</small> ${m}<small>min</small>` : `${p.minutes}<small>min</small>`;
        note = trafficNote(p.minutes, p.typicalMinutes);
      }
      return `<div class="tile">
        <div class="tile-head"><span class="tile-icon">${window.Icons.placeSVG(p.icon)}</span><span class="tile-name">${esc(p.name)}</span></div>
        <div class="tile-mins">${mins}</div>
        <div class="tile-delay" style="--dot:${note.dot}">${note.text}</div>
      </div>`;
    }).join("");
  }

  // ---------- Calendar ----------

  function parseEvents(cal) {
    return (cal && cal.events ? cal.events : []).map((ev) => {
      const s = ev.allDay ? parseYMD(ev.start) : new Date(ev.start);
      let e = ev.allDay ? (ev.end ? parseYMD(ev.end) : addDays(s, 1)) : ev.end ? new Date(ev.end) : new Date(s.getTime() + 3600000);
      if (!(e > s)) e = new Date(s.getTime() + (ev.allDay ? 86400000 : 0));
      return { ...ev, s, e };
    }).filter((ev) => !isNaN(ev.s) && !isNaN(ev.e));
  }

  function eventsOn(day) {
    const ds = startOfDay(day), de = addDays(ds, 1);
    return state.events
      .filter((ev) => (ev.s < de && ev.e > ds) || (!ev.allDay && ev.s >= ds && ev.s < de))
      .sort((a, b) => (b.allDay - a.allDay) || (a.s - b.s) || a.title.localeCompare(b.title));
  }

  function evHTML(ev, day, now) {
    if (ev.allDay) return `<div class="ev allday${isLight(ev.color) ? " light" : ""}" style="${paint(ev, "90deg")}">${esc(ev.title)}</div>`;
    const startsHere = sameDay(ev.s, day);
    const done = ev.e < now ? " done" : "";
    return `<div class="ev${done}" style="${paint(ev)}"><span class="bar"></span><span class="txt"><span class="tm">${startsHere ? fmtTime(ev.s, true) : "cont."}</span><span class="ti">${esc(ev.title)}</span></span></div>`;
  }

  // Remove events from the bottom until the box fits, then say how many are hidden
  function trimToFit(box, itemSel, moreClass) {
    const items = [...box.querySelectorAll(itemSel)];
    let hidden = 0;
    while (box.scrollHeight > box.clientHeight + 1 && items.length) {
      items.pop().remove();
      hidden++;
      let more = box.querySelector(`.${moreClass}`);
      if (!more) { more = document.createElement("div"); more.className = moreClass; box.appendChild(more); }
      more.textContent = `${hidden} more`;
    }
  }

  function renderCalendar() {
    const now = new Date(), today = startOfDay(now);
    const grid = $("cal-grid");
    const days = [7, 14, 21, 28].includes(widgetOpt("calendar", "days", 14)) ? widgetOpt("calendar", "days", 14) : 14;
    grid.style.gridTemplateRows = `repeat(${Math.ceil(days / 7)}, minmax(0, 1fr))`;
    const title = days === 7 ? "Next week" : days === 14 ? "Next two weeks" : days === 21 ? "Next three weeks" : "Next four weeks";
    const h = document.querySelector(".cal-top h2");
    if (h && h.textContent !== title) { h.textContent = title; h.closest("section").setAttribute("aria-label", title); }
    let html = "";
    for (let i = 0; i < days; i++) {
      const d = addDays(today, i);
      const weekend = d.getDay() === 0 || d.getDay() === 6;
      const mo = i === 0 || d.getDate() === 1 ? `<span class="mo">${d.toLocaleDateString([], { month: "short" })}</span>` : "";
      const dow = i === 0 ? "Today" : i === 1 ? "Tomorrow" : d.toLocaleDateString([], { weekday: "short" });
      html += `<div class="day${i === 0 ? " today" : ""}${weekend ? " weekend" : ""}">
        <div class="day-head"><span class="dow">${dow}</span><span class="n">${d.getDate()}</span>${mo}</div>
        ${eventsOn(d).map((ev) => evHTML(ev, d, now)).join("")}
      </div>`;
    }
    grid.innerHTML = html;
    grid.querySelectorAll(".day").forEach((cell) => trimToFit(cell, ".ev", "more"));

    const cals = (state.calendar && state.calendar.calendars) || [];
    $("legend").innerHTML = cals.map((c) => `<span><i style="--c:${esc(c.color)}"></i>${esc(c.name)}</span>`).join("");
  }

  // ---------- Day focus ----------

  function focusDay(now) {
    const today = startOfDay(now);
    const left = eventsOn(today).some((ev) => !ev.allDay && ev.e > now);
    return left ? today : addDays(today, 1);
  }

  function countdown(ms) {
    const mins = Math.round(ms / 60000);
    if (mins <= 0) return "now";
    return `in ${fmtDuration(mins)}`;
  }

  function leaveHTML(ev, now) {
    if (!ev.location) return "";
    const r = state.routes[routeKey(ev)];
    if (!r || (!r.data && !r.error)) return "";
    if (r.error) return `<div class="leave"><div class="leave-info" style="grid-column:1/-1">Drive time isn't available right now.</div></div>`;
    const d = r.data;
    if (!d || d.status !== "ok") return "";
    const leaveBy = d.leaveBy ? new Date(d.leaveBy) : null;
    const late = leaveBy && leaveBy <= now;
    const delay = Math.max(d.minutes - (d.typicalMinutes || d.minutes), 0);
    let traffic = { dot: "var(--green)", text: "Traffic is light" };
    if (d.jam > 0) traffic = { dot: "var(--red)", text: `Stopped traffic for ${d.jam} ${d.unit}${delay ? `, +${delay} min` : ""}` };
    else if (d.slow > 0) traffic = { dot: "var(--yellow)", text: `Slow for ${d.slow} ${d.unit}${delay ? `, +${delay} min` : ""}` };
    else if (delay >= 2) traffic = { dot: "var(--yellow)", text: `+${delay} min from traffic` };
    return `<div class="leave">
      <div class="leave-by${late ? " now" : ""}">${late ? "Running late" : "Leave by"}<b>${late ? "Leave now" : leaveBy ? fmtTime(leaveBy) : "--"}</b></div>
      <div class="leave-info">
        <div><strong>${fmtDuration(d.minutes)}</strong> drive, ${d.distance} ${esc(d.unit)}${d.via ? ` via ${esc(d.via)}` : ""}</div>
        <div class="traffic" style="--dot:${traffic.dot}">${traffic.text}</div>
      </div>
    </div>`;
  }

  const routeKey = (ev) => `${ev.location}|${ev.start}`;

  function renderFocus() {
    const el = $("focus");
    if (!state.calendar) { el.innerHTML = `<p class="note">${waiting("calendar", "Waiting for the calendar.")}</p>`; return; }
    const now = new Date();
    const day = focusDay(now);
    const isToday = sameDay(day, now);
    const all = eventsOn(day);
    const allDay = all.filter((ev) => ev.allDay);
    const timed = all.filter((ev) => !ev.allDay);
    const upcoming = timed.filter((ev) => ev.s > now);
    const featured = upcoming[0] || timed.find((ev) => ev.e > now) || null;

    const chips = allDay.length ? `<div class="chips">${allDay.map((ev) => `<span class="chip-ev${isLight(ev.color) ? " light" : ""}" style="${paint(ev, "90deg")}">${esc(ev.title)}</span>`).join("")}</div>` : "";

    let next = "";
    if (featured) {
      const started = featured.s <= now;
      next = `<div class="next" style="${paint(featured)}">
        <div class="next-when"><span class="next-time">${fmtTime(featured.s)}</span><span class="next-in">${started ? `now, until ${fmtTime(featured.e)}` : countdown(featured.s - now)}</span></div>
        <div class="next-title">${esc(featured.title)}</div>
        <div class="next-who">${esc(who(featured))}</div>
        ${featured.location ? `<div class="next-where">${esc(featured.location)}</div>` : ""}
        ${started ? "" : leaveHTML(featured, now)}
      </div>`;
    }

    const rest = timed.filter((ev) => ev !== featured);
    const rows = rest.map((ev) => {
      const cls = ev.e <= now ? " past" : ev.s <= now ? " now" : "";
      return `<div class="row${cls}" style="${paint(ev)}">
        <div class="t">${ev.s <= now && ev.e > now ? "Now" : sameDay(ev.s, day) ? fmtTime(ev.s) : "cont."}</div>
        <div class="bar"></div>
        <div><div class="ti">${esc(ev.title)}</div><div class="meta">${esc(who(ev))}</div></div>
        ${ev.s > now ? rowLeave(ev, now) : ""}
      </div>`;
    }).join("");

    const empty = !timed.length ? `<div class="empty">${allDay.length ? "No timed plans." : `Nothing on the calendar${isToday ? " today" : " tomorrow"}.`}</div>` : "";

    el.innerHTML = `
      <div class="focus-head"><h2>${isToday ? "Today" : "Tomorrow"}</h2><span class="sub">${day.toLocaleDateString([], { weekday: "long", month: "short", day: "numeric" })}</span></div>
      ${chips}${next}${empty}
      <div class="agenda" id="agenda">${rows}</div>`;
    trimToFit($("agenda"), ".row", "more");
    for (const ev of timed) if (ev.s > now) ensureRoute(ev, now);
  }

  // Right-hand "Leave 3:22 / 18 min" on an agenda row
  function rowLeave(ev, now) {
    const r = ev.location && state.routes[routeKey(ev)];
    const d = r && r.data;
    if (!d || d.status !== "ok" || !d.leaveBy) return "";
    const leave = new Date(d.leaveBy);
    const late = leave <= now;
    const delay = Math.max(d.minutes - (d.typicalMinutes || d.minutes), 0);
    const dot = d.jam > 0 ? "var(--red)" : d.slow > 0 || delay >= 2 ? "var(--yellow)" : "var(--green)";
    return `<div class="lv${late ? " late" : ""}"><b>${late ? "Leave now" : `Leave ${fmtTime(leave)}`}</b><span class="traffic" style="--dot:${dot}">${fmtDuration(d.minutes)}</span></div>`;
  }

  async function ensureRoute(ev, now) {
    const key = routeKey(ev);
    const r = state.routes[key] || (state.routes[key] = { data: null, error: null, at: 0, pending: false });
    // Only plan trips that start in the next 16 hours; the server keeps its own cache too
    if (!ev.location || r.pending || Date.now() - r.at < 5 * 60000 || ev.s - now > 16 * 3600000) return;
    r.pending = true;
    await null;  // let the current render finish before this one re-renders
    try {
      r.data = state.mode === "demo"
        ? window.Demo.route(ev.location, ev.start, now)
        : await getJSON(`api/route?to=${encodeURIComponent(ev.location)}&arrive=${encodeURIComponent(ev.start)}`);
      r.error = null;
    } catch (err) {
      r.data = null;
      r.error = String(err.message || err);
    } finally {
      r.at = Date.now();
      r.pending = false;
    }
    // Forget trips for events that have passed
    for (const k of Object.keys(state.routes)) if (new Date(k.split("|").pop()) < now) delete state.routes[k];
    renderFocus();
  }

  // ---------- Stock ticker ----------

  // One strip of quotes, drawn twice end to end and slid left forever. Prices
  // update in place, so the scroll never jumps when new numbers arrive.
  function tickerItem(q) {
    const up = q.change > 0, down = q.change < 0;
    const cls = up ? "up" : down ? "down" : "flat";
    const arrow = up ? "\u25B2" : down ? "\u25BC" : "\u2022";
    const price = q.price >= 1000 ? q.price.toLocaleString([], { maximumFractionDigits: 2, minimumFractionDigits: 2 }) : q.price.toFixed(2);
    return `<span class="tk ${cls}" data-sym="${esc(q.symbol)}"><b>${esc(q.symbol)}</b><span class="px">${price}</span>`
      + `<span class="pc">${arrow} ${Math.abs(q.percent).toFixed(2)}%</span></span>`;
  }

  function renderTicker() {
    const on = !!(cfg.stocks && cfg.stocks.enabled);
    document.body.classList.toggle("has-ticker", on);
    const el = $("ticker");
    if (!on || !el) return;
    const s = state.stocks;
    const quotes = (s && s.quotes) || [];
    if (!quotes.length) {
      el.innerHTML = `<div class="tk-note">${waiting("stocks", "Waiting for stock prices.")}</div>`;
      el.dataset.syms = "";
      return;
    }
    const syms = quotes.map((q) => q.symbol).join(",");
    const track = el.querySelector(".tk-track");
    if (track && el.dataset.syms === syms) {
      // Same tickers: swap the numbers in both copies without restarting the slide
      for (const q of quotes) el.querySelectorAll(`.tk[data-sym="${CSS.escape(q.symbol)}"]`).forEach((n) => { n.outerHTML = tickerItem(q); });
      return;
    }
    const strip = quotes.map(tickerItem).join("");
    el.innerHTML = `<div class="tk-track"><div class="tk-strip">${strip}</div><div class="tk-strip" aria-hidden="true">${strip}</div></div>`;
    el.dataset.syms = syms;
    // Each copy is at least as wide as the bar, so a short list scrolls past once
    // with room after it (never two copies on screen), and the loop has no seam.
    // Same reading speed however many tickers there are: about 90 px a second.
    const strips = el.querySelectorAll(".tk-strip");
    strips.forEach((st) => { st.style.minWidth = `${el.clientWidth}px`; });
    const w = strips[0].offsetWidth * widgetZoom("ticker");   // layout pixels on the 1920-wide stage
    el.querySelector(".tk-track").style.animationDuration = `${Math.max(w / widgetOpt("ticker", "speed", 90), 10).toFixed(2)}s`;
  }

  async function scheduleStocks() {
    if (!(cfg.stocks && cfg.stocks.enabled)) return;
    await load("stocks");
    const secs = (state.stocks && state.stocks.refresh) || 15;
    setTimeout(scheduleStocks, Math.max(secs, 5) * 1000);
  }

  // ---------- Status ----------

  function waiting(source, fallback) {
    const e = state.errors[source];
    return e ? `Can't load this yet: ${esc(e.message)}` : fallback;
  }

  function renderStatus() {
    const chips = [];
    if (state.mode === "demo") chips.push(`<span class="chip">Demo data</span>`);
    const names = { weather: "Weather", calendar: "Calendar", drive: "Drive times", stocks: "Stocks" };
    for (const [k, v] of Object.entries(state.errors)) chips.push(`<span class="chip warn">${names[k]} offline since ${fmtTime(v.since)}</span>`);
    $("status").innerHTML = chips.join("");
  }

  // ---------- Data ----------

  async function getJSON(url, timeoutMs) {
    const ctl = new AbortController();
    const t = setTimeout(() => ctl.abort(), timeoutMs || 20000);
    try {
      const r = await fetch(url, { signal: ctl.signal, cache: "no-store" });
      if (!r.ok) {
        let msg = `HTTP ${r.status}`;
        try { const j = await r.json(); if (j && j.error) msg = j.error; } catch (_) { /* keep status */ }
        throw new Error(msg);
      }
      return await r.json();
    } finally {
      clearTimeout(t);
    }
  }

  async function load(source) {
    try {
      const now = new Date();
      const data = state.mode === "demo"
        ? window.Demo[source](now)
        : await getJSON(`api/${source}`);
      state[source] = data;
      if (data && data.stale) {
        if (!state.errors[source]) state.errors[source] = { since: new Date(), message: data.error || "Upstream unavailable" };
      } else {
        delete state.errors[source];
      }
    } catch (err) {
      if (!state.errors[source]) state.errors[source] = { since: new Date(), message: String(err.message || err) };
      console.warn(`[dashboard] ${source} refresh failed:`, err);
    }
    if (source === "weather") {
      renderWeather();
      paintSky(new Date());
      if (document.body.classList.contains("booting")) requestAnimationFrame(() => requestAnimationFrame(() => document.body.classList.remove("booting")));
    }
    if (source === "calendar") { state.events = parseEvents(state.calendar); renderCalendar(); renderFocus(); }
    if (source === "drive") renderDrive();
    if (source === "stocks") renderTicker();
    renderStatus();
  }

  function schedule(source, secs) {
    load(source).finally(() => setTimeout(() => schedule(source, secs), secs * 1000));
  }

  // Settings saved in the admin page bump the version; the TV reloads to pick them up.
  async function watchVersion() {
    try {
      const q = fps.value == null ? "" : `?fps=${fps.value}&perf=${document.body.classList.contains("lite") ? "lite" : "full"}${fps.glass ? `&glass=${fps.glass}` : ""}`;
      const { version } = await getJSON(`api/version${q}`, 8000);
      if (state.version && version !== state.version) return location.reload();
      state.version = version;
    } catch (_) { /* server restarting; try again next round */ }
    setTimeout(watchVersion, 30000);
  }

  async function chooseMode() {
    const forced = params.get("mode") || cfg.mode;
    const demo = () => { cfg.stocks = cfg.stocks && cfg.stocks.enabled !== undefined ? cfg.stocks : { enabled: params.get("stocks") !== "0" }; return "demo"; };
    if (forced === "demo") {
      // Demo data, but the look (layout, background, clock style) still comes from the server when there is one
      try { Object.assign(cfg, await getJSON("api/config", 3000)); } catch (_) { /* a static preview has no server */ }
      return demo();
    }
    try {
      Object.assign(cfg, await getJSON("api/config", 4000));
      return "live";
    } catch (err) {
      if (forced === "live") {
        // Live was asked for: never substitute demo data.
        for (const k of ["weather", "calendar", "drive"]) state.errors[k] = { since: new Date(), message: "The dashboard server isn't answering" };
        return "live";
      }
      return demo();
    }
  }

  async function boot() {
    document.body.classList.toggle("lite", choosePerf() === "lite");
    fit();
    addEventListener("resize", () => { fit(); renderCalendar(); renderFocus(); });
    let cursorTimer;
    addEventListener("mousemove", () => {
      document.body.classList.add("show-cursor");
      clearTimeout(cursorTimer);
      cursorTimer = setTimeout(() => document.body.classList.remove("show-cursor"), 2500);
    });
    setTimeout(() => document.body.classList.remove("booting"), 5000);
    setTimeout(meterFrames, 6000);   // after the boot fade

    state.mode = await chooseMode();
    document.body.classList.toggle("lite", choosePerf() === "lite");
    applyLayout();
    applyBackground();
    renderClock();
    renderTicker();
    renderWeather(); renderDrive(); renderCalendar(); renderFocus(); renderStatus();
    tickMinute();
    const r = cfg.refresh || {};
    schedule("weather", r.weather || 120);
    schedule("calendar", r.calendar || 300);
    schedule("drive", r.drive || 300);
    scheduleStocks();
    loadPhotos();
    setInterval(loadPhotos, 30 * 60000);
    if (state.mode === "live") watchVersion();
    if (cfg.burnInShift !== false) {
      setInterval(() => { state.shift = { x: Math.round(Math.random() * 8 - 4), y: Math.round(Math.random() * 8 - 4) }; fit(); }, 15 * 60000);
    }
  }

  // The Layout editor in Settings shows this page in a frame and sends it the layout being edited
  addEventListener("message", (e) => {
    if (e.origin !== location.origin || !e.data || e.data.type !== "fd-layout") return;
    cfg.layout = e.data.layout;
    applyLayout();
    const t = $("ticker"); if (t) t.dataset.syms = "";
    renderCalendar(); renderFocus(); renderTicker();
  });

  boot();
})();
