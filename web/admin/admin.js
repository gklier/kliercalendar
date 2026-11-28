/* Family Dashboard settings page. Talks to /admin/api/* on the Pi. */
(function () {
  "use strict";

  // Shared with the page modules (pages-*.js), which load first and register themselves in FD.pages
  const FD = (window.FD = window.FD || { pages: {} });

  const $ = (sel, root) => (root || document).querySelector(sel);
  const $$ = (sel, root) => [...(root || document).querySelectorAll(sel)];
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const ICONS = { home: "Home", work: "Briefcase", school: "School", heart: "Heart", cart: "Store", fitness: "Gym", plane: "Airport", pin: "Pin", star: "Star" };

  let settings = null;
  let status = null;

  // ---------- API ----------

  async function api(path, body) {
    const opts = { headers: { "X-Dashboard": "1" }, cache: "no-store" };
    if (body !== undefined) {
      opts.method = "POST";
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(body);
    }
    let r;
    try {
      r = await fetch(`api/${path}`, opts);
    } catch (_) {
      throw new Error("The dashboard server isn't answering. Check that the Pi is on.");
    }
    const data = await r.json().catch(() => ({}));
    if (r.status === 401 && path !== "login" && path !== "password") { showGate(); throw new Error("Sign in again"); }
    if (!r.ok) throw new Error(data.error || `Request failed (${r.status})`);
    return data;
  }

  function toast(msg) {
    const t = $("#toast");
    t.textContent = msg;
    t.classList.add("show");
    clearTimeout(toast.timer);
    toast.timer = setTimeout(() => t.classList.remove("show"), 3200);
  }

  // A pop-up that holds a new TV key until it is closed. The key is shown once, so it stays until the person says so.
  function keyDialog(name, key) {
    const server = location.origin;
    const items = [
      ["Fire TV, tablet or any browser", "Open this address on the screen, then leave it up.", `${server}/tv#k=${key}`],
      ["Raspberry Pi", "Run this on the Pi.", `cd ~/family-dashboard && sudo DASHBOARD_SERVER=${server} DASHBOARD_TV_KEY=${key} ./pi/install-display.sh`],
      ["The key alone", "For anything else.", key],
    ];
    const dlg = document.createElement("dialog");
    dlg.className = "keydlg";
    dlg.setAttribute("aria-labelledby", "keydlg-h");
    dlg.innerHTML = `<h2 id="keydlg-h">TV key for ${esc(name)}</h2>
      <p class="hint" style="margin-left:0">Copy what you need now. For safety the key is shown only this once, and closing this window can't bring it back.</p>
      ${items.map(([title, note, text], i) => `<div class="keyrow"><b>${esc(title)}</b><span class="sub">${esc(note)}</span>
        <pre><code>${esc(text)}</code></pre>
        <button class="secondary" type="button" data-copy="${i}">Copy</button></div>`).join("")}
      <p class="hint" style="margin-left:0">If this page was opened by a name or address the screen can't reach, use the server's own address instead.</p>
      <div class="savebar"><button type="button" id="keydlg-close">Done, I've copied it</button></div>`;
    document.body.appendChild(dlg);
    const copy = async (text) => {
      try {
        if (navigator.clipboard && window.isSecureContext) { await navigator.clipboard.writeText(text); return true; }
      } catch (e) { /* fall through */ }
      const t = document.createElement("textarea");        // plain http on the home network has no clipboard API
      t.value = text;
      t.style.cssText = "position:fixed;opacity:0";
      dlg.appendChild(t);
      t.select();
      let ok = false;
      try { ok = document.execCommand("copy"); } catch (e) { /* ignore */ }
      t.remove();
      return ok;
    };
    dlg.querySelectorAll("[data-copy]").forEach((b) => b.onclick = async () => {
      const ok = await copy(items[+b.dataset.copy][2]);
      b.textContent = ok ? "Copied" : "Press and hold to select it above";
      setTimeout(() => { b.textContent = "Copy"; }, 2500);
    });
    const close = () => { dlg.close(); dlg.remove(); };
    dlg.querySelector("#keydlg-close").onclick = close;
    dlg.addEventListener("cancel", (e) => { e.preventDefault(); });     // Escape does not close it by accident
    dlg.showModal();
    dlg.querySelector("[data-copy]").focus();
  }

  function ago(sec) {
    if (!sec) return "not yet";
    const m = Math.round((Date.now() / 1000 - sec) / 60);
    return m < 1 ? "just now" : m < 60 ? `${m} min ago` : `${Math.round(m / 60)} hr ago`;
  }

  // ---------- Sign in ----------

  async function showGate() {
    $("#app").hidden = true;
    const gate = $("#gate");
    gate.hidden = false;
    let setup = false;
    try { setup = (await api("session")).setupNeeded; } catch (_) { /* show sign in anyway */ }
    gate.innerHTML = `
      <form id="gate-form">
        <h1>${setup ? "Create a password" : "Family Dashboard"}</h1>
        <p>${setup ? "This protects the settings, calendar links and keys on the Pi. Anyone on your Wi-Fi can reach this page, so pick something the kids won't guess." : "Sign in to change what the TV shows."}</p>
        <input id="pw" type="password" aria-label="Password" autocomplete="${setup ? "new-password" : "current-password"}" placeholder="Password" required minlength="${setup ? 8 : 1}">
        ${setup ? `<input id="pw2" type="password" autocomplete="new-password" placeholder="Type it again" aria-label="Type the password again" required>` : ""}
        <button class="primary" type="submit">${setup ? "Create password" : "Sign in"}</button>
        <p class="err" id="gate-err" role="alert"></p>
      </form>`;
    $("#pw").focus();
    $("#gate-form").onsubmit = async (e) => {
      e.preventDefault();
      const pw = $("#pw").value;
      if (setup && pw !== $("#pw2").value) { $("#gate-err").textContent = "The two passwords don't match."; return; }
      try {
        await api(setup ? "setup" : "login", { password: pw });
        gate.hidden = true;
        start();
      } catch (err) {
        $("#gate-err").textContent = err.message;
      }
    };
  }

  // ---------- Shell ----------

  async function start() {
    try {
      [settings, status] = await Promise.all([api("settings"), api("status")]);
    } catch (err) {
      return;
    }
    $("#app").hidden = false;
    setExpert(isExpert());
    $("#expert").onchange = (e) => { setExpert(e.target.checked); route(); };
    route();
  }

  const PAGES = { tv: pageTV, calendars: pageCalendars, drive: pageDrive, weather: pageWeather, stocks: pageStocks, account: pageAccount, ...FD.pages };

  // Simple settings show what the family needs day to day. "Expert settings" adds the rest.
  const EXPERT_PAGES = new Set(["layout", "drive", "weather", "backup", "about", "assistant"]);
  const isExpert = () => { try { return localStorage.getItem("fd_expert") === "1"; } catch (_) { return false; } };
  function setExpert(on) {
    try { localStorage.setItem("fd_expert", on ? "1" : "0"); } catch (_) { /* private window: just this visit */ }
    document.body.classList.toggle("expert", on);
    const sw = $("#expert"); if (sw) sw.checked = on;
  }

  function route() {
    let name = PAGES[location.hash.slice(1)] ? location.hash.slice(1) : "tv";
    if (EXPERT_PAGES.has(name) && !isExpert()) {
      toast("That page is under Expert settings. Switch them on at the bottom of the menu.");
      location.hash = "#tv";
      return;
    }
    $$("#nav a").forEach((a) => (a.getAttribute("href") === `#${name}` ? a.setAttribute("aria-current", "page") : a.removeAttribute("aria-current")));
    const page = $("#page");
    page.innerHTML = "";
    PAGES[name](page);
    page.focus({ preventScroll: true });
    scrollTo(0, 0);
  }

  addEventListener("hashchange", () => { if (settings) route(); });

  // Every link that leaves these settings opens in a new tab; the section links (#…) stay here
  document.addEventListener("click", (e) => {
    const a = e.target.closest && e.target.closest("a[href]");
    if (!a || a.hasAttribute("download") || (a.getAttribute("href") || "").startsWith("#") || e.defaultPrevented) return;
    e.preventDefault();
    window.open(a.href, "_blank", "noopener");
  });

  function saveBar(label, sticky) {
    return `<div class="savebar${sticky ? " sticky" : ""}"><button class="primary" type="submit">${label || "Save changes"}</button><span class="msg" role="status"></span></div>`;
  }

  // Submit a page's changes; the server validates and answers with the saved settings
  function bindSave(form, collect, done) {
    form.onsubmit = async (e) => {
      e.preventDefault();
      const msg = $(".savebar .msg", form);
      const btn = $(".savebar button", form);
      msg.className = "msg";
      msg.textContent = "Saving…";
      btn.disabled = true;
      try {
        const res = await api("settings", collect());
        settings = res.settings;
        status = await api("status").catch(() => status);
        msg.textContent = "";
        toast("Saved. The TV updates within a minute.");
        if (done) done(res);
      } catch (err) {
        msg.className = "msg bad";
        msg.textContent = err.message;
      } finally {
        btn.disabled = false;
      }
    };
  }

  async function runTest(kind, body, out) {
    out.className = "test-result";
    out.textContent = "Testing…";
    try {
      const res = await api(`test/${kind}`, body);
      out.className = `test-result state ${res.ok ? "good" : "bad"}`;
      out.textContent = res.message;
    } catch (err) {
      out.className = "test-result state bad";
      out.textContent = err.message;
    }
  }

  const secretPlaceholder = (m, empty) => (m && m.set ? `Saved${m.hint ? `, ends in ${m.hint.slice(1)}` : ""}. Paste a new one to replace it.` : empty);

  // ---------- On the TV ----------

  function sourceState(key, label) {
    const s = status.sources[key];
    if (s.ok) return `<span class="state good">${label} updated ${ago(s.fetchedAt)}</span>`;
    if (s.error) return `<span class="state bad">${label}: ${esc(s.error)}</span>`;
    return `<span class="state">${label}: waiting for the first update</span>`;
  }

  function mapsUsage() {
    const m = status.maps;
    if (!m) return "";
    if (!m.limit) return "Google Maps is switched off (monthly limit 0).";
    return `${m.used} of ${m.limit} Google Maps reads used this month, ${m.today} today (about ${Math.floor(m.todayAllowance)} allowed today).`;
  }

  function pageTV(page) {
    const cals = status.calendars;
    const linked = cals.filter((c) => c.ok === true).length;
    const broken = cals.filter((c) => c.ok === false).length;
    page.innerHTML = `
      <h1>On the TV</h1>
      <p class="lede">A live copy of what the TV shows, and a button to reload it.</p>

      <div class="preview" id="preview"><iframe src="/?mode=live" title="Preview of the family dashboard" loading="lazy"></iframe></div>
      <p class="caption">A live copy of what the TV shows.</p>

      <p class="hint" id="sleep-line">${FD.sleepLine ? FD.sleepLine() : ""}</p>
      <p class="hint" id="switch-note">${status.screen.kiosk
        ? "Reloading restarts the TV's browser. It takes about 15 seconds."
        : "The TV launcher isn't running yet, so the TV won't change until it has been installed on the Pi."}</p>

      <div class="expert-only">
      <h2>Connections</h2>
      <div class="group">
        <div class="item"><span class="label">Weather</span>${sourceState("weather", "Tempest")}</div>
        <div class="item"><span class="label">Calendars</span>
          <span class="state ${broken ? "bad" : linked ? "good" : ""}">${linked} of ${cals.length} loading${broken ? `, ${broken} with a problem` : ""}</span>
          <span class="sub"><a href="#calendars">Manage calendars</a></span>
        </div>
        <div class="item"><span class="label">Drive times</span>${sourceState("drive", "Google Maps")}
          <span class="sub">${mapsUsage()}</span></div>
        ${settings.stocks.enabled ? `<div class="item"><span class="label">Stocks</span>${sourceState("stocks", "Yahoo Finance")}</div>` : ""}
      </div>
      </div>

      <div class="savebar"><button class="secondary" id="reload" type="button">Reload the TV</button><span class="msg" id="reload-msg" role="status"></span></div>

      <div class="expert-only">
      <h2>More screens (TV keys)</h2>
      <p class="hint">The Pi on this machine needs nothing. Any other screen, such as a Fire TV, a second Pi or a tablet, gets its own key. Each key works on its own, so adding one never stops another.</p>
      <div class="group" id="tvkeys"></div>
      <div class="group">
        <div class="item"><span class="label">Add a screen</span>
          <div class="inline"><input type="text" id="tvname" maxlength="40" placeholder="Name, such as Fire TV" aria-label="Name for the new screen">
          <button class="secondary" type="button" id="tvkey">Make a TV key</button></div>
          </div>
      </div>
      </div>`;

    const drawKeys = () => {
      const list = settings.tv.devices || [];
      $("#tvkeys").innerHTML = list.length ? list.map((d) => `
        <div class="item"><span class="label">${esc(d.name)}</span>
          <span class="state good">Working${d.made ? ", made " + esc(d.made) : ""}</span>
          <div class="inline"><button class="secondary" type="button" data-stop="${esc(d.id)}">Stop this key</button></div></div>`).join("")
        : `<div class="item"><span class="label">No keys yet</span><span class="state">Not needed for the Pi on this machine.</span></div>`;
      $("#tvkeys").querySelectorAll("[data-stop]").forEach((b) => b.onclick = async () => {
        if (!confirm("Stop this key? The screen using it stops showing the dashboard. Other screens are not affected.")) return;
        try {
          settings = (await api("tv/revoke", { id: b.dataset.stop })).settings;
          drawKeys();
        } catch (err) {
          toast(err.message);
        }
      });
    };
    drawKeys();

    const frame = $("#preview iframe");
    const fitFrame = () => { if (frame) frame.style.transform = `scale(${$("#preview").clientWidth / 1920})`; };
    fitFrame();
    addEventListener("resize", fitFrame);

    $("#tvkey").onclick = async () => {
      try {
        const res = await api("tv/key", { name: $("#tvname").value });
        settings = res.settings;
        drawKeys();
        $("#tvname").value = "";
        keyDialog(res.name || "this screen", res.key);
      } catch (err) {
        toast(err.message);
      }
    };

    $("#reload").onclick = async () => {
      const msg = $("#reload-msg");
      try {
        const res = await api("screen/reload", {});
        msg.className = "msg";
        msg.textContent = res.restarted ? "Reloading. The TV will be back in about 15 seconds." : res.reason;
      } catch (err) {
        msg.className = "msg bad";
        msg.textContent = err.message;
      }
    };
  }

  // ---------- Calendars ----------

  function calState(st) {
    if (st && st.ok === true) return `<span class="state good">Working, ${st.count} events in the next two weeks</span>`;
    if (st && st.ok === false) return `<span class="state bad">${esc(st.error)}</span>`;
    if (st && st.note) return `<span class="state ${st.note === "Needs its link" ? "warn" : ""}">${esc(st.note)}</span>`;
    return `<span class="state">Not loaded yet</span>`;
  }

  function calRow(cal, st) {
    const google = cal.source === "google";
    const link = cal.link || {};
    return `<div class="cal" data-id="${esc(cal.id || "")}" data-source="${google ? "google" : "link"}">
      <input type="color" value="${esc(cal.color || "#0A84FF")}" aria-label="Colour for ${esc(cal.name)}">
      <div class="fields${google ? " one" : ""}">
        <input type="text" class="name" value="${esc(cal.name)}" placeholder="Name on the TV" aria-label="Name on the TV" maxlength="40" required>
        ${google ? "" : `<input type="text" class="link" inputmode="url" autocomplete="off" aria-label="Secret iCal link for ${esc(cal.name)}"
          placeholder="${link.set ? `Linked to ${esc(link.host)}. Paste a new link to replace it.` : "Paste the secret iCal link"}">`}
      </div>
      <div class="tools">
        ${google ? "" : `<button type="button" class="quiet test">Test</button><button type="button" class="danger remove" aria-label="Remove ${esc(cal.name)}">Remove</button>`}
      </div>
      <div class="foot">
        <label class="show"><input type="checkbox" class="enabled" ${cal.enabled !== false ? "checked" : ""}> Show on the TV</label>
        <span class="out">${calState(st)}</span>
      </div>
    </div>`;
  }

  function pageCalendars(page) {
    const st = Object.fromEntries(status.calendars.map((c) => [c.id, c]));
    const acc = settings.calendar.account;
    const cals = settings.calendar.calendars;
    const google = cals.filter((c) => c.source === "google");
    const links = cals.filter((c) => c.source !== "google");
    const connected = acc.email && acc.app_password.set;
    page.innerHTML = `
      <h1>Calendars</h1>
      <p class="lede">The TV shows the calendars from one Google account. Every calendar on that account is listed here; switch off the ones the family doesn't need to see.</p>

      <p class="hint simple-only" style="margin-left:0">${connected ? `Connected to Google as ${esc(acc.email)}. ${google.length} calendars found.` : "No Google account is connected yet. Ask whoever set up the dashboard, or switch on Expert settings."}</p>
      <div class="expert-only">
      <h2>Google account</h2>
      <form id="acct-form" novalidate>
        <div class="group">
          <div class="item"><label for="acct-email">Email</label>
            <input id="acct-email" type="text" inputmode="email" autocomplete="off" value="${esc(acc.email)}" placeholder="name@example.com"></div>
          <div class="item"><label for="acct-pw">App password</label>
            <input id="acct-pw" type="password" autocomplete="off" placeholder="${connected ? "Saved. Paste a new one to replace it." : "16 letters from Google"}"></div>
        </div>
        <div class="savebar">
          <button class="primary" type="submit">${connected ? "Refresh calendars" : "Connect and load calendars"}</button>
          <span class="msg" role="status">${connected ? `Connected. ${google.length} calendars found.` : ""}</span>
        </div>
      </form>
      <details>
        <summary>How to make an app password</summary>
        <ol>
          <li>Sign in to the Google account whose calendars the TV should show. Its 2-Step Verification must be on; Google only offers app passwords to accounts that have it.</li>
          <li>Open <a href="https://myaccount.google.com/apppasswords" target="_blank" rel="noopener">myaccount.google.com/apppasswords</a>.</li>
          <li>Type "Family Dashboard" as the name and select <b>Create</b>.</li>
          <li>Copy the 16 letters Google shows (it only shows them once), paste them above, and select <b>Connect</b>.</li>
        </ol>
        <p>An app password can open more of the account than its calendars, including mail, so it's kept only on the dashboard server and never shown again here. To cut the TV off, delete "Family Dashboard" on that same Google page. Changing the account's main password also removes it; make a new one and paste it here.</p>
        <p>Calendars other people share with the account appear here too. Holiday and other subscribed calendars may not; add those below with a link.</p>
      </details>
      <details>
        <summary>A calendar from the account is missing</summary>
        <p>Google only hands a calendar to apps like this one when it's ticked for syncing. Signed in as ${esc(acc.email || "the account")}, open <a href="https://calendar.google.com/calendar/syncselect">calendar.google.com/calendar/syncselect</a>, tick the missing calendars, select <b>Save</b>, then select <b>Refresh calendars</b> above.</p>
        <p>A calendar that still doesn't appear (Birthdays, or one from outside the family's Google Workspace) can be added below by its "Secret address in iCal format".</p>
      </details>
      </div>

      <form id="cal-form" novalidate>
        <h2>On the TV</h2>
        <div class="group"><div class="item"><label for="cal-start">First day shown</label>
          <select id="cal-start">
            <option value="today" ${settings.display.calStart !== "sunday" ? "selected" : ""}>Today, then the days after it</option>
            <option value="sunday" ${settings.display.calStart === "sunday" ? "selected" : ""}>Sunday, as a week (days already gone are dimmed)</option>
          </select></div></div>

        <h2>From the Google account</h2>
        <div class="group" id="gcals">${google.length ? google.map((c) => calRow(c, st[c.id])).join("") : `<div class="item"><span class="label">None yet</span><span class="sub" style="grid-column:2">Connect the account above to list its calendars.</span></div>`}</div>

        <div class="expert-only">
        <h2>Other calendars by link</h2>
        <div class="group" id="lcals">${links.map((c) => calRow(c, st[c.id])).join("")}</div>
        <p class="hint"><button type="button" class="quiet" id="add-cal">Add a calendar by link</button> For a calendar outside the account, such as a team schedule or holidays. Use its "Secret address in iCal format" or any public .ics link.</p>
        </div>
        ${saveBar("Save changes", true)}
      </form>`;

    $("#acct-form").onsubmit = async (e) => {
      e.preventDefault();
      const msg = $("#acct-form .msg");
      const btn = $("#acct-form button");
      msg.className = "msg";
      msg.textContent = "Asking Google for the calendar list…";
      btn.disabled = true;
      try {
        const body = { email: $("#acct-email").value.trim() };
        if ($("#acct-pw").value.trim()) body.app_password = $("#acct-pw").value.trim();
        const res = await api("calendar/refresh", body);
        settings = res.settings;
        status = await api("status").catch(() => status);
        toast(`Found ${res.found} calendars${res.added ? `, ${res.added} new` : ""}. The TV updates within a minute.`);
        pageCalendars(page);
      } catch (err) {
        msg.className = "msg bad";
        msg.textContent = err.message;
      } finally {
        btn.disabled = false;
      }
    };

    const list = $("#lcals");
    $("#add-cal").onclick = () => {
      list.insertAdjacentHTML("beforeend", calRow({ id: "", name: "", color: "#0A84FF", enabled: true, link: {} }, null));
      $(".cal:last-child .name", list).focus();
    };
    list.addEventListener("click", (e) => {
      const row = e.target.closest(".cal");
      if (!row) return;
      if (e.target.classList.contains("remove")) row.remove();
      if (e.target.classList.contains("test")) {
        runTest("calendar", { id: row.dataset.id || undefined, ics_url: $(".link", row).value.trim() || undefined }, $(".out", row));
      }
    });

    bindSave($("#cal-form"), () => ({
      display: { calStart: $("#cal-start").value },
      calendar: {
        calendars: $$(".cal", page).map((row) => ({
          id: row.dataset.id || undefined,
          name: $(".name", row).value.trim(),
          color: $("input[type=color]", row).value,
          ics_url: row.dataset.source === "link" ? $(".link", row).value.trim() || undefined : undefined,
          enabled: $(".enabled", row).checked,
        })),
      },
    }), () => pageCalendars(page));
  }

  // ---------- Drive times ----------

  function placeRow(p, i) {
    return `<div class="place">
      <input type="text" class="pname" value="${esc(p.name)}" placeholder="Name" aria-label="Place ${i + 1} name" maxlength="40">
      <input type="text" class="addr" value="${esc(p.address)}" placeholder="Street address" aria-label="Place ${i + 1} address">
      <select class="icon" aria-label="Place ${i + 1} icon">${Object.entries(ICONS).map(([k, v]) => `<option value="${k}" ${p.icon === k ? "selected" : ""}>${v}</option>`).join("")}</select>
      <button type="button" class="danger remove" aria-label="Remove place ${i + 1}">Remove</button>
    </div>`;
  }

  function pageDrive(page) {
    const d = settings.drive;
    const hours = d.active_hours || ["06:00", "22:00"];
    page.innerHTML = `
      <h1>Drive times</h1>
      <p class="lede">The TV keeps four drive times on screen. When the next thing on the calendar has an address, it also shows when to leave home and how traffic looks.</p>

      <form id="drive-form" novalidate>
        <h2>Home</h2>
        <div class="group">
          <div class="item"><label for="home">Home address</label><input id="home" type="text" value="${esc(settings.home.address)}" autocomplete="street-address"></div>
        </div>

        <h2>Always on the TV</h2>
        <div class="group" id="places">${d.places.map(placeRow).join("")}</div>
        <p class="hint"><button type="button" class="quiet" id="add-place">Add a place</button> Up to four fit. A name of a business works if it's the only one nearby; a street address is safer.</p>

        <h2>Leaving on time</h2>
        <div class="group">
          <div class="item"><label for="buffer">Extra minutes</label>
            <input id="buffer" type="number" min="0" max="60" value="${d.leave_buffer_minutes}">
            <span class="sub">Added to the drive when working out "Leave by", for getting shoes on and out the door.</span></div>
          <div class="item"><span class="label">Check traffic between</span>
            <div class="inline"><input id="ah-start" type="time" value="${esc(hours[0])}" aria-label="From" style="max-width:150px"> and <input id="ah-end" type="time" value="${esc(hours[1])}" aria-label="Until" style="max-width:150px"></div>
            <span class="sub">Outside these hours the TV keeps the last drive times, so Google isn't asked overnight.</span></div>
        </div>

        <h2>Google Maps key</h2>
        <div class="group">
          <div class="item"><label for="key">API key</label>
            <input id="key" type="password" autocomplete="off" placeholder="${esc(secretPlaceholder(d.api_key, "Paste your key"))}"></div>
          <div class="item"><label for="mlimit">Monthly limit</label>
            <input id="mlimit" type="number" min="0" max="100000" step="50" value="${d.monthly_limit == null ? 900 : d.monthly_limit}">
            <span class="sub">Most reads to Google Maps a month. They're spread evenly over the days left, so the drive times slow down instead of running out. ${esc(mapsUsage())}</span></div>
        </div>
        <details>
          <summary>How to get a key, and what it costs</summary>
          <ol>
            <li>Open <a href="https://console.cloud.google.com" target="_blank" rel="noopener">console.cloud.google.com</a> and create a project. Google asks for a billing account.</li>
            <li>Go to <b>APIs &amp; Services</b>, then <b>Library</b>, and turn on <b>Routes API</b>.</li>
            <li>Go to <b>Credentials</b>, create an <b>API key</b>, and restrict it to the Routes API.</li>
            <li>Paste it above and select <b>Test</b>.</li>
          </ol>
          <p>Each request to Google is one read here: one check of all the always-on places, or one leave-by route. With the monthly limit, the always-on drive times use up to 60% of each day's share, spaced evenly between the hours above, and leave-by times use the rest. At 900 a month that's about 30 a day: the four tiles refresh roughly every 50 minutes and leave-by times every 15 to 60. Google bills a route matrix per place, so 900 reads can be up to about 3,000 billed routes; compare that with Google's free amount, and set a budget alert in the Cloud console too.</p>
        </details>

        <div class="savebar"><button class="primary" type="submit">Save changes</button><button class="secondary" type="button" id="test-drive">Test</button><span class="msg" role="status"></span></div>
        <p class="test-result" id="drive-out"></p>
      </form>`;

    const list = $("#places");
    $("#add-place").onclick = () => {
      if ($$(".place", list).length >= 4) return toast("Four places fit on the TV. Remove one first.");
      list.insertAdjacentHTML("beforeend", placeRow({ name: "", address: "", icon: "pin" }, $$(".place", list).length));
    };
    list.addEventListener("click", (e) => { if (e.target.classList.contains("remove")) e.target.closest(".place").remove(); });

    const collect = () => {
      const out = {
        home: { address: $("#home").value.trim() },
        drive: {
          leave_buffer_minutes: Number($("#buffer").value),
          monthly_limit: Number($("#mlimit").value),
          active_hours: [$("#ah-start").value, $("#ah-end").value],
          places: $$(".place", list).map((r) => ({ name: $(".pname", r).value.trim(), address: $(".addr", r).value.trim(), icon: $(".icon", r).value }))
            .filter((p) => p.name || p.address),
        },
      };
      if ($("#key").value.trim()) out.drive.api_key = $("#key").value.trim();
      return out;
    };
    bindSave($("#drive-form"), collect, () => pageDrive(page));
    $("#test-drive").onclick = () => runTest("drive", collect(), $("#drive-out"));
  }

  // ---------- Stocks ----------

  function pageStocks(page) {
    const st = settings.stocks;
    page.innerHTML = `
      <h1>Stocks</h1>
      <p class="lede">A bar along the bottom of the TV that keeps sliding past the tickers you choose, with the latest price and today's change in green or red. Prices come from Yahoo Finance.</p>
      <form id="stk-form" novalidate>
        <div class="group">
          <div class="item"><label for="stk-on">Show the bar</label><input id="stk-on" type="checkbox" ${st.enabled ? "checked" : ""}></div>
          <div class="item"><label for="stk-syms">Tickers</label>
            <input id="stk-syms" type="text" autocomplete="off" autocapitalize="characters" value="${esc((st.symbols || []).join(", "))}" placeholder="AAPL, MSFT, ^GSPC, BTC-USD">
            <span class="sub">In the order they should scroll, up to 30. Add them from the lookup below, or type symbols separated by commas. Stocks, ETFs, mutual funds, indexes (^GSPC) and crypto (BTC-USD) all work.</span>
            <div class="chips" id="stk-chips" aria-label="Tickers on the bar"></div></div>
          <div class="item"><label for="stk-find">Find a company</label>
            <input id="stk-find" type="search" autocomplete="off" placeholder="Type a name, like Apple, Tesla or Vanguard">
            <span class="sub" id="stk-find-note">Pick a result to add its symbol to the bar.</span>
            <div class="results" id="stk-results" role="list"></div></div>
          <div class="item expert-only"><label for="stk-every">Update every</label>
            <div class="inline"><input id="stk-every" type="number" min="15" max="600" value="${st.refresh_seconds || 30}" style="max-width:110px"> seconds</div>
            <span class="sub">While US markets are open (4 AM to 8 PM Eastern on weekdays, including pre- and after-hours). Overnight and at weekends, every 15 minutes. Every ticker is a separate request to Yahoo, so with many tickers it slows down a little to be gentle.</span></div>
        </div>
        <details class="expert-only">
          <summary>About these prices</summary>
          <p>They come from Yahoo Finance through the community <code>yfinance</code> package. It needs no account or key. It isn't an official service, so prices can be delayed (up to about 15 minutes on some exchanges), and Yahoo can change or limit it without notice. If it ever stops working, the bar keeps the last prices and says how old they are. Treat the bar as a glance, not as something to trade on.</p>
          <p>Symbols follow Yahoo's style: <code>BRK-B</code> (with a dash) for share classes, <code>SAP.DE</code> for other countries' exchanges, <code>^GSPC</code> for the S&amp;P 500, <code>BTC-USD</code> for Bitcoin. Typing <code>BRK.B</code> or <code>NASDAQ</code> is turned into the right symbol for you.</p>
        </details>
        <div class="savebar"><button class="primary" type="submit">Save changes</button><button class="secondary expert-only" type="button" id="test-stk">Test</button><span class="msg" role="status"></span></div>
        <p class="test-result" id="stk-out"></p>
      </form>`;
    const collect = () => {
      return { stocks: { enabled: $("#stk-on").checked, symbols: $("#stk-syms").value, refresh_seconds: Number($("#stk-every").value) } };
    };
    bindSave($("#stk-form"), collect, () => pageStocks(page));
    $("#test-stk").onclick = () => runTest("stocks", collect(), $("#stk-out"));

    // Ticker chips: the text box is the source of truth, the chips are just a friendlier view of it
    const syms = () => [...new Set($("#stk-syms").value.toUpperCase().split(/[\s,]+/).map((x) => x.replace(/^\$/, "")).filter(Boolean))];   // as typed: the server tidies them on save
    const setSyms = (list) => { $("#stk-syms").value = list.join(", "); drawChips(); };
    function drawChips() {
      const list = syms();
      $("#stk-chips").innerHTML = list.length
        ? list.map((x) => `<span class="chip">${esc(x)}<button type="button" class="x" data-sym="${esc(x)}" aria-label="Remove ${esc(x)}">&times;</button></span>`).join("")
        : `<span class="sub" style="grid-column:auto">No tickers yet.</span>`;
    }
    $("#stk-chips").onclick = (e) => { const s = e.target.dataset && e.target.dataset.sym; if (s) setSyms(syms().filter((x) => x !== s)); };
    $("#stk-syms").addEventListener("input", drawChips);
    drawChips();

    // Company lookup, through Yahoo's search (debounced)
    let timer = 0, ticket = 0;
    $("#stk-find").addEventListener("input", () => {
      clearTimeout(timer);
      const q = $("#stk-find").value.trim(), box = $("#stk-results");
      if (q.length < 2) { box.innerHTML = ""; return; }
      box.innerHTML = `<p class="sub" style="grid-column:auto">Searching…</p>`;
      timer = setTimeout(async () => {
        const mine = ++ticket;
        try {
          const { results } = await api(`stocks/search?q=${encodeURIComponent(q)}`);
          if (mine !== ticket) return;
          const have = new Set(syms());
          box.innerHTML = results.length ? results.map((r) => `
            <div class="res" role="listitem">
              <div><b>${esc(r.symbol)}</b> <span>${esc(r.name)}</span> <small>${esc(r.type)}</small></div>
              <button type="button" class="secondary" data-add="${esc(r.symbol)}" ${have.has(r.symbol) ? "disabled" : ""}>${have.has(r.symbol) ? "On the bar" : "Add"}</button>
            </div>`).join("") : `<p class="sub" style="grid-column:auto">Nothing found. Try the company's full name.</p>`;
        } catch (err) {
          if (mine === ticket) box.innerHTML = `<p class="state bad" style="grid-column:auto">${esc(err.message)}</p>`;
        }
      }, 350);
    });
    $("#stk-results").onclick = (e) => {
      const s = e.target.dataset && e.target.dataset.add;
      if (!s) return;
      setSyms([...syms(), s]);
      e.target.textContent = "On the bar"; e.target.disabled = true;
    };
  }

  // ---------- Weather ----------

  function pageWeather(page) {
    const t = settings.tempest;
    const opt = (v, cur, label) => `<option value="${v}" ${cur === v ? "selected" : ""}>${label}</option>`;
    page.innerHTML = `
      <h1>Weather</h1>
      <p class="lede">Current conditions come straight from your Tempest station. The forecast is Tempest's forecast for your backyard.</p>
      <form id="wx-form" novalidate>
        <div class="group">
          <div class="item"><label for="sid">Station ID</label><input id="sid" type="text" inputmode="numeric" value="${esc(t.station_id)}" placeholder="12345"></div>
          <div class="item"><label for="tok">Access token</label><input id="tok" type="password" autocomplete="off" placeholder="${esc(secretPlaceholder(t.token, "Paste your token"))}"></div>
          <div class="item"><label for="sname">Name on the TV</label><input id="sname" type="text" value="${esc(t.station_name)}" maxlength="60"></div>
          <div class="item"><label for="ut">Temperature</label><select id="ut">${opt("f", t.units.temp, "Fahrenheit")}${opt("c", t.units.temp, "Celsius")}</select></div>
          <div class="item"><label for="uw">Wind</label><select id="uw">${opt("mph", t.units.wind, "mph")}${opt("kph", t.units.wind, "km/h")}${opt("mps", t.units.wind, "m/s")}${opt("kts", t.units.wind, "knots")}</select></div>
          <div class="item"><label for="ur">Rain</label><select id="ur">${opt("in", t.units.precip, "Inches")}${opt("mm", t.units.precip, "Millimetres")}</select></div>
        </div>
        <details>
          <summary>Where to find these</summary>
          <ol>
            <li>Sign in at <a href="https://tempestwx.com" target="_blank" rel="noopener">tempestwx.com</a>.</li>
            <li>Open <b>Settings</b>, then <b>Data Authorizations</b>, and create a token. Name it "Family Dashboard" so you know what it's for later.</li>
            <li>The station ID is the number at the end of your station's web address, like <code>tempestwx.com/station/12345</code>.</li>
          </ol>
        </details>
        <div class="savebar"><button class="primary" type="submit">Save changes</button><button class="secondary" type="button" id="test-wx">Test</button><span class="msg" role="status"></span></div>
        <p class="test-result" id="wx-out"></p>
      </form>`;
    const collect = () => {
      const out = { tempest: { station_id: $("#sid").value.trim(), station_name: $("#sname").value.trim(), units: { temp: $("#ut").value, wind: $("#uw").value, precip: $("#ur").value } } };
      if ($("#tok").value.trim()) out.tempest.token = $("#tok").value.trim();
      return out;
    };
    bindSave($("#wx-form"), collect, () => pageWeather(page));
    $("#test-wx").onclick = () => runTest("weather", collect(), $("#wx-out"));
  }

  // ---------- Account ----------

  function pageAccount(page) {
    page.innerHTML = `
      <h1>Account</h1>
      <p class="lede">One password protects these settings.</p>
      <form id="pw-form">
        <div class="group">
          <div class="item"><label for="cur">Current password</label><input id="cur" type="password" autocomplete="current-password" required></div>
          <div class="item"><label for="new">New password</label><input id="new" type="password" autocomplete="new-password" minlength="8" required><span class="sub">At least 8 characters.</span></div>
        </div>
        <div class="savebar"><button class="primary" type="submit">Change password</button><button class="secondary" type="button" id="out">Sign out</button><span class="msg" role="status"></span></div>
      </form>`;
    $("#pw-form").onsubmit = async (e) => {
      e.preventDefault();
      const msg = $(".msg", page);
      try {
        await api("password", { current: $("#cur").value, new: $("#new").value });
        $("#pw-form").reset();
        msg.className = "msg";
        toast("Password changed.");
      } catch (err) {
        msg.className = "msg bad";
        msg.textContent = err.message;
      }
    };
    $("#out").onclick = async () => { await api("logout", {}).catch(() => {}); showGate(); };
  }

  // ---------- Boot ----------

  Object.defineProperties(FD, {
    settings: { get: () => settings, set: (v) => { settings = v; } },
    status: { get: () => status, set: (v) => { status = v; } },
  });
  Object.assign(FD, { $, $$, esc, api, toast, keyDialog, ago, bindSave, saveBar, runTest, secretPlaceholder, rerender: () => route(), isExpert });


  api("session").then((s) => (s.signedIn ? start() : showGate())).catch(() => showGate());
})();
