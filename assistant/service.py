"""The dashboard's built-in assistant: Claude Code working on a copy of the dashboard's code.

Runs in its own container, reachable only from the dashboard server (which checks the settings sign-in)
and only with the shared key in ~/assistant.key. It keeps ONE conversation and ONE memory for the whole
household, so every device sees the same history:

  ~/.claude/CLAUDE.md     the memory Claude Code reads at the start of every request (private, never in git)
  ~/assistant/state.json  the conversation as shown in Settings, and the Claude session to resume
  ~/work                  a git copy of the dashboard's code that Claude edits

Claude only proposes. A change reaches the TV only when someone presses Apply in Settings: this service runs
the tests and commits; the dashboard then asks a helper on the host to build the image from that exact commit.
This container has no Docker access and can't start a build itself.
"""

from __future__ import annotations

import hmac
import json
import os
import shutil
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HOME = Path(os.environ.get("HOME", "/home/node"))
WORK = Path(os.environ.get("ASSISTANT_WORK", HOME / "work"))
STATE = HOME / "assistant" / "state.json"
KEYFILE = Path(os.environ.get("ASSISTANT_KEYFILE", HOME / "assistant.key"))
REPO = os.environ.get("ASSISTANT_REPO", "https://github.com/gklier/kliercalendar.git")
CLAUDE = os.environ.get("ASSISTANT_CLAUDE", "claude")
PORT = int(os.environ.get("ASSISTANT_PORT", "8090"))
RUN_LIMIT = 20 * 60          # seconds a single request may take
KEEP_MESSAGES = 400          # the conversation shown in Settings keeps this many entries

# What Claude may do without asking. Editing files in the work copy and running the tests and read-only git
# commands; nothing that reaches the network, Docker or the running dashboard.
ALLOWED_TOOLS = [
    "Read", "Edit", "Write", "MultiEdit", "Glob", "Grep", "TodoWrite",
    "Bash(python3 -m unittest:*)", "Bash(/opt/venv/bin/python -m unittest:*)", "Bash(node --check:*)",
    "Bash(bash -n:*)", "Bash(git status:*)", "Bash(git diff:*)", "Bash(git log:*)", "Bash(git show:*)",
    "Bash(ls:*)", "Bash(wc:*)", "Bash(cd:*)",
]
DISALLOWED_TOOLS = ["WebFetch", "WebSearch", "Bash(git push:*)", "Bash(git commit:*)", "Bash(git reset:*)",
                    "Bash(curl:*)", "Bash(wget:*)", "Bash(docker:*)", "Bash(ssh:*)"]

ROLE = """You are the Family Dashboard's built-in assistant, used from the dashboard's own Settings page.
You work in a git copy of the dashboard's code (the current folder). You only propose changes: edit the files,
run the tests (cd server && /opt/venv/bin/python -m unittest test_server test_chaos; cd pi && python3 -m unittest
test_resilience test_kiosk_setup), and explain what you changed in plain words for a non-technical family.
You cannot commit, push, build or deploy; the person presses Apply in Settings when they are happy, which runs
the tests again, commits, and builds the new image. The repository is public: never put addresses, names,
passwords, keys or other personal details in the code or docs. Your memory is ~/.claude/CLAUDE.md: read it,
follow it, and add short notes there when you learn something lasting about this household's setup."""

lock = threading.Lock()
run: dict = {"proc": None, "thread": None}
apply_job: dict = {"status": "idle", "hash": "", "output": "", "at": 0}


# ---------------------------------------------------------------------------- state

def load() -> dict:
    try:
        data = json.loads(STATE.read_text())
        if isinstance(data, dict):
            data.setdefault("messages", [])
            return data
    except (OSError, ValueError):
        pass
    return {"session": "", "messages": [], "busy": False}


def save(data: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    data["messages"] = data["messages"][-KEEP_MESSAGES:]
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1))
    os.replace(tmp, STATE)


def add(role: str, text: str, **extra) -> None:
    with lock:
        data = load()
        data["messages"].append({"role": role, "text": text, "at": int(time.time()), **extra})
        save(data)


def git(*args: str, check: bool = False, timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(WORK), *args], capture_output=True, text=True, timeout=timeout, check=check)


def ensure_work() -> None:
    if not (WORK / ".git").exists():
        WORK.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "clone", "-q", REPO, str(WORK)], check=True, timeout=300)
    git("config", "user.name", "Family Dashboard Assistant")
    git("config", "user.email", "assistant@family-dashboard.invalid")


def signed_in() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY")) or (HOME / ".claude" / ".credentials.json").exists()


def changes() -> dict:
    """What Claude has changed and not applied yet."""
    git("add", "--intent-to-add", "--all")
    stat = git("diff", "--stat").stdout.strip()
    files = [ln[3:] for ln in git("status", "--porcelain").stdout.splitlines() if ln.strip()]
    ahead = git("rev-list", "--count", "@{u}..HEAD").stdout.strip()
    return {"files": files, "stat": stat, "notOnGitHub": int(ahead) if ahead.isdigit() else 0}


