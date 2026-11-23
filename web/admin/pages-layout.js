/* Layout: move and resize everything on the TV.
   The preview is the real TV page in a frame; the boxes on top are what you drag. Every change is sent
   to the frame as you make it (postMessage), and nothing reaches the TV until you select Save. */
(function () {
  "use strict";
  const FD = (window.FD = window.FD || { pages: {} });
  const W = 1920, H = 1080, STEP = 10, MIN_W = 80, MIN_H = 40;
  const NAMES = { clock: "Clock", weather: "Weather", drive: "Drive times", calendar: "Calendar", focus: "Today panel", ticker: "Stock bar", home: "Home status", camera: "Camera", camwall: "4-camera wall" };
  const clamp = (n, lo, hi) => Math.max(lo, Math.min(hi, n));
  const snap = (n) => Math.round(n / STEP) * STEP;

  // The built-in arrangement, written out as boxes so it can be nudged
  function builtIn(tickerOn) {
    const tall = tickerOn ? 766 : 832;
    return [
      { type: "clock", x: 32, y: 30, w: 340, h: 160, visible: true, scale: "auto" },
      { type: "weather", x: 396, y: 30, w: 660, h: 160, visible: true, scale: "auto" },
      { type: "drive", x: 1080, y: 30, w: 808, h: 160, visible: true, scale: "auto" },
      { type: "calendar", x: 32, y: 214, w: 1422, h: tall, visible: true, scale: 1, options: { days: 14 } },
      { type: "focus", x: 1478, y: 214, w: 410, h: tall, visible: true, scale: 1 },
      { type: "ticker", x: 32, y: 1004, w: 1856, h: 50, visible: true, scale: "auto", options: { speed: 90 } },
      // Home Assistant widgets start switched off: the Smart home page (or the box below) turns them on
      { type: "home", x: 32, y: 214, w: 1422, h: 64, visible: false, scale: "auto" },
      { type: "camera", x: 1478, y: 654, w: 410, h: 326, visible: false, scale: "auto", options: { camera: "" } },
      { type: "camwall", x: 1478, y: 614, w: 410, h: 366, visible: false, scale: "auto", options: { cameras: [] } },
    ];
  }

  // Used by the Smart home page: switch a widget on in the saved layout, making room for it
  FD.addWidget = async function (type) {
    const s = FD.settings, saved = s.layout && s.layout.widgets;
    const base = builtIn(!!(s.stocks.enabled && s.stocks.symbols.length));
    const widgets = base.map((b) => { const f = saved && saved.find((w) => w.type === b.type); return f ? JSON.parse(JSON.stringify(f)) : { ...b, visible: !saved && b.visible !== false }; });
    const get = (t) => widgets.find((w) => w.type === t), me = get(type), cal = get("calendar"), foc = get("focus");
    if (me.visible) return "already";
    me.visible = true;
    if (type === "home") {
      if (cal.y === 214) { cal.y += 84; cal.h -= 84; }           // the strip sits above the calendar, which gives up a band
      Object.assign(me, { x: cal.x, y: 214, w: cal.w, h: 64 });
    } else if (type === "camera") {
      if (foc.y === 214 && foc.h > 500) foc.h = 416;             // the tile sits under the Today panel, which gives up its lower part
      const top = foc.y + foc.h + 24;
      Object.assign(me, { x: foc.x, y: top, w: foc.w, h: Math.max(cal.y + cal.h - top, 160) });
    }
    if (type === "camwall") {
      const one = get("camera"); if (one) one.visible = false;   // the wall replaces the single tile
      if (foc.y === 214 && foc.h > 420) foc.h = 366;             // under the Today panel, which gives up its lower part
      const top = foc.y + foc.h + 24;
      Object.assign(me, { x: foc.x, y: top, w: foc.w, h: Math.max(cal.y + cal.h - top, 240) });
    }
    const res = await FD.api("settings", { layout: { version: 1, widgets } });
    FD.settings = res.settings;
    return "added";
  };

  FD.pages.layout = function (page) {
    const { $, $$, esc, api, toast } = FD;
    const saved = FD.settings.layout && FD.settings.layout.widgets;
    const tickerOn = !!(FD.settings.stocks.enabled && FD.settings.stocks.symbols.length);
    // Any widget missing from a saved layout starts at its built-in place, hidden
    const base = builtIn(tickerOn);
    let widgets = base.map((b) => {
      const s = saved && saved.find((w) => w.type === b.type);
      return s ? JSON.parse(JSON.stringify(s)) : { ...b, visible: !saved && b.visible !== false };
    });
    let selected = "calendar", sample = true, dirty = false, scale = 0.4, raf = 0;

    const get = (t) => widgets.find((w) => w.type === t);
    const current = () => ({ version: 1, widgets: widgets.map((w) => JSON.parse(JSON.stringify(w))) });

    function post() {                       // a short timer rather than an animation frame, so it also runs in a background tab
      clearTimeout(raf);
      raf = setTimeout(() => {
        const f = $("#lay-frame");
        if (f && f.contentWindow) f.contentWindow.postMessage({ type: "fd-layout", layout: current() }, location.origin);
      }, 25);
    }

    function place(el, w) {
      el.style.cssText = `left:${w.x * scale}px;top:${w.y * scale}px;width:${w.w * scale}px;height:${w.h * scale}px`;
      el.classList.toggle("hidden", !w.visible);
    }
    function paint() {
      const cv = $("#canvas");
      scale = cv.clientWidth / W;
      $("#lay-frame").style.transform = `scale(${scale})`;
      $$(".lay-box", cv).forEach((el) => { place(el, get(el.dataset.type)); el.classList.toggle("sel", el.dataset.type === selected); });
    }

    function inspector() {
      const w = get(selected);
      const zooms = [["auto", "Automatic"], [0.7, "70%"], [0.85, "85%"], [1, "100%"], [1.15, "115%"], [1.3, "130%"], [1.5, "150%"], [2, "200%"]];
      const num = (id, label, v, min, max) => `<label class="tfield">${label}<input type="number" id="${id}" value="${v}" min="${min}" max="${max}" step="${STEP}"></label>`;
      $("#inspector").innerHTML = `
        <h3>${NAMES[selected]}${selected === "ticker" && !tickerOn ? ` <small class="warn">Only shows once the stock bar is on</small>` : ""}</h3>
        <div class="inline">${num("f-x", "Left", w.x, 0, W - MIN_W)}${num("f-y", "Top", w.y, 0, H - MIN_H)}${num("f-w", "Width", w.w, MIN_W, W)}${num("f-h", "Height", w.h, MIN_H, H)}</div>
        <div class="inline" style="margin-top:10px">
          <label class="tfield">Text size
            <select id="f-scale">${zooms.map(([v, l]) => `<option value="${v}" ${String(w.scale) === String(v) ? "selected" : ""}>${l}</option>`).join("")}</select></label>
          ${selected === "calendar" ? `<label class="tfield">Shows
            <select id="f-days">${[[7, "1 week"], [14, "2 weeks"], [21, "3 weeks"], [28, "4 weeks"]].map(([v, l]) => `<option value="${v}" ${(w.options && w.options.days) === v ? "selected" : ""}>${l}</option>`).join("")}</select></label>` : ""}
          ${selected === "camera" ? `<label class="tfield">Shows
            <select id="f-cam"><option value="">Each camera in turn</option>${(FD.settings.ha.cameras || []).map((c) => `<option value="${esc(c.id)}" ${(w.options && w.options.camera) === c.id ? "selected" : ""}>${esc(c.name || c.id)}</option>`).join("")}</select></label>` : ""}
          ${selected === "camwall" ? [0, 1, 2, 3].map((n) => `<label class="tfield">Camera ${n + 1}
            <select class="f-wall" data-n="${n}"><option value="">${n === 0 ? "First camera" : "None"}</option>${(FD.settings.ha.cameras || []).map((c) => `<option value="${esc(c.id)}" ${((w.options && w.options.cameras) || [])[n] === c.id ? "selected" : ""}>${esc(c.name || c.id)}</option>`).join("")}</select></label>`).join("") : ""}
          ${selected === "ticker" ? `<label class="tfield">Speed
            <select id="f-speed">${[[60, "Slow"], [90, "Normal"], [130, "Fast"]].map(([v, l]) => `<option value="${v}" ${(w.options && w.options.speed) === v ? "selected" : ""}>${l}</option>`).join("")}</select></label>` : ""}
          <label class="show"><input type="checkbox" id="f-vis" ${w.visible ? "checked" : ""}> Show on the TV</label>
        </div>
        <p class="hint" style="margin-left:0">Automatic text size scales the widget's contents to fit its box. The calendar and Today panel keep their text size and fit more or fewer days instead.</p>`;
      const set = (patch) => { fit(selected, { ...get(selected), ...patch }); };
      const onNum = (id, key) => { $(id).onchange = (e) => set({ [key]: Number(e.target.value) }); };
      onNum("#f-x", "x"); onNum("#f-y", "y"); onNum("#f-w", "w"); onNum("#f-h", "h");
      $("#f-scale").onchange = (e) => { w.scale = e.target.value === "auto" ? "auto" : Number(e.target.value); changed(); };
      $("#f-vis").onchange = (e) => { w.visible = e.target.checked; changed(); drawList(); };
      const days = $("#f-days"); if (days) days.onchange = (e) => { w.options = { days: Number(e.target.value) }; changed(); };
      const speed = $("#f-speed"); if (speed) speed.onchange = (e) => { w.options = { speed: Number(e.target.value) }; changed(); };
      $$(".f-wall").forEach((sel) => { sel.onchange = () => { w.options = { cameras: $$(".f-wall").map((x) => x.value).filter(Boolean) }; changed(); }; });
      const cam = $("#f-cam"); if (cam) cam.onchange = (e) => { w.options = { camera: e.target.value }; changed(); };
    }

    function drawList() {
      $("#wlist").innerHTML = widgets.map((w) => `<button type="button" class="wchip${w.type === selected ? " sel" : ""}${w.visible ? "" : " off"}" data-type="${w.type}" aria-pressed="${w.type === selected}">${NAMES[w.type]}${w.visible ? "" : " (hidden)"}</button>`).join("");
    }

    function changed() { dirty = true; paint(); post(); $("#lay-state").textContent = "Not saved yet"; }

    // Keep a widget on the screen, on the grid, and at least a sensible size
    function fit(type, r, dir) {
      const w = get(type), right = w.x + w.w, bottom = w.y + w.h;
      let width = clamp(snap(r.w), MIN_W, W), height = clamp(snap(r.h), MIN_H, H);
      let x = snap(r.x), y = snap(r.y);
      if (dir && dir.includes("w")) x = right - width;       // dragging the left edge keeps the right edge still
      if (dir && dir.includes("n")) y = bottom - height;
      x = clamp(x, 0, W - MIN_W); y = clamp(y, 0, H - MIN_H);
      width = Math.min(width, W - x); height = Math.min(height, H - y);
      Object.assign(w, { x, y, w: width, h: height });
      changed();
      if (selected === type) { ["x", "y", "w", "h"].forEach((k) => { const el = $(`#f-${k}`); if (el) el.value = w[k]; }); }
    }

    function select(type) { selected = type; paint(); drawList(); inspector(); }

    function draw() {
      page.innerHTML = `
        <h1>Layout</h1>
        <p class="lede">Move and resize anything on the TV. Drag a box to move it, drag its edges or corners to resize it, or use the arrow keys (hold Shift to resize). Everything snaps to a 10-pixel grid.</p>
        <div class="lay-canvas" id="canvas" tabindex="0" aria-label="Layout editor. Arrow keys move the selected widget; Shift with arrow keys resizes it.">
          <iframe id="lay-frame" title="Live preview of the TV" tabindex="-1" src="/?mode=${sample ? "demo" : "live"}"></iframe>
          <div class="lay-grid" aria-hidden="true"></div>
          ${widgets.map((w) => `<div class="lay-box" data-type="${w.type}"><span class="lay-name">${NAMES[w.type]}</span>${["n", "s", "e", "w", "ne", "nw", "se", "sw"].map((d) => `<i class="hd ${d}" data-h="${d}"></i>`).join("")}</div>`).join("")}
        </div>
        <div class="inline" style="margin:10px 2px 0">
          <label class="show"><input type="checkbox" id="sample" ${sample ? "checked" : ""}> Show sample data in the preview</label>
          <span class="msg" id="lay-state" role="status">${FD.settings.layout ? "Saved layout" : "The built-in arrangement"}</span>
        </div>

        <h2>Widgets</h2>
        <div id="wlist" class="wlist"></div>
        <div class="group" style="margin-top:10px"><div class="item" style="grid-template-columns:1fr" id="inspector"></div></div>

        <div class="savebar sticky">
          <button class="primary" id="lay-save" type="button">Save layout</button>
          <button class="secondary" id="lay-reset" type="button">Back to the built-in arrangement</button>
          <span class="msg" id="lay-msg" role="status"></span>
        </div>`;
      bind(); paint(); drawList(); inspector();
    }

    function bind() {
      const cv = $("#canvas");
      $("#lay-frame").addEventListener("load", post);
      $("#sample").onchange = (e) => { sample = e.target.checked; $("#lay-frame").src = `/?mode=${sample ? "demo" : "live"}`; };
      addEventListener("resize", paint);
      $("#wlist").onclick = (e) => { const t = e.target.dataset && e.target.dataset.type; if (t) select(t); };

      cv.addEventListener("pointerdown", (e) => {
        const el = e.target.closest(".lay-box");
        if (!el) return;
        const type = el.dataset.type, dir = (e.target.dataset && e.target.dataset.h) || "move";
        select(type);
        const from = { x: e.clientX, y: e.clientY };                 // where the pointer went down
        const box = { x: get(type).x, y: get(type).y, w: get(type).w, h: get(type).h };   // and the box as it was
        try { el.setPointerCapture(e.pointerId); } catch (_) { /* no live pointer to capture (a synthetic event) */ }
        el.classList.add("drag");
        const move = (ev) => {
          const dx = (ev.clientX - from.x) / scale, dy = (ev.clientY - from.y) / scale;
          let { x, y, w, h } = box;
          if (dir === "move") { x += dx; y += dy; }
          else {
            if (dir.includes("e")) w += dx;
            if (dir.includes("s")) h += dy;
            if (dir.includes("w")) { x += dx; w -= dx; }
            if (dir.includes("n")) { y += dy; h -= dy; }
          }
          fit(type, { x, y, w, h }, dir === "move" ? null : dir);
        };
        const up = () => {
          el.classList.remove("drag");
          el.removeEventListener("pointermove", move); el.removeEventListener("pointerup", up); el.removeEventListener("pointercancel", up);
        };
        el.addEventListener("pointermove", move); el.addEventListener("pointerup", up); el.addEventListener("pointercancel", up);
        e.preventDefault();
      });

      cv.addEventListener("keydown", (e) => {
        const d = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] }[e.key];
        if (!d) return;
        e.preventDefault();
        const w = get(selected);
        if (e.shiftKey) fit(selected, { x: w.x, y: w.y, w: w.w + d[0] * STEP, h: w.h + d[1] * STEP });
        else fit(selected, { x: w.x + d[0] * STEP, y: w.y + d[1] * STEP, w: w.w, h: w.h });
      });

      $("#lay-save").onclick = async () => {
        const msg = $("#lay-msg"); msg.className = "msg"; msg.textContent = "Saving…";
        try {
          const res = await api("settings", { layout: current() });
          FD.settings = res.settings; dirty = false;
          msg.textContent = ""; $("#lay-state").textContent = "Saved layout";
          toast("Saved. The TV updates within a minute.");
        } catch (err) { msg.className = "msg bad"; msg.textContent = err.message; }
      };
      $("#lay-reset").onclick = async () => {
        if (!confirm("Go back to the built-in arrangement? Your own layout will be lost.")) return;
        try {
          const res = await api("settings", { layout: null });
          FD.settings = res.settings;
          toast("Back to the built-in arrangement.");
          FD.rerender();
        } catch (err) { toast(err.message); }
      };
    }
    draw();
  };
})();
