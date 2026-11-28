/* Assistant: one shared conversation with Claude about the dashboard's own code. It proposes changes;
   nothing reaches the TV until someone presses Apply here (tests, commit, then a new image is built). */
(function () {
  "use strict";
  const FD = (window.FD = window.FD || { pages: {} });

  FD.pages.assistant = function (page) {
    const { $, esc, api, toast } = FD;
    let timer = 0, last = null, seen = -1;

    page.innerHTML = `
      <h1>Assistant</h1>
      <p class="lede">Ask Claude to change or add something to the dashboard. Everyone sees the same conversation, and it remembers how this household's setup works. It only proposes: nothing changes on the TV until you press Apply.</p>
      <div id="as-notice"></div>
      <div class="chat" id="as-log" aria-live="polite"></div>
      <div id="as-work"></div>
      <form class="composer" id="as-form">
        <label for="as-text" class="sr">Request</label>
        <textarea id="as-text" rows="3" maxlength="8000" placeholder="For example: make the weather box show the wind in km/h"></textarea>
        <div class="inline"><button class="primary" type="submit" id="as-send">Send</button>
          <button type="button" class="quiet" id="as-stop" hidden>Stop</button>
          <span class="hint" style="margin:0">⌘ or Ctrl + Enter sends</span></div>
      </form>
      <div id="as-changes"></div>
      <h2>Conversation</h2>
      <div class="group"><div class="item"><span class="label">Start over</span>
        <div class="inline"><button type="button" class="secondary" id="as-new">Start a new conversation</button></div>
        <span class="sub">Clears the history shown here. The assistant keeps its memory of the setup.</span></div>
        <div class="item"><span class="label">Its copy of the code</span>
        <div class="inline"><button type="button" class="secondary" id="as-sync">Update from GitHub</button></div>
        <span class="sub" id="as-gh">Takes the latest published code. Only when nothing is waiting to be applied.</span></div></div>`;

    const text = (s) => esc(s || "").replace(/`([^`\n]+)`/g, "<code>$1</code>");

    function render(st) {
      last = st;
      const n = $("#as-notice");
      if (st.error && st.setup === false) {
        n.innerHTML = `<div class="notice warn"><b>Not set up yet.</b> The assistant runs in its own container next to the dashboard. See "Built-in assistant" in the README to add it.</div>`;
      } else if (st.error && !st.messages) {
        n.innerHTML = `<div class="notice bad">${esc(st.error)}</div>`;
      } else if (!st.signedIn) {
        n.innerHTML = `<div class="notice warn"><b>Claude isn't signed in yet.</b> On the server, run this once and follow the steps to sign in with your Claude account:
          <pre><code>docker exec -it family-dashboard-assistant claude</code></pre>Type <code>/login</code> if it doesn't ask, then <code>/exit</code> when it's done.</div>`;
      } else n.innerHTML = "";

      const msgs = st.messages || [];
      if (msgs.length !== seen) {
        seen = msgs.length;
        $("#as-log").innerHTML = msgs.length ? msgs.map((m) => {
          if (m.role === "user") return `<div class="msg me"><div class="bubble">${text(m.text)}</div></div>`;
          if (m.role === "assistant") return `<div class="msg them"><div class="bubble">${text(m.text)}${m.tools && m.tools.length ? `<details class="tools"><summary>${m.tools.length} step${m.tools.length === 1 ? "" : "s"}</summary><ul>${m.tools.map((t) => `<li><code>${esc(t)}</code></li>`).join("")}</ul></details>` : ""}</div></div>`;
          return `<div class="msg note ${m.role === "error" ? "bad" : ""}">${text(m.text)}</div>`;
        }).join("") : `<p class="hint" style="margin:0">No conversation yet.</p>`;
        $("#as-log").scrollTop = $("#as-log").scrollHeight;
      }

      const p = st.progress;
      $("#as-work").innerHTML = st.busy ? `<div class="notice working"><b>Working…</b>${p && p.tools && p.tools.length ? `<ul>${p.tools.map((t) => `<li><code>${esc(t)}</code></li>`).join("")}</ul>` : " Reading the code."}</div>` : "";
      $("#as-send").disabled = !!st.busy || !st.signedIn;
      $("#as-stop").hidden = !st.busy;

      const ch = st.changes || { files: [] }, ap = st.apply || {}, b = st.build;
      let html = "";
      if (ch.files && ch.files.length) {
        html += `<h2>Proposed changes</h2><div class="group"><div class="item" style="grid-template-columns:1fr">
          <div>${ch.files.length} file${ch.files.length === 1 ? "" : "s"} changed: ${ch.files.map((f) => `<code>${esc(f)}</code>`).join(" ")}</div>
          <div class="inline"><button type="button" class="secondary" id="as-diff">Show the changes</button></div>
          <pre class="diff" id="as-difftext" hidden></pre>
          <label class="tfield" style="width:100%">Describe it in a few words (optional)<input type="text" id="as-note" maxlength="200" placeholder="Wind in km/h"></label>
          <div class="inline"><button type="button" class="primary" id="as-apply" ${ap.status === "testing" ? "disabled" : ""}>Apply</button>
            <button type="button" class="danger" id="as-discard">Discard</button>
            <span class="sub" style="grid-column:auto">Apply runs the tests first; if they fail, nothing changes.</span></div>
        </div></div>`;
      }
      if (ap.status === "testing") html += `<div class="notice working"><b>Running the tests…</b> This takes about a minute.</div>`;
      if (ap.status === "failed") html += `<div class="notice bad"><b>Not applied.</b><pre>${esc(ap.output)}</pre></div>`;
      if (b && b.state === "building") html += `<div class="notice working"><b>Building the new version…</b></div>`;
      if (b && b.state === "built" && ap.status !== "testing") html += `<div class="notice good"><b>Ready.</b> ${esc(b.message)}</div>`;
      if (b && b.state === "failed") html += `<div class="notice bad">${esc(b.message)}</div>`;
      $("#as-changes").innerHTML = html;
      if (ch.notOnGitHub) $("#as-gh").innerHTML = `${ch.notOnGitHub} applied change${ch.notOnGitHub === 1 ? " isn't" : "s aren't"} on GitHub yet. <button type="button" class="quiet" id="as-patch">Download as a patch</button>`;
      bindChanges();
    }

    function bindChanges() {
      const d = $("#as-diff");
      if (d) d.onclick = async () => {
        const pre = $("#as-difftext");
        if (!pre.hidden) { pre.hidden = true; d.textContent = "Show the changes"; return; }
        try {
          const r = await api("assistant/diff");
          pre.innerHTML = esc(r.diff || "").split("\n").map((ln) => ln.startsWith("+") && !ln.startsWith("+++") ? `<ins>${ln}</ins>` : ln.startsWith("-") && !ln.startsWith("---") ? `<del>${ln}</del>` : ln).join("\n");
          pre.hidden = false; d.textContent = "Hide the changes";
        } catch (err) { toast(err.message); }
      };
      const a = $("#as-apply");
      if (a) a.onclick = () => act("apply", { note: ($("#as-note") || {}).value || "" }, "Running the tests, then applying.");
      const x = $("#as-discard");
      if (x) x.onclick = () => { if (confirm("Throw away the proposed changes? This can't be undone.")) act("discard", {}, "Discarded."); };
      const pt = $("#as-patch");
      if (pt) pt.onclick = async () => {
        try {
          const r = await api("assistant/patch");
          const url = URL.createObjectURL(new Blob([r.patch || ""], { type: "text/plain" }));
          const link = Object.assign(document.createElement("a"), { href: url, download: "assistant-changes.patch" });
          link.setAttribute("download", "assistant-changes.patch");
          document.body.appendChild(link); link.click(); link.remove(); URL.revokeObjectURL(url);
        } catch (err) { toast(err.message); }
      };
    }

    async function act(what, body, ok) {
      try { await api(`assistant/${what}`, body); if (ok) toast(ok); } catch (err) { toast(err.message); }
      poll();
    }

    async function poll() {
      clearTimeout(timer);
      if (!page.isConnected || !$("#as-log")) return;
      let st;
      try { st = await api("assistant/state"); } catch (err) { st = { error: err.message, setup: !/set up/.test(err.message) }; }
      if (!page.isConnected || !$("#as-log")) return;
      render(st);
      const active = st.busy || (st.apply && st.apply.status === "testing") || (st.build && st.build.state === "building");
      timer = setTimeout(poll, active ? 2000 : 10000);
    }

    $("#as-form").onsubmit = async (e) => {
      e.preventDefault();
      const t = $("#as-text").value.trim();
      if (!t) return;
      $("#as-send").disabled = true;
      try { await api("assistant/ask", { text: t }); $("#as-text").value = ""; } catch (err) { toast(err.message); }
      poll();
    };
    $("#as-text").addEventListener("keydown", (e) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) $("#as-form").requestSubmit(); });
    $("#as-stop").onclick = () => act("cancel", {}, "Stopping.");
    $("#as-new").onclick = () => { if (confirm("Start a new conversation? The history shown here is cleared; the assistant's memory stays.")) act("new", {}, "New conversation."); };
    $("#as-sync").onclick = () => act("sync", {}, "Updating the copy.");
    poll();
  };
})();
