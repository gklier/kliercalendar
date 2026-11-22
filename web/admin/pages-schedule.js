/* Schedule: when the TV's screen sleeps, by day of the week.
   The rules live on the dashboard server. The TV launcher asks the server every few seconds whether
   the screen should be asleep and switches the HDMI output (and the TV, over HDMI-CEC) to match, so
   nothing here needs a visit to the Pi. */
(function () {
  "use strict";
  const FD = (window.FD = window.FD || { pages: {} });
  const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];       // 0 = Monday, as the server counts
  const FULL = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];

  const mins = (hhmm) => { const [h, m] = hhmm.split(":").map(Number); return h * 60 + m; };
  const clock = (hhmm) => {
    if (FD.settings && FD.settings.display.timeFormat === "24h") return hhmm;
    const [h, m] = hhmm.split(":").map(Number);
    return `${h % 12 || 12}:${String(m).padStart(2, "0")} ${h < 12 ? "AM" : "PM"}`;
  };

  // The server writes times in its own zone (the household's); read them as written, not in this browser's zone
  function parts(iso) {
    const today = (FD.status.info && FD.status.info.now || iso).slice(0, 10);
    const date = iso.slice(0, 10), t = clock(iso.slice(11, 16));
    if (date === today) return { day: "", t };
    const next = new Date(today + "T12:00:00Z"); next.setUTCDate(next.getUTCDate() + 1);
    if (date === next.toISOString().slice(0, 10)) return { day: "tomorrow", t };
    return { day: new Date(date + "T12:00:00").toLocaleDateString([], { weekday: "long" }), t };
  }
  const at = (iso) => { const p = parts(iso); return `${p.day ? p.day + " " : ""}at ${p.t}`; };          // at 10:00 PM / tomorrow at 6:45 AM
  const until = (iso) => { const p = parts(iso); return p.day ? `${p.day} at ${p.t}` : p.t; };           // 6:45 AM / tomorrow at 6:45 AM

  // One sentence about the screen right now. The On the TV page shows it too.
  FD.sleepLine = function () {
    const sl = FD.status && FD.status.screen && FD.status.screen.sleep;
    if (!sl) return "";
    const link = ` <a href="#schedule">Change the schedule</a>`;
    if (sl.reason === "manual") {
      return sl.asleep ? `The screen is asleep because you put it to sleep. It wakes up ${at(sl.change)}.${link}`
                       : `The screen is being kept awake until ${until(sl.change)}.${link}`;
    }
    if (sl.asleep) return `The screen is asleep until ${until(sl.change)}.${link}`;
    return sl.change ? `The screen is on. It goes to sleep ${at(sl.change)}.${link}` : `The screen stays on. No sleep times are set.${link}`;
  };

  function summarize(r) {
    const d = r.days.slice().sort();
    const days = d.length === 7 ? "Every day"
      : d.join() === "0,1,2,3,4" ? "Weekdays" : d.join() === "5,6" ? "Weekends"
      : d.map((k) => DAYS[k]).join(", ");
    return `${days}: asleep from ${clock(r.off)} until ${clock(r.on)}`;
  }

  // The week at a glance: shaded where the screen is off
  function weekPicture(rules, enabled) {
    const seg = Array.from({ length: 7 }, () => []);
    for (const r of rules) {
      if (r.enabled === false || !r.days.length || r.off === r.on) continue;
      const off = mins(r.off), on = mins(r.on);
      for (const d of r.days) {
        if (on > off) seg[d].push([off, on]);
        else { seg[d].push([off, 1440]); seg[(d + 1) % 7].push([0, on]); }
      }
    }
    const rows = seg.map((list, d) => `
      <div class="wk-row"><span class="wk-day">${DAYS[d]}</span>
        <div class="wk-bar" role="img" aria-label="${FULL[d]}: ${list.length ? "asleep " + list.map(([a, b]) => `${clock(String(Math.floor(a / 60)).padStart(2, "0") + ":" + String(a % 60).padStart(2, "0"))} to ${b === 1440 ? "midnight" : clock(String(Math.floor(b / 60)).padStart(2, "0") + ":" + String(b % 60).padStart(2, "0"))}`).join(", ") : "awake all day"}">
          ${list.map(([a, b]) => `<i style="left:${(a / 14.4).toFixed(2)}%;width:${((b - a) / 14.4).toFixed(2)}%"></i>`).join("")}
        </div></div>`).join("");
    const ticks = [0, 6, 12, 18, 24].map((h) => `<span style="left:${(h / 24) * 100}%">${h === 0 || h === 24 ? "12a" : h === 12 ? "12p" : h < 12 ? h + "a" : h - 12 + "p"}</span>`).join("");
    return `<div class="week${enabled ? "" : " off"}">${rows}<div class="wk-row"><span class="wk-day"></span><div class="wk-ticks">${ticks}</div></div></div>
      <p class="hint" style="margin-left:0">Shaded means the screen is off.</p>`;
  }

  FD.pages.schedule = function (page) {
    const { $, $$, esc, api, toast } = FD;
    let enabled = FD.settings.schedule.enabled;
    let rules = FD.settings.schedule.rules.map((r) => ({ ...r }));

    function ruleCard(r, i) {
      return `<div class="rule" data-i="${i}">
        <div class="days" role="group" aria-label="Days for sleep time ${i + 1}">
          ${DAYS.map((d, k) => `<label class="daychip"><input type="checkbox" data-day="${k}" ${r.days.includes(k) ? "checked" : ""}><span>${d}</span></label>`).join("")}
        </div>
        <div class="inline">
          <label class="tfield">Sleeps at <input type="time" class="off" value="${esc(r.off)}"></label>
          <label class="tfield">Wakes at <input type="time" class="on" value="${esc(r.on)}"></label>
          <label class="show"><input type="checkbox" class="en" ${r.enabled !== false ? "checked" : ""}> In use</label>
          <button type="button" class="danger remove" aria-label="Remove sleep time ${i + 1}">Remove</button>
        </div>
        <p class="sum">${r.days.length ? esc(summarize(r)) : "Pick at least one day."}</p>
      </div>`;
    }

    function draw() {
      const st = FD.status.screen.sleep;
      const manual = st && st.reason === "manual";
      page.innerHTML = `
        <h1>Schedule</h1>
        <p class="lede">Choose when the TV screen goes to sleep. It switches off at the sleep time and back on at the wake time, on the days you pick. Changes reach the TV within a few seconds.</p>

        <h2>Right now</h2>
        <div class="group"><div class="item" style="grid-template-columns:1fr">
          <p id="now-line" style="margin:0">${FD.sleepLine()}</p>
          <div class="inline">
            <select id="ov-hours" aria-label="For how long" style="width:auto">
              ${[[0.5, "30 minutes"], [1, "1 hour"], [2, "2 hours"], [4, "4 hours"], [8, "8 hours"]].map(([v, l]) => `<option value="${v}" ${v === 2 ? "selected" : ""}>${l}</option>`).join("")}
            </select>
            <button class="secondary" id="ov-sleep" type="button">Sleep the screen</button>
            <button class="secondary" id="ov-wake" type="button">Keep it awake</button>
            ${manual ? `<button class="quiet" id="ov-clear" type="button">Back to the schedule</button>` : ""}
          </div>
          <span class="sub" style="grid-column:1" id="ov-msg" role="status"></span>
        </div></div>

        <form id="sch-form" novalidate>
          <h2>Sleep times</h2>
          <div class="group"><div class="item"><label for="sch-on">Use a schedule</label><input id="sch-on" type="checkbox" ${enabled ? "checked" : ""}>
            <span class="sub">Turn this off and the screen stays on all the time.</span></div></div>
          <div id="rules">${rules.map(ruleCard).join("")}</div>
          <div class="inline" style="margin-top:8px">
            <button type="button" class="secondary" id="add-rule">Add a sleep time</button>
            <button type="button" class="quiet" id="pre-nightly">Every night</button>
            <button type="button" class="quiet" id="pre-split">Weeknights and weekends</button>
          </div>
          <p class="hint" style="margin-left:0">A sleep time that ends before it starts, like 10:00 PM to 6:45 AM, runs overnight and ends the next morning.</p>
          <h2>The week</h2>
          <div id="week">${weekPicture(rules, enabled)}</div>
          <div class="savebar sticky"><button class="primary" type="submit">Save schedule</button><span class="msg" role="status"></span></div>
        </form>`;
      bind();
    }

    function readRules() {
      return $$(".rule", page).map((card, i) => ({
        id: rules[i] && rules[i].id,
        days: $$("[data-day]", card).filter((c) => c.checked).map((c) => Number(c.dataset.day)),
        off: $(".off", card).value || "22:00", on: $(".on", card).value || "06:45", enabled: $(".en", card).checked,
      }));
    }
    function refreshLive() {
      rules = readRules(); enabled = $("#sch-on").checked;
      $$(".rule", page).forEach((card, i) => { $(".sum", card).textContent = rules[i].days.length ? summarize(rules[i]) : "Pick at least one day."; });
      $("#week").innerHTML = weekPicture(rules, enabled);
    }

    async function override(mode, hours) {
      const msg = $("#ov-msg");
      try {
        await api("screen/sleep", { mode, hours });
        FD.status = await api("status");
        draw();
        toast(mode === "clear" ? "Back on the schedule." : mode === "sleep" ? "Putting the screen to sleep." : "Keeping the screen awake.");
      } catch (err) { msg.className = "sub state bad"; msg.textContent = err.message; }
    }

    function bind() {
      $("#ov-sleep").onclick = () => override("sleep", Number($("#ov-hours").value));
      $("#ov-wake").onclick = () => override("wake", Number($("#ov-hours").value));
      const clear = $("#ov-clear"); if (clear) clear.onclick = () => override("clear", 0);
      const form = $("#sch-form");
      form.addEventListener("input", refreshLive);
      form.addEventListener("change", refreshLive);
      $("#rules").addEventListener("click", (e) => {
        if (!e.target.classList.contains("remove")) return;
        rules = readRules(); rules.splice(Number(e.target.closest(".rule").dataset.i), 1); enabled = $("#sch-on").checked; draw();
      });
      const add = (list) => { rules = readRules().concat(list); enabled = $("#sch-on").checked; draw(); };
      const all = [0, 1, 2, 3, 4, 5, 6];
      $("#add-rule").onclick = () => add([{ days: all, off: "22:00", on: "06:45", enabled: true }]);
      $("#pre-nightly").onclick = () => { rules = [{ days: all, off: "22:00", on: "06:45", enabled: true }]; enabled = true; draw(); };
      $("#pre-split").onclick = () => {
        rules = [{ days: [0, 1, 2, 3, 4], off: "22:00", on: "06:45", enabled: true }, { days: [5, 6], off: "23:30", on: "08:30", enabled: true }];
        enabled = true; draw();
      };
      FD.bindSave(form, () => ({ schedule: { enabled: $("#sch-on").checked, rules: readRules() } }), async () => {
        FD.status = await api("status").catch(() => FD.status);
        rules = FD.settings.schedule.rules.map((r) => ({ ...r })); enabled = FD.settings.schedule.enabled;
        draw();
        toast("Saved. The TV follows the new times within a few seconds.");
      });
    }
    draw();
  };
})();
