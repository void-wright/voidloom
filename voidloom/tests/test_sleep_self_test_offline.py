"""`sleep_loop.py --unit-tests` fetches nothing and says what it did not run.

Its reflection row reaches the embedder, so on a clean host it could reach the hub. The
self-test now forces the offline flags itself, verifies the pin with allow_fetch=False,
and counts what it could not run as `refused`, typed. Its brain is a locked one, so the
centroid rule runs in the shipped self-test too: a member with no stamp stays out of the
cluster, and the centroid is stamped as a centroid under the lock.

Run with an EMPTY hub cache, no offline variable from outside, and a socket observer that
denies every connect. Children inherit the suite's import blocker, so no model loads.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SLEEP = ROOT / "organs" / "sleep_loop.py"


def _socket_observer(root: Path, log: Path) -> Path:
    """A sitecustomize on PYTHONPATH: logs every connect a child makes, then denies it, so 'reached for the network'
    is observable and nothing leaves the host."""
    d = root / "sock_observer"
    d.mkdir(parents=True, exist_ok=True)
    (d / "sitecustomize.py").write_text(textwrap.dedent(f"""
        import json, socket
        _LOG = {str(log)!r}
        def _rec(addr, fam):
            with open(_LOG, "a") as fh:
                fh.write(json.dumps({{"family": str(fam), "addr": repr(addr)}}) + "\\n")
        _oc, _ocx = socket.socket.connect, socket.socket.connect_ex
        def connect(self, addr):
            _rec(addr, self.family)
            if "AF_UNIX" not in str(self.family):
                raise OSError("connect denied by the test's socket observer")
            return _oc(self, addr)
        def connect_ex(self, addr):
            _rec(addr, self.family)
            if "AF_UNIX" not in str(self.family):
                return 111
            return _ocx(self, addr)
        socket.socket.connect, socket.socket.connect_ex = connect, connect_ex
    """), encoding="utf-8")
    return d


class _SelfTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="vl_sleep_selftest_"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.hub = self.tmp / "hub"
        self.hub.mkdir()
        (self.tmp / "home").mkdir()
        self.log = self.tmp / "sock.jsonl"
        self.obs = _socket_observer(self.tmp, self.log)

    def run_it(self, **extra):
        env = {k: v for k, v in os.environ.items()
               if k not in ("WIZARD_LIBRARY_DB", "CLAUDE_MEMORY_DB", "WIZARD_EMBED_MODEL", "WIZARD_LIBRARY_HOME",
                            "HF_HUB_CACHE", "HUGGINGFACE_HUB_CACHE", "HF_HOME", "HF_HUB_OFFLINE",
                            "TRANSFORMERS_OFFLINE")}
        env.update({"HF_HUB_CACHE": str(self.hub), "PYTHONDONTWRITEBYTECODE": "1", "HOME": str(self.tmp / "home")})
        env["PYTHONPATH"] = str(self.obs) + os.pathsep + env.get("PYTHONPATH", "")
        env.update(extra)
        return subprocess.run([sys.executable, str(SLEEP), "--unit-tests"], capture_output=True, text=True,
                              timeout=600, env=env)

    def network_rows(self):
        if not self.log.exists():
            return []
        rows = [json.loads(l) for l in self.log.read_text(encoding="utf-8").splitlines() if l.strip()]
        return [r for r in rows if "AF_UNIX" not in r["family"]]


class OnAHostWithNoSnapshot(_SelfTest):
    def test_control_it_refuses_typed_fetches_nothing_and_opens_no_socket(self):
        r = self.run_it()
        self.assertEqual(r.returncode, 0, r.stdout[-800:] + r.stderr[-800:])
        self.assertIn("0 failed", r.stdout)
        self.assertIn("1 refused", r.stdout)
        self.assertIn("REFUSED: reflection_embedding SNAPSHOT_ABSENT", r.stdout)
        self.assertNotIn("PASS: reflection_vectored_under_lock", r.stdout)
        self.assertEqual([p for p in self.hub.rglob("*") if p.is_file()], [], "the self-test wrote into the cache")
        self.assertEqual(self.network_rows(), [], "the self-test opened a network socket")

    def test_saboteur_a_caller_asking_for_online_does_not_get_it(self):
        """Forced, never setdefault: a value from outside does not reopen the door."""
        r = self.run_it(HF_HUB_OFFLINE="0", TRANSFORMERS_OFFLINE="0")
        self.assertEqual(r.returncode, 0, r.stdout[-800:] + r.stderr[-800:])
        self.assertIn("REFUSED: reflection_embedding SNAPSHOT_ABSENT", r.stdout)
        self.assertEqual(self.network_rows(), [], "the self-test opened a network socket")


class TheCentroidRuleRunsInTheShippedSelfTest(_SelfTest):
    def test_control_it_runs_on_a_locked_brain_with_the_unstamped_saboteur(self):
        r = self.run_it()
        self.assertEqual(r.returncode, 0, r.stdout[-800:] + r.stderr[-800:])
        for row in ("PASS: members_stamped_under_lock", "PASS: saboteur_unstamped_vector_is_red",
                    "PASS: saboteur_unstamped_member_excluded", "PASS: centroid_stamped"):
            self.assertIn(row, r.stdout)


if __name__ == "__main__":
    unittest.main()
