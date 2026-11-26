/* Help: what to do when something goes wrong, in plain steps. Every scenario has a short "do this first". */
(function () {
  "use strict";
  const FD = (window.FD = window.FD || { pages: {} });

  const SCENARIOS = [
    {
      group: "The TV (the Raspberry Pi)",
      items: [
        {
          title: "The TV shows a dinosaur or \"No internet\"",
          first: "Unplug the Pi's power for 10 seconds and plug it back in.",
          body: `<p>That page is the browser saying it couldn't reach the dashboard server when it started. It happens when the power comes back before the router does: the Pi wakes up first, finds no network and the browser gives up. It stays on that page until something reopens it.</p>
            <ol>
              <li>Check that the router is on and other devices are online.</li>
              <li>Unplug the Pi for 10 seconds and plug it in. It reconnects and shows the dashboard within a minute.</li>
              <li>If it comes back to the same page, the Pi isn't joining the Wi-Fi. See <b>The Pi can't join the Wi-Fi</b> below.</li>
            </ol>
            <p><b>Prevent it:</b> the newest TV software handles this by itself (it keeps retrying the Wi-Fi, reopens the dashboard when the server is back, and restarts the Pi as a last resort). See <b>Update the TV software</b> below if your TV still shows the old behaviour.</p>`,
        },
        {
          title: "The TV is black",
          first: "Check <a href=\"#schedule\">Schedule</a>: is it supposed to be asleep?",
          body: `<ol>
              <li>A schedule or a manual <i>Sleep the screen</i> may be running. Choose <b>Wake</b> on the Schedule page.</li>
              <li>Check the TV's input is the one the Pi is plugged into, and that it's on.</li>
              <li>Press the TV remote's power button once. Some TVs turn off when the Pi goes to sleep.</li>
              <li>If the Pi's green light isn't blinking at all, it has no power: check its cable and plug.</li>
              <li>Unplug the Pi for 10 seconds and plug it back in.</li>
            </ol>`,
        },
        {
          title: "The TV says \"Waiting for the family dashboard server\"",
          first: "Check that the dashboard server is running (the next section).",
          body: `<p>The Pi is on the network but the dashboard server isn't answering. The TV will show the dashboard by itself as soon as the server is back; you don't need to touch the Pi.</p>
            <ol>
              <li>Open this settings page from another device. If it loads, the server is fine, and the Pi may have the wrong address or a stopped TV key (see <b>A screen stopped working after I made a new key</b>).</li>
              <li>If it doesn't load, see <b>The dashboard server is down</b>.</li>
            </ol>`,
        },
        {
          title: "The Pi can't join the Wi-Fi",
          first: "Restart the router, wait 3 minutes, then unplug the Pi for 10 seconds.",
          body: `<ol>
              <li>Confirm the router's Wi-Fi works for your phone.</li>
              <li>Restart the Pi after the router is fully back. A Pi that was on during an outage takes up to 20 minutes to restart itself, if it still can't find the router.</li>
              <li>If you changed the Wi-Fi name or password, the Pi needs the new one. Connect a keyboard to the Pi, sign in and run <code>sudo nmtui</code>, choose <i>Activate a connection</i> or <i>Edit a connection</i>, then restart.</li>
              <li>An Ethernet cable from the router to the Pi is the most reliable fix. It never needs a Wi-Fi password.</li>
            </ol>`,
        },
        {
          title: "Update the TV software",
          first: "On the Pi, run the update command below, then restart it.",
          body: `<p>The TV software lives on the Pi, separate from the server. To pick up improvements (for example the outage handling), sign in to the Pi (SSH, or a keyboard on it) and run:</p>
            <pre><code>cd ~/kliercalendar && git pull && sudo ./pi/install-display.sh && sudo reboot</code></pre>
            <p>It keeps the server address and TV key the Pi already has. If it says it needs them, run it the way <a href="#tv">On the TV → More screens</a> shows when you make a key.</p>
            <p>To see what the TV is doing: <code>journalctl -u family-tv -b</code>. To see what the network watchdog did: <code>journalctl -t family-tv-net</code>.</p>`,
        },
        {
          title: "The time on the TV is wrong after an outage",
          first: "Wait a minute. The TV takes the time from the server.",
          body: `<p>A Pi has no clock that keeps time while it's unplugged, and it normally learns the time from the internet. After a power cut with no internet it would think it's still the moment it was switched off. The dashboard page asks the server for the time instead, and corrects itself within a minute of starting.</p>
            <p>If it's still wrong after that, the <i>server's</i> time is wrong. Check the server's clock and time zone in <a href="#about">How it works</a>, and the household time zone in <a href="#schedule">Schedule</a>.</p>`,
        },
      ],
    },
    {
      group: "Power and internet outages",
      items: [
        {
          title: "What happens when the power goes out",
          first: "Nothing needs doing: everything starts again by itself in the right order.",
          body: `<ul>
              <li><b>The server</b> starts again when its computer does. Its settings and data are saved on disk, so nothing is lost.</li>
              <li><b>The TV (the Pi)</b> starts, waits for the network, then shows the dashboard.</li>
              <li><b>If the router comes back later than the Pi,</b> the Pi keeps trying and reconnects by itself. If it can't reach the router for 20 minutes it restarts, at most once an hour.</li>
              <li><b>Cameras and Home Assistant</b> reconnect on their own the next time the TV asks.</li>
            </ul>
            <p>A <b>small battery backup</b> (UPS) for the router and the Pi makes the whole house ride through short outages without a blip.</p>`,
        },
        {
          title: "What happens when only the internet goes out",
          first: "Nothing. The TV keeps working, with the last known information.",
          body: `<ul>
              <li>The TV only needs the <i>home network</i> and the dashboard server, not the internet. It keeps showing what it has.</li>
              <li>Calendar, weather, drive times and stocks stop updating. Each shows a small "offline since" note.</li>
              <li>Cameras and Home Assistant keep working if they are in the house.</li>
              <li>When the internet returns, everything refreshes within a few minutes with no action from you.</li>
            </ul>`,
        },
      ],
    },
    {
      group: "The dashboard server",
      items: [
        {
          title: "The dashboard server is down",
          first: "Open Portainer and check the <code>family-dashboard</code> stack is running.",
          body: `<ol>
              <li>Open Portainer, go to <b>Stacks → family-dashboard</b> and check its container is <i>running</i> and <i>healthy</i>. If it isn't, start it.</li>
              <li>If the whole machine is off, start it. The stack comes back by itself when the machine does.</li>
              <li>If it keeps stopping, look at the container's <b>Logs</b> in Portainer. A message about the settings file means that file is damaged: restore the data folder from a backup (see <b>Restore from a backup</b>).</li>
            </ol>`,
        },
        {
          title: "I updated the server and something broke",
          first: "Roll back to the previous version.",
          body: `<p>Every update keeps the version before it. On the server's computer:</p>
            <pre><code>docker tag family-dashboard:previous family-dashboard:latest</code></pre>
            <p>Then, in Portainer, open the stack and press <b>Update the stack</b> with <b>Re-pull image</b> turned <b>off</b> (that image only exists on the server, so pulling it fails). Your settings are untouched either way.</p>`,
        },
        {
          title: "The server won't update in Portainer (\"pull access denied\")",
          first: "Turn off <b>Re-pull image and redeploy</b>.",
          body: `<p>The <code>family-dashboard</code> image is built on the server, not downloaded. With the re-pull option on, Portainer looks for it on Docker Hub and fails. With it off, Portainer uses the image already there and recreates the container.</p>`,
        },
        {
          title: "Restore from a backup",
          first: "Stop the stack, put the backup's files in the data folder, start it.",
          body: `<p>The <a href="#backup">Backup</a> page downloads a file with everything: settings, photos and usage counters. To restore it:</p>
            <ol>
              <li>In Portainer, stop the <code>family-dashboard</code> stack.</li>
              <li>Unpack the backup into the stack's data folder, replacing what's there.</li>
              <li>Start the stack. Everyone must sign in to Settings again.</li>
            </ol>
            <p>Make a backup now, and after big changes. A copy kept somewhere other than the server is the one that saves you.</p>`,
        },
        {
          title: "I forgot the settings password",
          first: "Reset it from the server's computer.",
          body: `<p>The password is stored only as a fingerprint, so it can't be shown, but it can be reset. On the server's computer, stop the stack, open the settings file in the data folder and delete the <code>"password_hash"</code> value (make it empty, <code>""</code>), then start the stack. The settings page then asks you to choose a new password. Everything else stays.</p>`,
        },
      ],
    },
    {
      group: "More screens, cameras and sources",
      items: [
        {
          title: "A screen stopped working after I made or stopped a key",
          first: "Make a fresh key for that screen under On the TV.",
          body: `<p>Each screen has its own key. <b>Stop this key</b> signs that one screen out and no others. To bring it back, open <a href="#tv">On the TV</a> (Expert settings on), <b>Make a TV key</b> for it, and open the address the pop-up gives you on that screen. Keys are shown once, so copy them from the pop-up before pressing Done.</p>`,
        },
        {
          title: "The Fire TV goes to sleep or shows the browser bars",
          first: "Set the Fire TV's screensaver to Never.",
          body: `<ol>
              <li>On the Fire TV: Settings → Display &amp; Sounds → Screen Saver → Start Time → <b>Never</b>.</li>
              <li>Open the dashboard address in the browser and choose its full-screen option so the address bar goes away.</li>
              <li>A kiosk browser app (such as Fully Kiosk) keeps the screen awake and hides everything else, if you can install one.</li>
            </ol>`,
        },
        {
          title: "A camera is blank or frozen",
          first: "Open the camera in Home Assistant. If it's blank there too, the camera is the problem.",
          body: `<ol>
              <li>Check <a href="#smarthome">Smart home</a> shows Home Assistant as connected.</li>
              <li>If the camera works in Home Assistant, check it's still in the camera list on the Smart home page.</li>
              <li>If Home Assistant's own access token expired or you changed it, make a new one and paste it on the Smart home page.</li>
              <li>Pictures are fetched once a couple of seconds, so a very weak Wi-Fi shows a frozen picture. An Ethernet cable to the Pi or the Fire TV helps.</li>
            </ol>`,
        },
        {
          title: "A calendar, the weather, drive times or stocks are missing or old",
          first: "Open On the TV and read the line under <b>Connections</b>: it names the problem.",
          body: `<ul>
              <li><b>Calendar missing:</b> <a href="#calendars">Calendars</a> → <b>Refresh calendars</b>. If Google refuses, the app password needs to be made again.</li>
              <li><b>Weather:</b> check the station token on the Weather page.</li>
              <li><b>Drive times:</b> the monthly limit may be used up; it paces itself over the month.</li>
              <li><b>Stocks:</b> the price service can be late or briefly unavailable. The bar keeps the last prices and says how old they are. A mistyped symbol (for example QQQQ instead of QQQ) shows nothing.</li>
              <li>All of these recover by themselves when the internet is back.</li>
            </ul>`,
        },
        {
          title: "The text looks wrong on one screen",
          first: "Open Layout, pick that screen and choose a ready-made arrangement.",
          body: `<p>Each screen can have its own layout. Choose the screen under <b>Editing the layout for</b>, then use <b>Start from a ready-made arrangement</b>. To make it follow the shared layout again, press <b>Use the shared layout again</b>.</p>`,
        },
      ],
    },
  ];

  FD.pages.help = function (page) {
    const sec = ({ group, items }) => `<h2>${group}</h2>
      <div class="group helplist">${items.map((it) => `<details class="help"><summary><span>${it.title}</span><small>Do this first: ${it.first}</small></summary><div class="helpbody">${it.body}</div></details>`).join("")}</div>`;
    page.innerHTML = `<div class="prose">
      <h1>Help</h1>
      <p class="lede">What to do when something goes wrong. Open a heading for the steps. If you're not sure where to start, the answer is almost always: <b>unplug the Pi for 10 seconds and plug it back in</b>.</p>
      ${SCENARIOS.map(sec).join("")}
      <p class="hint" style="margin-left:0">Something not covered here? <a href="#about">How it works</a> explains how the pieces fit together.</p>
    </div>`;
  };
})();