# ---------------------------------------------------------------------------- asking Claude

def describe_tool(block: dict) -> str:
    name, inp = block.get("name", "tool"), block.get("input") or {}
    target = inp.get("file_path") or inp.get("path") or inp.get("pattern") or inp.get("command") or ""
    for base in sorted({str(WORK.resolve()), str(WORK)}, key=len, reverse=True):   # paths in the code copy as short names
        target = str(target).replace(base + "/", "")
    return f"{name} {target}".strip()[:160]


def ask(text: str) -> None:
    with lock:
        data = load()
        session = data.get("session") or ""
        data["busy"] = True
        data["error"] = ""
        save(data)
    cmd = [CLAUDE, "-p", text, "--output-format", "stream-json", "--verbose", "--permission-mode", "acceptEdits",
           "--append-system-prompt", ROLE, "--allowedTools", ",".join(ALLOWED_TOOLS),
           "--disallowedTools", ",".join(DISALLOWED_TOOLS)]
    if session:
        cmd += ["--resume", session]
    reply, tools, new_session, error = [], [], session, ""
    started = time.time()
    try:
        proc = subprocess.Popen(cmd, cwd=WORK, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)
        run["proc"] = proc
        for line in proc.stdout:
            if time.time() - started > RUN_LIMIT:
                proc.kill()
                error = "That took longer than 20 minutes and was stopped."
                break
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            new_session = ev.get("session_id") or new_session
            if ev.get("type") == "assistant":
                for block in (ev.get("message") or {}).get("content") or []:
                    if block.get("type") == "text" and block.get("text", "").strip():
                        reply.append(block["text"].strip())
                    elif block.get("type") == "tool_use":
                        tools.append(describe_tool(block))
                with lock:                                   # show progress while it works
                    d = load()
                    d["progress"] = {"text": reply[-1][:400] if reply else "", "tools": tools[-6:]}
                    save(d)
            elif ev.get("type") == "result":
                if ev.get("is_error"):
                    error = str(ev.get("result") or "Claude reported an error.")
                elif ev.get("result") and not reply:
                    reply.append(str(ev["result"]).strip())
        proc.wait(timeout=30)
        if proc.returncode and not error:
            err = (proc.stderr.read() or "").strip()
            error = friendly(err) or f"Claude stopped with code {proc.returncode}."
    except FileNotFoundError:
        error = "Claude Code isn't installed in the assistant container."
    except Exception as e:  # noqa: BLE001 — whatever happens, the conversation records it and the lock is released
        error = f"Something went wrong running Claude: {type(e).__name__}"
    finally:
        run["proc"] = None
    if reply or tools:
        add("assistant", "\n\n".join(reply) or "(Made changes without a written reply.)", tools=tools)
    if error:
        add("error", error)
    with lock:
        d = load()
        d["busy"] = False
        d["progress"] = None
        d["session"] = new_session or d.get("session", "")
        d["error"] = error
        save(d)


def friendly(err: str) -> str:
    low = err.lower()
    if "login" in low or "not logged in" in low or "authenticate" in low or "api key" in low:
        return "Claude isn't signed in yet. See the sign-in steps on the Assistant page."
    if "rate" in low and "limit" in low or "usage limit" in low:
        return "Your Claude plan's usage limit has been reached for now. Try again later."
    return err.splitlines()[-1][:300] if err else ""


# ---------------------------------------------------------------------------- apply: test, then commit

def run_tests() -> tuple[bool, str]:
    out = []
    for folder, cmd in (("server", [os.environ.get("ASSISTANT_PYTHON", "/opt/venv/bin/python"), "-m", "unittest", "test_server", "test_chaos"]),
                        ("pi", ["python3", "-m", "unittest", "test_resilience", "test_kiosk_setup"])):
        p = subprocess.run(cmd, cwd=WORK / folder, capture_output=True, text=True, timeout=900)
        tail = "\n".join((p.stdout + p.stderr).strip().splitlines()[-25:])
        out.append(f"== {folder}\n{tail}")
        if p.returncode:
            return False, "\n".join(out)
    return True, "\n".join(out)


def apply_change(note: str) -> None:
    apply_job.update(status="testing", hash="", output="", at=int(time.time()))
    try:
        if not changes()["files"]:
            apply_job.update(status="failed", output="There are no changes to apply.")
            return
        ok, output = run_tests()
        if not ok:
            apply_job.update(status="failed", output="The tests failed, so nothing was applied.\n\n" + output)
            return
        git("add", "--all", check=True)
        msg = (note.strip() or "Change made with the dashboard assistant")[:200]
        git("commit", "-q", "-m", msg, check=True)
        sha = git("rev-parse", "HEAD").stdout.strip()
        apply_job.update(status="committed", hash=sha, output=output)
        add("system", f"Applied: {msg}. The new version is being built.")
    except Exception as e:  # noqa: BLE001
        apply_job.update(status="failed", output=f"Couldn't apply: {type(e).__name__}: {e}")


