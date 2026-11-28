"""Tests for the assistant service, with a stand-in for Claude Code.  Run: python3 -m unittest -v test_service.py"""

import json
import os
import subprocess
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import service

FAKE_CLAUDE = r'''#!/bin/bash
# Pretends to be "claude -p": edits a file in the current folder and reports in stream-json
prompt="$2"
echo '{"type":"system","subtype":"init","session_id":"sess-123"}'
if [[ "$*" == *"--resume sess-123"* ]]; then echo "resumed" >> resumed.log; fi
echo "# changed by: $prompt" >> README.md
echo '{"type":"assistant","message":{"content":[{"type":"tool_use","name":"Edit","input":{"file_path":"'"$PWD"'/README.md"}}]}}'
echo '{"type":"assistant","message":{"content":[{"type":"text","text":"I added a line to the README."}]}}'
echo '{"type":"result","is_error":false,"result":"I added a line to the README.","session_id":"sess-123"}'
'''


def wait_for(cond, seconds=10):
    end = time.time() + seconds
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


class AssistantTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        origin = root / "origin.git"
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
        seed = root / "seed"
        subprocess.run(["git", "clone", "-q", str(origin), str(seed)], check=True, capture_output=True)
        (seed / "README.md").write_text("hello\n")
        for args in (["add", "."], ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "first"], ["push", "-q", "origin", "HEAD:main"]):
            subprocess.run(["git", "-C", str(seed), *args], check=True, capture_output=True)
        fake = root / "claude"
        fake.write_text(FAKE_CLAUDE)
        fake.chmod(0o755)
        (root / "home").mkdir()
        (root / "home/assistant.key").write_text("k" * 64)
        service.HOME = root / "home"
        service.WORK = root / "home/work"
        service.STATE = root / "home/assistant/state.json"
        service.KEYFILE = root / "home/assistant.key"
        service.REPO = str(origin)
        service.CLAUDE = str(fake)
        service.apply_job.update(status="idle", hash="", output="")
        service.run_tests = lambda: (True, "== server\nOK")          # the real suites are tested on their own
        (root / "home/.claude").mkdir()
        (root / "home/.claude/.credentials.json").write_text("{}")   # "signed in"
        service.ensure_work()
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), service.Handler)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.srv.server_port}"

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        self.tmp.cleanup()

    def call(self, path, body=None, key="k" * 64):
        req = urllib.request.Request(self.base + path, data=json.dumps(body).encode() if body is not None else None,
                                     headers={"X-Assistant-Key": key, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    def state(self):
        return self.call("/state")[1]

    def test_needs_the_key(self):
        self.assertEqual(self.call("/state", key="wrong")[0], 403)
        self.assertEqual(self.call("/ask", {"text": "hi"}, key="")[0], 403)

    def test_ask_propose_apply(self):
        self.assertEqual(self.call("/ask", {"text": "add a line"})[0], 200)
        self.assertTrue(wait_for(lambda: not self.state()["busy"]))
        st = self.state()
        self.assertEqual([m["role"] for m in st["messages"]], ["user", "assistant"])
        self.assertIn("README", st["messages"][1]["text"])
        self.assertEqual(st["messages"][1]["tools"], ["Edit README.md"])
        self.assertEqual(st["changes"]["files"], ["README.md"])
        self.assertIn("+# changed by: add a line", self.call("/diff")[1]["diff"])

        # the next request carries on the same Claude session
        self.call("/ask", {"text": "and another"})
        self.assertTrue(wait_for(lambda: not self.state()["busy"]))
        self.assertTrue((service.WORK / "resumed.log").exists())

        # Apply: tests, then a commit the dashboard can hand to the build helper
        self.assertEqual(self.call("/apply", {"note": "Two lines"})[0], 200)
        self.assertTrue(wait_for(lambda: self.state()["apply"]["status"] == "committed"))
        st = self.state()
        self.assertRegex(st["apply"]["hash"], r"^[0-9a-f]{40}$")
        self.assertEqual(st["changes"]["files"], [])
        self.assertEqual(st["changes"]["notOnGitHub"], 1)
        self.assertIn("Two lines", subprocess.run(["git", "-C", str(service.WORK), "log", "-1", "--format=%s"],
                                                  capture_output=True, text=True).stdout)
        self.assertIn("Subject: [PATCH] Two lines", self.call("/patch")[1]["patch"])
        self.call("/applied", {"hash": st["apply"]["hash"]})
        self.assertEqual(self.state()["apply"]["status"], "building")

    def test_failed_tests_apply_nothing(self):
        service.run_tests = lambda: (False, "FAILED (failures=1)")
        self.call("/ask", {"text": "break it"})
        self.assertTrue(wait_for(lambda: not self.state()["busy"]))
        self.call("/apply", {})
        self.assertTrue(wait_for(lambda: self.state()["apply"]["status"] == "failed"))
        st = self.state()
        self.assertIn("tests failed", st["apply"]["output"])
        self.assertEqual(st["changes"]["files"], ["README.md"])           # still waiting, not committed

    def test_discard_and_new_conversation(self):
        self.call("/ask", {"text": "change"})
        self.assertTrue(wait_for(lambda: not self.state()["busy"]))
        self.assertEqual(self.call("/discard", {})[0], 200)
        self.assertEqual(self.state()["changes"]["files"], [])
        self.assertEqual((service.WORK / "README.md").read_text(), "hello\n")
        self.call("/new", {})
        st = self.state()
        self.assertEqual([m["role"] for m in st["messages"]], ["system"])
        self.assertEqual(service.load()["session"], "")

    def test_not_signed_in(self):
        (service.HOME / ".claude/.credentials.json").unlink()
        s, r = self.call("/ask", {"text": "hi"})
        self.assertEqual(s, 409)
        self.assertIn("signed in", r["error"])
        self.assertFalse(self.state()["signedIn"])

    def test_one_request_at_a_time(self):
        slow = Path(service.CLAUDE).read_text().replace("prompt=\"$2\"", "prompt=\"$2\"; sleep 1")
        Path(service.CLAUDE).write_text(slow)
        self.assertEqual(self.call("/ask", {"text": "one"})[0], 200)
        self.assertTrue(wait_for(lambda: self.state()["busy"], 3))
        self.assertEqual(self.call("/ask", {"text": "two"})[0], 409)
        self.assertEqual(self.call("/discard", {})[0], 409)
        self.assertTrue(wait_for(lambda: not self.state()["busy"]))


if __name__ == "__main__":
    unittest.main()
