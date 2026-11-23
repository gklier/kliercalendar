/* Backup and How it works: the two pages for whoever looks after the system. */
(function () {
  "use strict";
  const FD = (window.FD = window.FD || { pages: {} });
  const REPO = "https://github.com/gklier/kliercalendar";

  const upTime = (s) => {
    const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60);
    return d ? `${d} d ${h} h` : h ? `${h} h ${m} min` : `${m} min`;
  };

  // ---------------------------------------------------------------- Backup and recovery

  FD.pages.backup = function (page) {
    const { $, esc, api, toast } = FD;
    const info = FD.status.info || {};
    page.innerHTML = `
      <h1>Backup &amp; recovery</h1>
      <p class="lede">What to keep, and how to get the TV back if something breaks. There are two machines, and only one of them holds anything that can't be replaced.</p>

      <h2>What lives where</h2>
      <div class="group"><table class="tbl">
        <thead><tr><th>Machine</th><th>Holds</th><th>If it's lost</th></tr></thead>
        <tbody>
          <tr><td><b>The dashboard server</b></td><td>Every setting, the Google, weather, maps and stock keys, the settings password, uploaded photos and the Maps usage counter, all in one data folder (<code>${esc(info.dataDir || "/data")}</code> on the server).</td><td><b>Back this up.</b> It can't be recreated.</td></tr>
          <tr><td><b>The TV (Raspberry Pi)</b></td><td>Only the program that shows the page, plus a small launcher file with the address of the server and its TV key.</td><td>Reinstall in about ten minutes. Nothing is lost.</td></tr>
        </tbody></table></div>

      <h2>1. Back up the server's data</h2>
      <div class="group"><div class="item" style="grid-template-columns:1fr">
        <p style="margin:0">Download everything in the data folder as one file. It includes every key, so keep it somewhere private.</p>
        <div class="inline"><a class="secondary filebtn" href="api/backup" download>Download a backup now</a></div>
        <span class="sub" style="grid-column:1">Up to 200 MB. For a larger folder (lots of photos), back the folder up from the machine itself.</span>
      </div></div>
      <details open>
        <summary>Automatic backups, and restoring one</summary>
        <p>On the machine that runs the server, a nightly job can copy the data folder. Adjust the path to wherever it is mounted:</p>
<pre><code>tar -C /path/to/data/.. -czf family-dashboard-$(date +%F).tar.gz data</code></pre>
        <p>To restore: stop the container, unpack the archive so the folder is back in place, give it to user 1000 (the user inside the container) and start the container again:</p>
<pre><code>tar -C /path/to/data/.. -xzf family-dashboard-backup.tar.gz
sudo chown -R 1000:1000 /path/to/data</code></pre>
        <p>The settings password and keys come back with the data, so nothing needs setting up again.</p>
      </details>

      <h2>2. If the TV's SD card dies</h2>
      <p class="hint" style="margin-left:0">The TV keeps nothing the server doesn't already know, so a new card is a fresh install and one command.</p>
      <ol class="steps">
        <li><b>Write the system.</b> In Raspberry Pi Imager choose <i>Raspberry Pi OS Lite (64-bit)</i>. In the settings, give it a hostname, a user and password, the Wi-Fi name and password (with the right country), and turn on SSH.</li>
        <li><b>Get the project onto it</b> once it has started:
<pre><code>git clone ${esc(REPO)}.git
cd kliercalendar</code></pre></li>
        <li><b>Make a TV key</b> (below) and run the installer with it. It sets up the full-screen browser, the 1080p video mode and the sleep behaviour:
<pre><code>sudo DASHBOARD_SERVER=${esc(location.origin)} DASHBOARD_TV_KEY=&lt;the key&gt; ./pi/install-display.sh
sudo reboot</code></pre></li>
      </ol>
      <div class="group"><div class="item" style="grid-template-columns:1fr">
        <div class="inline"><button class="secondary" type="button" id="mk-key">Make a TV key</button>
          <span class="state">${FD.settings.tv.key.set ? "Keys exist. Making another one doesn't stop the others." : "No key yet."}</span></div>
        <div id="key-out"></div>
        <span class="sub" style="grid-column:1">The key is shown once and only a fingerprint of it is stored.</span>
      </div></div>

      <h2>3. Optional: an exact copy of the card</h2>
      <p>If you'd rather swap in a card that's already set up (no reinstalling at all), keep an image of it. This is only a convenience, because step 2 rebuilds the same thing. Do it after the TV is set up, and again after big changes. On a Mac, with the card in a reader:</p>
<pre><code>./tools/backup-sd-card.sh</code></pre>
      <p>The script lists the removable disks, asks you to confirm which one is the card, and writes a compressed image file. It only reads from the card. To use the image, open Raspberry Pi Imager, choose <i>Use custom</i>, pick the file and write it to a new card. An image taken while the Pi was off is the safest; shut it down and move the card to the reader first.</p>
      <p class="hint" style="margin-left:0">A spare card holding such an image, kept in a drawer, is the fastest recovery there is.</p>`;

    $("#mk-key").onclick = async () => {
      try {
        const res = await api("tv/key", {});
        FD.settings = res.settings;
        $("#key-out").innerHTML = `<p class="sub" style="margin-top:10px">Copy it now; it won't be shown again:</p>
<pre><code>sudo DASHBOARD_SERVER=${esc(location.origin)} DASHBOARD_TV_KEY=${esc(res.key)} ./pi/install-display.sh</code></pre>
          <p class="sub">If this page was opened by a name or address the TV can't reach, use the server's own address instead.</p>`;
      } catch (err) { toast(err.message); }
    };
  };

  // ---------------------------------------------------------------- How it works

  FD.pages.about = function (page) {
    const { esc } = FD;
    const i = FD.status.info || {};
    const row = (k, v) => `<tr><th scope="row">${k}</th><td>${v}</td></tr>`;
    page.innerHTML = `<div class="prose">
      <h1>How it works</h1>
      <p class="lede">A technical overview for whoever looks after it: what runs where, how data and credentials move, how it fails and how to operate it.</p>

      <h2>This installation</h2>
      <div class="group"><table class="tbl kv"><tbody>
        ${row("Version", esc(i.version || "?"))}
        ${row("Runtime", `Python ${esc(i.python || "?")} on <code>${esc(i.host || "?")}</code>`)}
        ${row("Mode", i.offsite ? "<b>Separate server.</b> Requests from the same machine are not trusted; the TV signs in with its TV key." : "<b>On the TV's own computer.</b> The TV's browser is trusted by being on the same machine.")}
        ${row("Listening on", `TCP ${esc(i.port || 8080)} (plain HTTP)`)}
        ${row("Data folder", `<code>${esc(i.dataDir || "?")}</code>`)}
        ${row("Time zone", esc(i.timezone || "?"))}
        ${row("Running for", upTime(i.uptime || 0))}
      </tbody></table></div>

      <h2>The pieces</h2>
      <figure class="diagram">${diagram()}<figcaption>Solid arrows: the server pulls from outside services over HTTPS. Dashed arrows: the TV and browsers talk to the server over plain HTTP on the home network.</figcaption></figure>
      <div class="group"><dl>
        <dt>Dashboard server</dt>
        <dd>One Python process using only the standard library's HTTP server, plus two small packages for reading calendars (<code>icalendar</code>, <code>recurring-ical-events</code>). It keeps every key private, fetches and caches the data, serves the TV page and this settings site, and answers a few JSON endpoints. It normally runs in a container limited to 200 MB, as an unprivileged user, with no database: settings are one JSON file.</dd>
        <dt>The TV</dt>
        <dd>A Raspberry Pi 4 running Raspberry Pi OS Lite. A systemd service starts <i>cage</i> (a single-window Wayland compositor) and Chromium in kiosk mode, started by a launcher script. The launcher asks the server every 5 seconds which page to show and whether the screen should be asleep, and restarts the browser or switches the screen to match. If the server can't be reached it shows a waiting page and keeps trying.</dd>
        <dt>Settings (this site)</dt>
        <dd>Served by the same process under <code>/admin/</code>. It never holds secrets: the server returns only "set or not" for every key and password.</dd>
      </dl></div>

      <h2>Where the data comes from</h2>
      <div class="group"><table class="tbl">
        <thead><tr><th>Data</th><th>Source</th><th>Credential</th><th>Refresh</th></tr></thead>
        <tbody>
          <tr><td>Calendars</td><td>Google Calendar over CalDAV, plus any secret iCal links</td><td>A Google app password (a full-account secret: it can reach mail too)</td><td>Server every 10 min; a failing calendar doesn't hide the others</td></tr>
          <tr><td>Weather</td><td>Tempest weather station (WeatherFlow)</td><td>Station token</td><td>Every 2 min</td></tr>
          <tr><td>Drive times, leave-by</td><td>Google Maps Routes API (route matrix and directions)</td><td>API key restricted to Routes</td><td>Paced by a monthly limit (900 reads by default), spread evenly over the days left</td></tr>
          <tr><td>Stock prices, search</td><td>Yahoo Finance, through the <code>yfinance</code> package (unofficial; may be delayed)</td><td>None</td><td>30 s while US markets are open, else 15 min; one request per ticker, so it slows down with many</td></tr>
          <tr><td>Sunrise, sunset</td><td>The weather forecast</td><td>none</td><td>With the weather</td></tr>
        </tbody></table></div>
      <p class="hint" style="margin-left:0">Every source is cached with "stale on error": if an outside service fails, the last good data stays on the TV with a small "offline since" note, and the server backs off for a minute before trying again.</p>

      <h2>Security model</h2>
      <div class="group"><dl>
        <dt>Settings sign-in</dt>
        <dd>One password, stored only as a salted PBKDF2-HMAC-SHA256 hash (200,000 rounds). A session is an <code>HttpOnly</code>, <code>SameSite=Strict</code> cookie that lasts 30 days and lives in memory, so restarting the server signs everyone out. Five wrong passwords from one address lock sign-in for five minutes.</dd>
        <dt>Cross-site requests</dt>
        <dd>Every change needs a custom header (<code>X-Dashboard: 1</code>) that a web page on another site can't send without a pre-flight request the server never allows.</dd>
        <dt>Who can read the data</dt>
        <dd>The JSON data (<code>/api/*</code>, photos) is open to: the same machine (only when the server runs on the TV's own computer), a signed-in settings session, a TV with the <b>TV key</b>, or anyone on the network if "Others can view" is switched on. The TV key is 32 random characters, stored only as a SHA-256 fingerprint and shown once. Each screen has its own key, and one can be stopped without touching the others.</dd>
        <dt>Secrets</dt>
        <dd>Never sent back to a browser, never logged. The settings file is readable only by its owner (mode 600). The container runs as user 1000.</dd>
        <dt>Transport</dt>
        <dd>Plain HTTP, meant for a trusted home network. To add HTTPS, put any reverse proxy in front: cookies then become <code>Secure</code> automatically when the proxy sends <code>X-Forwarded-Proto: https</code>, and the client address is taken from <code>X-Forwarded-For</code> only when it arrives from the same machine or a Docker network.</dd>
        <dt>What to restrict</dt>
        <dd>Keep port ${esc(i.port || 8080)} reachable only from the TV and trusted devices (a VLAN or firewall rule). Use a dedicated Google account, or share calendars read-only with one, and revoke the app password at <a href="https://myaccount.google.com/apppasswords">myaccount.google.com/apppasswords</a> to cut the dashboard off at once.</dd>
      </dl></div>

      <h2>How the TV is driven</h2>
      <div class="group"><dl>
        <dt>Which page</dt>
        <dd><code>GET /api/screen-url</code> returns three lines: the page to show, a counter that "Reload the TV" bumps, and <code>asleep</code> or <code>awake</code>. The launcher polls it every 5 seconds and restarts the browser when the first two change.</dd>
        <dt>Screen sleep</dt>
        <dd>The Schedule page's rules are evaluated on the server in the household time zone. The launcher turns the HDMI output off or on (<code>wlr-randr</code>) and sends HDMI-CEC standby or wake (<code>cec-ctl</code>) when the answer changes, so schedule edits take effect within seconds with no visit to the Pi.</dd>
        <dt>Settings changes</dt>
        <dd>Saving a look or layout change bumps a version number. The page checks it every 30 seconds and reloads itself. Sleep schedule changes don't bump it, so they don't reload the page. The page also reloads itself daily at 03:30.</dd>
        <dt>Rendering</dt>
        <dd>The page is drawn at a fixed 1920 × 1080 and scaled to the screen. On a Pi 4 the video output is best at 1080p; 4K cuts the animation rate sharply. The page measures its own frame rate and reports it to the server, and switches off the frosted-glass blur by itself if it can't keep up.</dd>
      </dl></div>

      <h2>Operating it</h2>
      <div class="group"><table class="tbl kv"><tbody>
        ${row("Health", "<code>GET /healthz</code> (needs a settings session or the TV key) reports each source's last success and error, the Maps counter and the TV's last frame rate. The container's own health check calls <code>/admin/api/session</code> every minute.")}
        ${row("Logs", "The server writes to standard output (the container's log). On the TV: <code>journalctl -u family-tv -b</code>.")}
        ${row("Restart the TV", "<code>sudo systemctl restart family-tv</code> on the Pi, or <b>Reload the TV</b> on the first settings page.")}
        ${row("Update", `Rebuild the image from the repository (<a href="${esc(REPO)}">${esc(REPO.replace("https://", ""))}</a>) and recreate the container. The data folder is untouched. In Portainer, deploy the stack from the repository and use "Pull and redeploy".`)}
        ${row("Back up", "The server's data folder. See <a href=\"#backup\">Backup</a>.")}
        ${row("Network", "Inbound: TCP " + esc(i.port || 8080) + " from the TV and browsers. Outbound HTTPS from the server to Google (Calendar and Maps), WeatherFlow and Yahoo Finance. The TV needs only the server.")}
      </tbody></table></div>

      <h2>When something looks wrong</h2>
      <div class="group"><dl>
        <dt>A calendar is missing</dt><dd>Calendars → <b>Refresh calendars</b>. Google only offers a calendar to apps when it's ticked for syncing at <a href="https://calendar.google.com/calendar/syncselect">calendar.google.com/calendar/syncselect</a>. If Google refuses the app password (it does after the account's main password changes), make a new one.</dd>
        <dt>"Offline since…" on the TV</dt><dd>The TV keeps the last good information. Open <b>On the TV → Connections</b> for the error each source reports.</dd>
        <dt>The TV shows "Waiting for the family dashboard server"</dt><dd>The launcher can't reach the server. Check that the server is running and that the address and TV key it was installed with are still right. Until it can, the TV shows a waiting page.</dd>
        <dt>The TV is blank at the wrong time</dt><dd>Check <b>Schedule</b>, including a manual "Sleep the screen" that is still running.</dd>
        <dt>Drive times are stale or missing</dt><dd>The monthly Maps limit may be used up (shown under Drive times and On the TV); it paces itself, so a busy morning can use that day's share.</dd>
        <dt>The animation looks choppy</dt><dd>Confirm the video output is 1080p (the installer sets it) and that the page isn't in the 4K mode.</dd>
        <dt>Everything is wrong after a restart</dt><dd>Check that the data folder is mounted at the same place and owned by user 1000. An empty folder starts the server as a new install.</dd>
      </dl></div>
    </div>`;
  };

  // Plain SVG, themed with the page's own colours
  function diagram() {
    const box = (x, y, w, h, title, sub, cls = "") => `<g class="${cls}"><rect x="${x}" y="${y}" width="${w}" height="${h}" rx="12"/><text x="${x + w / 2}" y="${y + 24}" class="t">${title}</text>${(sub || []).map((s, k) => `<text x="${x + w / 2}" y="${y + 44 + k * 17}" class="s">${s}</text>`).join("")}</g>`;
    return `<svg viewBox="0 0 780 360" role="img" aria-label="Architecture: outside services feed the dashboard server, which serves the TV and the settings site." class="arch">
      <defs><marker id="ar" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0 0L10 5L0 10z" class="ah"/></marker></defs>
      ${box(10, 20, 190, 56, "Google Calendar", ["CalDAV, app password"])}
      ${box(10, 94, 190, 56, "Google Maps", ["Routes API, monthly limit"])}
      ${box(10, 168, 190, 56, "Tempest weather", ["WeatherFlow API"])}
      ${box(10, 242, 190, 56, "Yahoo Finance", ["quotes and search, no key"])}
      ${box(290, 60, 230, 190, "Dashboard server", ["one container, Python", "caches every source", "keeps all keys private", "/data: settings, photos,", "usage counters"], "core")}
      ${box(580, 30, 195, 96, "The TV", ["Raspberry Pi, cage + Chromium", "launcher polls the server"])}
      ${box(580, 170, 195, 70, "Browsers", ["settings at /admin/"])}
      <g class="ln"><path d="M200 48 L290 100"/><path d="M200 122 L290 130"/><path d="M200 196 L290 160"/><path d="M200 270 L290 200"/></g>
      <g class="ln dash"><path d="M580 78 L520 110"/><path d="M580 205 L520 190"/></g>
      <text x="105" y="326" class="s">Outbound HTTPS only</text>
      <text x="680" y="268" class="s">Plain HTTP, home network</text>
    </svg>`;
  }
})();
