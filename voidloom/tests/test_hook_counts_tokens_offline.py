"""The session hook makes no network call, on a host that has tiktoken and no cached encoding.

tiktoken's get_encoding downloads on a cache miss and again on a cached file that fails its hash, so the counter the hook
runs must not call it. A child process gets a stub `tiktoken` that records the call and a socket that records any
connection; the hook itself runs there. An explicit `tiktoken` counter is a person asking, and still loads it.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

_STUB = """
import os
def get_encoding(name):
    with open(os.environ["PROBE_LOG"], "a") as f:
        f.write("get_encoding %s\\n" % name)
    class Enc:
        def encode(self, s, disallowed_special=()):
            return s.split()
    return Enc()
"""

_SITE = """
import os, socket
def _refuse(*a, **k):
    with open(os.environ["PROBE_LOG"], "a") as f:
        f.write("socket %r\\n" % (a[1:] or a,))
    raise OSError("this test blocks the network")
socket.socket.connect = _refuse
socket.socket.connect_ex = _refuse
socket.create_connection = _refuse
socket.getaddrinfo = _refuse
"""

_COUNTER = """
import json, sys
sys.path.insert(0, sys.argv[1])
import voidloom_preamble as vp
name, count = vp.make_counter(sys.argv[2])
pre = vp.build_preamble(db=sys.argv[3], home=sys.argv[4], organs=sys.argv[1], token_counter=sys.argv[2])
print(json.dumps({"name": name, "payload": pre.counter, "tokens": count("four words of text")}))
"""


class _Child(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.d = Path(self.tmp.name)
        probe = self.d / "probe"
        probe.mkdir()
        (probe / "tiktoken.py").write_text(_STUB, encoding="utf-8")
        (probe / "sitecustomize.py").write_text(_SITE, encoding="utf-8")
        self.log = self.d / "calls.log"
        self.log.write_text("", encoding="utf-8")
        self.env = {k: v for k, v in os.environ.items() if not k.startswith(("WIZARD_", "VOIDLOOM_"))}
        self.env.update(HOME=str(self.d), PYTHONPATH=str(probe), PROBE_LOG=str(self.log), PYTHONDONTWRITEBYTECODE="1",
                        WIZARD_LIBRARY_DB=str(self.d / "claude_memory.db"))

    def counter(self, name):
        p = subprocess.run([sys.executable, "-c", _COUNTER, str(ROOT / "organs"), name, str(self.d / "claude_memory.db"),
                            str(self.d)], capture_output=True, text=True, env=self.env, timeout=120)
        self.assertEqual(p.returncode, 0, p.stderr[-800:])
        return json.loads(p.stdout.strip().splitlines()[-1])

    def calls(self):
        return self.log.read_text(encoding="utf-8")


class AutoNeverReachesTheNetwork(_Child):
    def test_saboteur_auto_does_not_call_get_encoding(self):
        out = self.counter("auto")
        self.assertEqual(self.calls(), "")
        self.assertEqual(out["name"], "heuristic:chars_div4_ceil")

    def test_the_counters_name_stays_in_the_payload(self):
        self.assertEqual(self.counter("auto")["payload"], "heuristic:chars_div4_ceil")

    def test_saboteur_the_hook_itself_calls_nothing(self):
        p = subprocess.run([sys.executable, str(ROOT / "hooks" / "session_start_inject.py")], input="{}", capture_output=True,
                           text=True, env=self.env, timeout=120)
        self.assertEqual(p.returncode, 0, p.stderr[-800:])
        self.assertEqual(self.calls(), "")


class AnExplicitCounterIsAPersonAsking(_Child):
    def test_control_the_stub_is_live_and_tiktoken_still_loads(self):
        out = self.counter("tiktoken")
        self.assertIn("get_encoding cl100k_base", self.calls())
        self.assertEqual(out["name"], "tiktoken:cl100k_base")
        self.assertEqual(out["tokens"], 4)


if __name__ == "__main__":
    unittest.main()