# ---------------------------------------------------------------------------- web

def key() -> str:
    try:
        return KEYFILE.read_text().strip()
    except OSError:
        return ""


class Handler(BaseHTTPRequestHandler):
    server_version = "FamilyDashboardAssistant/1.0"
    timeout = 30

    def log_message(self, fmt, *args):
        pass

    def _json(self, status: int, obj) -> None:
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _allowed(self) -> bool:
        k = key()
        return bool(k) and hmac.compare_digest(self.headers.get("X-Assistant-Key", ""), k)

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if n < 0 or n > 200_000:
            raise ValueError("Too large")
        data = json.loads(self.rfile.read(n) or b"{}")
        return data if isinstance(data, dict) else {}

    def do_GET(self):
        if not self._allowed():
            return self._json(403, {"error": "Not allowed"})
        if self.path == "/state":
            data = load()
            try:
                ch = changes()
            except Exception:  # noqa: BLE001 — a missing work copy shouldn't hide the conversation
                ch = {"files": [], "stat": "", "notOnGitHub": 0}
            return self._json(200, {"signedIn": signed_in(), "busy": bool(data.get("busy")), "progress": data.get("progress"),
                                    "messages": data["messages"][-200:], "error": data.get("error", ""),
                                    "changes": ch, "apply": dict(apply_job)})
        if self.path == "/diff":
            git("add", "--intent-to-add", "--all")
            diff = git("diff").stdout
            return self._json(200, {"diff": diff[:400_000], "truncated": len(diff) > 400_000})
        if self.path == "/patch":                  # the applied commits GitHub doesn't have yet, as one patch
            p = git("format-patch", "--stdout", "@{u}..HEAD")
            return self._json(200, {"patch": p.stdout[:2_000_000]})
        return self._json(404, {"error": "Not found"})

    def do_POST(self):
        if not self._allowed():
            return self._json(403, {"error": "Not allowed"})
        try:
            data = self._body()
        except (ValueError, json.JSONDecodeError):
            return self._json(400, {"error": "Bad request"})
        busy = bool(load().get("busy"))
        if self.path == "/ask":
            text = str(data.get("text") or "").strip()[:8000]
            if not text:
                return self._json(400, {"error": "Type a request first."})
            if busy or apply_job["status"] == "testing":
                return self._json(409, {"error": "The assistant is still working on the last request."})
            if not signed_in():
                return self._json(409, {"error": "Claude isn't signed in yet. See the sign-in steps on this page."})
            add("user", text, who=str(data.get("who") or "")[:40])
            t = threading.Thread(target=ask, args=(text,), daemon=True)
            run["thread"] = t
            t.start()
            return self._json(200, {"ok": True})
        if self.path == "/cancel":
            proc = run.get("proc")
            if proc:
                proc.kill()
            return self._json(200, {"ok": True})
        if busy or apply_job["status"] == "testing":
            return self._json(409, {"error": "Wait until the assistant has finished."})
        if self.path == "/discard":
            git("checkout", "--", ".")
            git("reset", "-q")
            git("clean", "-fdq")
            add("system", "The proposed changes were discarded.")
            return self._json(200, {"ok": True})
        if self.path == "/apply":
            threading.Thread(target=apply_change, args=(str(data.get("note") or ""),), daemon=True).start()
            return self._json(200, {"ok": True})
        if self.path == "/applied":                  # the dashboard has handed the commit to the build helper
            if apply_job["status"] == "committed" and data.get("hash") == apply_job["hash"]:
                apply_job["status"] = "building"
            return self._json(200, {"ok": True})
        if self.path == "/new":
            with lock:
                d = load()
                d.update(session="", messages=[], error="", progress=None)
                save(d)
            add("system", "A new conversation started. The assistant still has its memory of the household's setup.")
            return self._json(200, {"ok": True})
        if self.path == "/sync":                     # take the latest code from GitHub (only with nothing pending)
            if changes()["files"]:
                return self._json(409, {"error": "Apply or discard the proposed changes first."})
            p = git("pull", "-q", "--rebase", timeout=120)
            if p.returncode:
                git("rebase", "--abort")
                return self._json(409, {"error": "The copy couldn't be brought up to date automatically."})
            add("system", "The assistant's copy of the code is now up to date with GitHub.")
            return self._json(200, {"ok": True})
        return self._json(404, {"error": "Not found"})


def main() -> None:
    ensure_work()
    with lock:                                       # a restart in the middle of a request leaves it idle, not stuck
        d = load()
        if d.get("busy"):
            d.update(busy=False, progress=None, error="The assistant restarted while working; ask again.")
            save(d)
    if not shutil.which(CLAUDE):
        print("Warning: Claude Code is not installed", flush=True)
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    srv.daemon_threads = True
    print(f"Assistant listening on {PORT}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
