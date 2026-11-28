/* Look: what sits behind the calendar, the light/dark appearance, the clock and the overnight dimming. */
(function () {
  "use strict";
  const FD = (window.FD = window.FD || { pages: {} });

  // Gradients and colors, each with the appearance that reads best on it (dark text on light, white on dark)
  const GRADIENTS = [
    { id: "midnight", name: "Midnight", a: "#0B1626", b: "#1A3259", angle: 180, look: "dark" },
    { id: "aurora", name: "Aurora", a: "#0F2027", b: "#2C5364", angle: 160, look: "dark" },
    { id: "ocean", name: "Ocean", a: "#082032", b: "#1B6CA8", angle: 170, look: "dark" },
    { id: "dusk", name: "Dusk", a: "#2B1B4A", b: "#C75B7A", angle: 165, look: "dark" },
    { id: "ember", name: "Ember", a: "#2B1215", b: "#7A2E1D", angle: 160, look: "dark" },
    { id: "forest", name: "Forest", a: "#0F2A1D", b: "#2F6B4F", angle: 160, look: "dark" },
    { id: "slate", name: "Slate", a: "#1F2430", b: "#3B4252", angle: 180, look: "dark" },
    { id: "daybreak", name: "Daybreak", a: "#CFE2F6", b: "#F6E3C5", angle: 160, look: "light" },
    { id: "mist", name: "Mist", a: "#E7ECF3", b: "#C9D6E8", angle: 170, look: "light" },
    { id: "blush", name: "Blush", a: "#F8E1E7", b: "#F3C9B8", angle: 160, look: "light" },
  ];
  const SOLIDS = [
    { name: "Navy", c: "#0B1626", look: "dark" }, { name: "Charcoal", c: "#1C1C1E", look: "dark" },
    { name: "Pine", c: "#14281D", look: "dark" }, { name: "Wine", c: "#3B1F2B", look: "dark" },
    { name: "Cloud", c: "#E9EEF5", look: "light" }, { name: "Linen", c: "#F3EDE2", look: "light" },
  ];
  const css = (g) => `linear-gradient(${g.angle}deg, ${g.a}, ${g.b})`;

  // ---- the photo library (shared by "Family photos" and "One photo") ----

  // Shrink to TV size in the browser, so the Pi never has to decode a 12-megapixel photo
  function shrink(file) {
    return new Promise((resolve, reject) => {
      const url = URL.createObjectURL(file);
      const img = new Image();
      img.onload = () => {
        const k = Math.min(1, 1920 / Math.max(img.width, img.height));
        const w = Math.round(img.width * k), h = Math.round(img.height * k);
        const c = document.createElement("canvas");
        c.width = w; c.height = h;
        c.getContext("2d").drawImage(img, 0, 0, w, h);
        URL.revokeObjectURL(url);
        c.toBlob((b) => (b ? resolve(b) : reject(new Error("Couldn't read that photo"))), "image/jpeg", 0.86);
      };
      img.onerror = () => { URL.revokeObjectURL(url); reject(new Error(`${file.name} isn't a photo this browser can open`)); };
      img.src = url;
    });
  }

  FD.pages.look = function (page) {
    const { $, $$, esc, api, toast } = FD;
    const d = FD.settings.display;
    const v = {
      background: d.background, bgColor: d.bgColor, bgGradient: { ...d.bgGradient }, bgPreset: d.bgPreset || "",
      bgPhoto: d.bgPhoto || "", photoDim: d.photoDim, appearance: d.appearance, photoMinutes: d.photoMinutes,
    };
    let photos = [];

    const MODES = [
      ["sky", "Weather sky", "Follows the time of day and the weather", "background:linear-gradient(160deg,#6FA8DC,#CFE2F6 55%,#F6CF72)"],
      ["photos", "Family photos", "A slideshow of your photos", "background:linear-gradient(135deg,#8E6B4F,#C9A27E 40%,#5E7C5B)"],
      ["photo", "One photo", "A single favourite", "background:linear-gradient(135deg,#3D5A80,#98C1D9 60%,#EE6C4D)"],
      ["gradient", "Gradient", "Two colors blending", "background:linear-gradient(160deg,#0F2027,#2C5364)"],
      ["color", "Solid color", "One flat color", "background:#14281D"],
    ];

    function opts() {
      if (v.background === "sky") return `<p class="hint" style="margin-left:0">The sky turns light from sunrise to sunset and dark at night, and shows clouds, rain and snow as they happen.</p>`;
      if (v.background === "gradient") {
        return `<div class="swatches" role="radiogroup" aria-label="Gradients">
            ${GRADIENTS.map((g) => { const on = v.bgPreset === g.id || (g.a === v.bgGradient.a.toUpperCase() && g.b === v.bgGradient.b.toUpperCase() && g.angle === v.bgGradient.angle); return `<button type="button" class="swatch${on ? " on" : ""}" data-grad="${g.id}" style="background:${css(g)}" aria-label="${esc(g.name)}" aria-pressed="${on}"><span>${esc(g.name)}</span></button>`; }).join("")}
          </div>
          <div class="group" style="margin-top:12px">
            <div class="item"><span class="label">Your own</span><div class="inline">
              <input type="color" id="g-a" value="${esc(v.bgGradient.a)}" aria-label="First color"><span>to</span>
              <input type="color" id="g-b" value="${esc(v.bgGradient.b)}" aria-label="Second color">
              <label class="tfield">Angle <input type="range" id="g-angle" min="0" max="359" value="${v.bgGradient.angle}" style="width:140px"></label></div></div>
          </div>`;
      }
      if (v.background === "color") {
        return `<div class="swatches" role="radiogroup" aria-label="Colors">
            ${SOLIDS.map((s) => `<button type="button" class="swatch${v.bgColor === s.c ? " on" : ""}" data-solid="${s.c}" data-look="${s.look}" style="background:${s.c}" aria-label="${esc(s.name)}" aria-pressed="${v.bgColor === s.c}"><span>${esc(s.name)}</span></button>`).join("")}
          </div>
          <div class="group" style="margin-top:12px"><div class="item"><label for="c-own">Your own</label><div class="inline"><input type="color" id="c-own" value="${esc(v.bgColor)}"></div></div></div>`;
      }
      // photos / one photo
      return `<div class="group">
          ${v.background === "photos" ? `<div class="item"><label for="pmin">Change photo every</label>
            <select id="pmin">${[1, 5, 10, 30, 60, 1440].map((m) => `<option value="${m}" ${v.photoMinutes === m ? "selected" : ""}>${m === 1440 ? "day" : m === 60 ? "hour" : `${m} minute${m > 1 ? "s" : ""}`}</option>`).join("")}</select></div>` : ""}
          <div class="item"><label for="pdim">Darken the photo</label>
            <div class="inline"><input type="range" id="pdim" min="0" max="80" value="${v.photoDim}" style="width:200px"><output id="pdim-out">${v.photoDim}%</output></div>
            <span class="sub">Darker photos make the writing easier to read.</span></div>
          <div class="item" style="grid-template-columns:1fr">
            <div class="inline"><label class="secondary filebtn">Add photos<input id="pfile" type="file" accept="image/*" multiple hidden></label><span class="msg" id="pmsg" role="status"></span></div>
            <div class="thumbs" id="thumbs"></div>
            <span class="sub" style="grid-column:1">${v.background === "photo" ? "Select the photo to use. " : ""}Photos are resized to TV size on this device before they upload, so phone photos are fine. Landscape photos fill the screen best.</span>
          </div></div>`;
    }

    function draw() {
      page.innerHTML = `
        <h1>Look</h1>
        <p class="lede">What sits behind the calendar, light or dark, and how the clock and the overnight dimming behave.</p>
        <form id="look-form" novalidate>
          <h2>Background</h2>
          <div class="bgmodes" role="radiogroup" aria-label="Background">
            ${MODES.map(([id, name, sub, sw]) => `<label class="bgcard${v.background === id ? " on" : ""}"><input type="radio" name="bg" value="${id}" ${v.background === id ? "checked" : ""}><span class="sw" style="${sw}"></span><b>${name}</b><small>${sub}</small></label>`).join("")}
          </div>
          <div id="bgopts">${opts()}</div>

          <h2>Light or dark</h2>
          <div class="group"><div class="item"><span class="label">Appearance</span>
            <div class="inline">
              ${[["auto", "Automatic"], ["dark", "Always dark"], ["light", "Always light"]].map(([k, l]) => `<label class="show"><input type="radio" name="ap" value="${k}" ${v.appearance === k ? "checked" : ""}> ${l}</label>`).join("")}
            </div>
            <span class="sub">Automatic is light from sunrise to sunset and dark at night. Pick one if your background is always light or always dark.</span></div></div>

          <h2>Clock</h2>
          <div class="group"><div class="item"><label for="tf">Style</label><select id="tf"><option value="12h" ${d.timeFormat === "12h" ? "selected" : ""}>12-hour (7:20 PM)</option><option value="24h" ${d.timeFormat === "24h" ? "selected" : ""}>24-hour (19:20)</option></select></div></div>

          <h2>Overnight dimming</h2>
          <div class="group">
            <div class="item"><label for="dim-on">Dim the screen</label><input id="dim-on" type="checkbox" ${d.nightDim.enabled ? "checked" : ""}>
              <span class="sub">Dimming is separate from sleep. To switch the screen off, use <a href="#schedule">Schedule</a>.</span></div>
            <div class="item"><span class="label">From</span><div class="inline"><input id="dim-start" type="time" value="${esc(d.nightDim.start)}" aria-label="Dim from" style="max-width:150px"> until <input id="dim-end" type="time" value="${esc(d.nightDim.end)}" aria-label="Dim until" style="max-width:150px"></div></div>
            <div class="item"><label for="dim-level">How dark</label><select id="dim-level">${[0.4, 0.6, 0.8].map((x) => `<option value="${x}" ${Math.abs(d.nightDim.level - x) < 0.05 ? "selected" : ""}>${x === 0.4 ? "A little" : x === 0.6 ? "Medium" : "Very dark"}</option>`).join("")}</select></div>
          </div>

          <div class="expert-only">
            <h2>Technical</h2>
            <div class="group">
              <div class="item"><label for="tz">Time zone</label><input id="tz" type="text" value="${esc(FD.settings.timezone)}"><span class="sub">The household's time zone, like America/Chicago or Europe/London. Sunrise, schedules and the calendar use it.</span></div>
              <div class="item"><label for="pfolder">Photo folder</label><input id="pfolder" type="text" value="${esc(FD.settings.photos.folder)}" placeholder="The dashboard's own photo folder">
                <span class="sub">Leave empty to use the photos added above. To use another folder on the machine that runs the dashboard, enter its full path.</span></div>
              <div class="item"><label for="perf">Glass effect</label>
                <select id="perf"><option value="auto" ${d.perf === "auto" ? "selected" : ""}>Automatic</option><option value="lite" ${d.perf === "lite" ? "selected" : ""}>Off (smoother on small boards)</option><option value="full" ${d.perf === "full" ? "selected" : ""}>On</option></select>
                <span class="sub">Automatic turns the blur off by itself on boards with 1 GB of memory or less, or if the screen can't keep up.</span></div>
              <div class="item"><label for="share">Others can view</label><input id="share" type="checkbox" ${d.shareOnNetwork ? "checked" : ""}>
                <span class="sub">Lets anyone on the home network open the dashboard (not these settings) without signing in. Leave off if guests use your Wi-Fi.</span></div>
            </div>
          </div>
          <div class="savebar sticky"><button class="primary" type="submit">Save changes</button><span class="msg" role="status"></span></div>
        </form>`;
      bind();
      if (["photos", "photo"].includes(v.background)) showThumbs();
    }

    async function showThumbs() {
      const box = $("#thumbs");
      if (!box) return;
      try {
        const r = await fetch("/api/photos", { cache: "no-store" });
        photos = (await r.json()).photos || [];
        const single = v.background === "photo";
        box.innerHTML = photos.length
          ? photos.map((n) => `<figure class="${single && v.bgPhoto === n ? "on" : ""}">
              <img src="/photos/${encodeURIComponent(n)}" alt="" loading="lazy">
              <div>${single ? `<button type="button" class="secondary" data-use="${esc(n)}" aria-pressed="${v.bgPhoto === n}">${v.bgPhoto === n ? "In use" : "Use"}</button>` : ""}
              <button type="button" class="danger" data-del="${esc(n)}" aria-label="Delete this photo">Delete</button></div></figure>`).join("")
          : `<p class="sub" style="margin:0">No photos yet.</p>`;
      } catch (_) {
        box.innerHTML = `<p class="sub" style="margin:0">Couldn't load the photo list.</p>`;
      }
    }

    async function upload(files) {
      const msg = $("#pmsg");
      let done = 0;
      for (const f of files) {
        msg.className = "msg"; msg.textContent = `Adding ${done + 1} of ${files.length}…`;
        try {
          const blob = await shrink(f);
          const r = await fetch(`api/photos/upload?name=${encodeURIComponent(f.name.replace(/\.[^.]*$/, "") + ".jpg")}`, {
            method: "POST", headers: { "X-Dashboard": "1", "Content-Type": "image/jpeg" }, body: blob });
          if (!r.ok) throw new Error((await r.json().catch(() => ({}))).error || "Upload failed");
          const out = await r.json();
          if (v.background === "photo" && !v.bgPhoto) v.bgPhoto = out.name;
          done++;
        } catch (err) { msg.className = "msg bad"; msg.textContent = err.message; return showThumbs(); }
      }
      msg.textContent = `Added ${done} photo${done === 1 ? "" : "s"}.`;
      showThumbs();
    }

    function bind() {
      const form = $("#look-form");
      $$("input[name=bg]", form).forEach((r) => (r.onchange = () => { v.background = r.value; keepAppearance(); draw(); }));
      $$("input[name=ap]", form).forEach((r) => (r.onchange = () => { v.appearance = r.value; }));
      const bgopts = $("#bgopts");
      bgopts.addEventListener("click", async (e) => {
        const t = e.target.closest("button"); if (!t) return;
        if (t.dataset.grad) {
          const g = GRADIENTS.find((x) => x.id === t.dataset.grad);
          v.bgPreset = g.id; v.bgGradient = { a: g.a, b: g.b, angle: g.angle }; v.appearance = g.look; draw();
        } else if (t.dataset.solid) {
          v.bgColor = t.dataset.solid; v.bgPreset = ""; v.appearance = t.dataset.look; draw();
        } else if (t.dataset.use) { v.bgPhoto = t.dataset.use; showThumbs();
        } else if (t.dataset.del) {
          try {
            await api("photos/delete", { name: t.dataset.del });
            if (v.bgPhoto === t.dataset.del) v.bgPhoto = "";
            showThumbs();
          } catch (err) { toast(err.message); }
        }
      });
      const bind1 = (id, fn) => { const el = $(id); if (el) el.addEventListener("input", () => fn(el)); };
      bind1("#g-a", (el) => { v.bgGradient.a = el.value; v.bgPreset = ""; });
      bind1("#g-b", (el) => { v.bgGradient.b = el.value; v.bgPreset = ""; });
      bind1("#g-angle", (el) => { v.bgGradient.angle = Number(el.value); v.bgPreset = ""; });
      bind1("#c-own", (el) => { v.bgColor = el.value; });
      bind1("#pmin", (el) => { v.photoMinutes = Number(el.value); });
      bind1("#pdim", (el) => { v.photoDim = Number(el.value); $("#pdim-out").textContent = `${v.photoDim}%`; });
      const file = $("#pfile"); if (file) file.onchange = (e) => upload([...e.target.files]);

      FD.bindSave(form, () => ({
        timezone: $("#tz").value.trim(),
        photos: { folder: $("#pfolder").value.trim() },
        display: {
          background: v.background, bgColor: v.bgColor, bgGradient: v.bgGradient, bgPreset: v.bgPreset, bgPhoto: v.bgPhoto,
          photoDim: v.photoDim, appearance: v.appearance, photoMinutes: v.photoMinutes,
          timeFormat: $("#tf").value, perf: $("#perf").value, shareOnNetwork: $("#share").checked,
          nightDim: { enabled: $("#dim-on").checked, start: $("#dim-start").value, end: $("#dim-end").value, level: Number($("#dim-level").value) },
        },
      }), () => FD.rerender());
    }

    // A fresh background choice keeps a sensible appearance: the sky is automatic, a dark gradient is dark
    function keepAppearance() {
      if (v.background === "sky") v.appearance = "auto";
      if (v.background === "gradient") { const g = GRADIENTS.find((x) => x.id === v.bgPreset); if (g) v.appearance = g.look; }
    }
    draw();
  };
})();
