/* Smart home: Home Assistant on the TV.
   Status tiles (locks, doors, garage doors, the alarm, who's home), cameras as a tile or a motion pop-up.
   The connection (address and token) is for whoever looks after the system; choosing what to show is for everyone. */
(function () {
  "use strict";
  const FD = (window.FD = window.FD || { pages: {} });

  const KIND = { lock: "Lock", opening: "Door or window", cover: "Garage or gate", alarm: "Alarm", person: "Person", motion: "Motion sensor", camera: "Camera" };
  const DOMAIN = { lock: "Lock", cover: "Garage or gate", alarm_control_panel: "Alarm", person: "Person", binary_sensor: "Sensor", camera: "Camera" };
  const label = (id, kind) => KIND[kind] || DOMAIN[id.split(".")[0]] || "";

  FD.pages.smarthome = function (page) {
    const { $, $$, esc, api, toast, runTest } = FD;
    const ha = FD.settings.ha;
    const connected = !!(ha.url && ha.token.set);
    let ents = ha.entities.map((e) => ({ ...e }));
    let cams = ha.cameras.map((c) => ({ ...c, motion: [...(c.motion || [])] }));
    let all = null, filter = "all", term = "";                      // what Home Assistant has, once asked

    page.innerHTML = `
      <h1>Smart home</h1>
      <p class="lede">Show locks, doors, garage doors, the alarm and who's home on the TV, and show cameras in a tile or as a pop-up when there's motion. This uses Home Assistant.</p>
      <p class="hint simple-only" style="margin-left:0">${connected && ha.enabled ? "Connected to Home Assistant." : "Not connected yet. Ask whoever set up the dashboard, or switch on Expert settings."}</p>

      <div class="expert-only">
        <h2>Connection</h2>
        <form id="ha-conn" novalidate>
          <div class="group">
            <div class="item"><label for="ha-on">Use Home Assistant</label><input id="ha-on" type="checkbox" ${ha.enabled ? "checked" : ""}></div>
            <div class="item"><label for="ha-url">Address</label>
              <input id="ha-url" type="text" inputmode="url" autocomplete="off" value="${esc(ha.url)}" placeholder="http://homeassistant.local:8123">
              <span class="sub">Where the dashboard <b>server</b> can reach Home Assistant. That's the server's view of the network, not your phone's.</span></div>
            <div class="item"><label for="ha-token">Access token</label>
              <input id="ha-token" type="password" autocomplete="off" placeholder="${esc(FD.secretPlaceholder(ha.token, "Paste a long-lived access token"))}"></div>
          </div>
          <details>
            <summary>How to make a token</summary>
            <ol>
              <li>In Home Assistant, open your <b>profile</b> (your name, bottom left) and go to the <b>Security</b> tab.</li>
              <li>Scroll to <b>Long-lived access tokens</b> and select <b>Create token</b>. Name it "Family Dashboard".</li>
              <li>Copy the token (it's shown once), paste it above and select <b>Test</b>.</li>
            </ol>
            <p>The token acts as the account that made it, so it's kept only on the dashboard server and never shown again. Making a token for a user that can only see what the TV needs is the safest choice. To cut the dashboard off, delete the token in the same place.</p>
          </details>
          <div class="savebar"><button class="primary" type="submit">Save connection</button><button class="secondary" type="button" id="ha-test">Test</button><span class="msg" role="status"></span></div>
          <p class="test-result" id="ha-out"></p>
        </form>
      </div>

      <form id="ha-form" novalidate>
        <h2>Status tiles</h2>
        <div class="ha-list" id="tiles"></div>
        <h2>Cameras</h2>
        <div class="ha-list" id="cams"></div>

        <h2>Pop-ups and warnings</h2>
        <div class="group">
          <div class="item"><label for="pu-on">Pop up on motion</label><input id="pu-on" type="checkbox" ${ha.popup.enabled ? "checked" : ""}>
            <span class="sub">When a camera's motion sensor fires, a big picture of it appears over everything, then goes away by itself.</span></div>
          <div class="item"><label for="pu-sec">Stay up for</label>
            <select id="pu-sec">${[15, 30, 60, 120].map((v) => `<option value="${v}" ${ha.popup.seconds === v ? "selected" : ""}>${v} seconds after the motion stops</option>`).join("")}</select></div>
          <div class="item"><label for="al-min">Warn when open or unlocked for</label>
            <select id="al-min">${[[0, "Never"], [5, "5 minutes"], [10, "10 minutes"], [30, "30 minutes"], [60, "1 hour"]].map(([v, l]) => `<option value="${v}" ${ha.alert_minutes === v ? "selected" : ""}>${l}</option>`).join("")}</select>
            <span class="sub">A lock or door left that long turns its tile red and says for how long.</span></div>
        </div>

        <div class="expert-only">
          <h2>Add from Home Assistant</h2>
          <div class="group"><div class="item" style="grid-template-columns:1fr">
            <div class="inline"><button type="button" class="secondary" id="load">${connected ? "Load what Home Assistant has" : "Save the connection first"}</button><span class="msg" id="load-msg" role="status"></span></div>
            <div id="finder" hidden>
              <div class="wlist" id="kinds" style="margin:10px 0"></div>
              <input id="find" type="search" autocomplete="off" placeholder="Search by name">
              <div class="results" id="found" role="list" style="grid-column:1;margin-top:8px"></div>
            </div>
          </div></div>
        </div>

        <h2>On the screen</h2>
        <div class="group"><div class="item" style="grid-template-columns:1fr">
          <p style="margin:0">The status strip and the camera tile are extra boxes in the TV layout. Add them here, then move and resize them in <a href="#layout">Layout</a> (under Expert settings).</p>
          <div class="inline"><button type="button" class="secondary" id="add-home">Add the status strip</button><button type="button" class="secondary" id="add-cam">Add a camera tile</button></div>
          <span class="sub" style="grid-column:1" id="add-msg" role="status"></span>
        </div></div>

        <div class="savebar sticky"><button class="primary" type="submit">Save changes</button><span class="msg" role="status"></span></div>
      </form>`;

    // ---- the two lists

    function drawTiles() {
      $("#tiles").innerHTML = ents.length ? ents.map((e, i) => `
        <div class="ha-row" data-i="${i}">
          <span class="ha-kind">${esc(label(e.id, (all || []).find((x) => x.id === e.id)?.kind))}</span>
          <input class="nm" type="text" value="${esc(e.name)}" placeholder="Name on the TV" aria-label="Name for ${esc(e.id)}" maxlength="40">
          <span class="meta">${esc(e.id)}</span>
          <button type="button" class="danger rm" aria-label="Remove ${esc(e.name || e.id)}">Remove</button>
        </div>`).join("") : `<div class="item"><span class="sub" style="grid-column:1">Nothing chosen yet.</span></div>`;
    }

    function drawCams() {
      const sensors = (all || []).filter((x) => x.kind === "motion");
      $("#cams").innerHTML = cams.length ? cams.map((c, i) => {
        const free = sensors.filter((s) => !c.motion.includes(s.id));
        return `<div class="ha-cam" data-i="${i}">
          <img class="ha-thumb" src="/api/ha/camera/${encodeURIComponent(c.id)}.jpg?t=${Date.now()}" alt="" onerror="this.style.visibility='hidden'">
          <div class="ha-fields">
            <input class="nm" type="text" value="${esc(c.name)}" placeholder="Name on the TV" aria-label="Name for ${esc(c.id)}" maxlength="40">
            <span class="meta">${esc(c.id)}</span>
            <div class="chips" style="grid-column:auto;margin:6px 0 0">
              ${c.motion.map((m) => `<span class="chip">${esc(m)}<button type="button" class="x" data-m="${esc(m)}" aria-label="Remove ${esc(m)}">&times;</button></span>`).join("") || `<span class="sub" style="grid-column:auto">No motion sensor: it won't pop up.</span>`}
              ${all && free.length && c.motion.length < 3 ? `<select class="addm" aria-label="Add a motion sensor" style="width:auto"><option value="">Add a motion sensor…</option>${free.map((s) => `<option value="${esc(s.id)}">${esc(s.name)}</option>`).join("")}</select>` : ""}
            </div>
            <label class="show" style="margin-top:6px"><input type="checkbox" class="pu" ${c.popup ? "checked" : ""}> Pop up for this camera</label>
          </div>
          <button type="button" class="danger rm" aria-label="Remove ${esc(c.name || c.id)}">Remove</button>
        </div>`;
      }).join("") : `<div class="item"><span class="sub" style="grid-column:1">No cameras chosen yet.</span></div>`;
    }

    // Keep what's typed in the lists when something else redraws them
    function sync() {
      $$("#tiles .ha-row").forEach((row) => { ents[Number(row.dataset.i)].name = $(".nm", row).value.trim(); });
      $$("#cams .ha-cam").forEach((row) => { const c = cams[Number(row.dataset.i)]; c.name = $(".nm", row).value.trim(); c.popup = $(".pu", row).checked; });
    }

    $("#tiles").onclick = (e) => {
      if (!e.target.classList.contains("rm")) return;
      sync(); ents.splice(Number(e.target.closest(".ha-row").dataset.i), 1); drawTiles(); drawFound();
    };
    $("#cams").onclick = (e) => {
      sync();
      const row = e.target.closest(".ha-cam"); if (!row) return;
      const c = cams[Number(row.dataset.i)];
      if (e.target.classList.contains("rm")) { cams.splice(Number(row.dataset.i), 1); drawCams(); drawFound(); }
      else if (e.target.dataset && e.target.dataset.m) { c.motion = c.motion.filter((m) => m !== e.target.dataset.m); drawCams(); }
    };
    $("#cams").onchange = (e) => {
      if (!e.target.classList.contains("addm") || !e.target.value) return;
      sync(); cams[Number(e.target.closest(".ha-cam").dataset.i)].motion.push(e.target.value); drawCams();
    };

    // ---- what Home Assistant has

    const KINDS = [["all", "Everything"], ["lock", "Locks"], ["opening", "Doors and windows"], ["cover", "Garage and gates"], ["alarm", "Alarm"], ["person", "People"], ["motion", "Motion"], ["camera", "Cameras"]];
    function drawFound() {
      if (!all) return;
      $("#kinds").innerHTML = KINDS.map(([k, l]) => `<button type="button" class="wchip${filter === k ? " sel" : ""}" data-k="${k}">${l}</button>`).join("");
      const have = new Set([...ents.map((e) => e.id), ...cams.map((c) => c.id)]);
      const rows = all.filter((x) => (filter === "all" || x.kind === filter) && (!term || (x.name + " " + x.id).toLowerCase().includes(term))).slice(0, 60);
      $("#found").innerHTML = rows.length ? rows.map((x) => `
        <div class="res" role="listitem"><div><b>${esc(x.name)}</b> <small>${esc(label(x.id, x.kind))} · ${esc(x.id)}</small></div>
          <button type="button" class="secondary" data-add="${esc(x.id)}" ${have.has(x.id) ? "disabled" : ""}>${have.has(x.id) ? "Added" : x.kind === "camera" ? "Add camera" : "Add tile"}</button></div>`).join("")
        : `<p class="sub" style="grid-column:auto">Nothing matches.</p>`;
    }
    $("#load").onclick = async () => {
      const msg = $("#load-msg"); msg.className = "msg"; msg.textContent = "Asking Home Assistant…";
      try {
        all = (await api("ha/entities")).entities;
        msg.textContent = `${all.length} items found.`; $("#finder").hidden = false;
        sync(); drawFound(); drawCams(); drawTiles();
      } catch (err) { msg.className = "msg bad"; msg.textContent = err.message; }
    };
    $("#kinds").onclick = (e) => { if (e.target.dataset.k) { filter = e.target.dataset.k; drawFound(); } };
    $("#find").oninput = (e) => { term = e.target.value.trim().toLowerCase(); drawFound(); };
    $("#found").onclick = (e) => {
      const id = e.target.dataset && e.target.dataset.add; if (!id) return;
      const x = all.find((y) => y.id === id); sync();
      if (x.kind === "camera") { if (cams.length >= 8) return toast("Up to 8 cameras fit."); cams.push({ id, name: x.name, motion: [], popup: true }); drawCams(); }
      else { if (ents.length >= 24) return toast("Up to 24 tiles fit."); ents.push({ id, name: x.name }); drawTiles(); }
      drawFound();
    };

    // ---- saving

    const conn = () => { const out = { ha: { enabled: $("#ha-on").checked, url: $("#ha-url").value.trim() } }; if ($("#ha-token").value.trim()) out.ha.token = $("#ha-token").value.trim(); return out; };
    FD.bindSave($("#ha-conn"), conn, () => FD.rerender());
    $("#ha-test").onclick = () => runTest("ha", conn(), $("#ha-out"));
    FD.bindSave($("#ha-form"), () => {
      sync();
      return { ha: {
        entities: ents.map(({ id, name }) => ({ id, name })),
        cameras: cams.map((c) => ({ id: c.id, name: c.name, motion: c.motion, popup: c.popup })),
        popup: { enabled: $("#pu-on").checked, seconds: Number($("#pu-sec").value) },
        alert_minutes: Number($("#al-min").value),
      } };
    }, () => FD.rerender());

    // ---- put the widgets on the screen
    const addTo = async (type, okText) => {
      const msg = $("#add-msg");
      try {
        msg.textContent = (await FD.addWidget(type)) === "already" ? "That's already on the screen." : okText;
      } catch (err) { msg.textContent = err.message; }
    };
    $("#add-home").onclick = () => addTo("home", "Added. The TV updates within a minute; move it in Layout if you like.");
    $("#add-cam").onclick = () => addTo("camera", "Added. The TV updates within a minute; choose which camera it shows in Layout.");

    drawTiles(); drawCams();
  };
})();
