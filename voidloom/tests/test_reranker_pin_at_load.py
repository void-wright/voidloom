"""The reranker's bytes are checked when they load, and a recall never reaches the hub.

THE CLAIM UNDER TEST
  * the default reranker's snapshot is hashed against the catalogue's pin BEFORE torch is
    imported: bytes that do not match refuse, typed, and nothing is loaded;
  * a host with no snapshot loads nothing, says SNAPSHOT_ABSENT and fetches nothing, under
    an open network as under an offline one;
  * no load call goes out with the hub open, in a process that imported the libraries first
    or one that did not, and the process is left as it was found;
  * the two load calls keep public's form: by name, at the pinned revision;
  * the fallback never fetches by bare name, and a recall that ends with no reranker keeps
    the fused order and says so in its coverage and its path label.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "organs"))
FILES = ("config.json", "model.safetensors", "tokenizer.json")
REPO = "Qwen/Qwen3-Reranker-0.6B"

FAKE_TRANSFORMERS = r'''
import json, os
LOG = os.environ["VL_CALL_LOG"]
def _rec(api):
    def f(*a, **k):
        import huggingface_hub.constants as c
        with open(LOG, "a") as h:
            h.write(json.dumps({"api": api, "arg0": a[0], "revision": k.get("revision"),
                                "local_files_only": k.get("local_files_only", "<absent>"),
                                "hub_flag": bool(c.HF_HUB_OFFLINE),
                                "env": os.environ.get("HF_HUB_OFFLINE")}) + "\n")
        return Fake()
    return f
class Fake:
    unk_token_id = 0
    def to(self, d):
        with open(LOG, "a") as h:
            h.write(json.dumps({"api": "to"}) + "\n")
        return self
    def eval(self): return self
    def convert_tokens_to_ids(self, t): return {"yes": 9, "no": 2}[t]
    def encode(self, s, add_special_tokens=False): return [1]
class AutoTokenizer: from_pretrained = staticmethod(_rec("tokenizer"))
class AutoModelForCausalLM: from_pretrained = staticmethod(_rec("model"))
'''
FAKE_TORCH = "float16 = 'float16'\nfloat32 = 'float32'\n"
FAKE_DEVICE = "def torch_device(t): return 'cpu'\n"


class _FakeHub(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)
        self.log = self.tmp / "calls.jsonl"

    def tearDown(self):
        self._td.cleanup()

    def _world(self, *, mutate=False, absent=False, partial=False):
        """A hub, and a copy of the organs whose catalogue pins that hub's fake reranker."""
        import embedder_lock as el
        hub = self.tmp / "hub"
        repo = hub / ("models--" + REPO.replace("/", "--"))
        rev = "0" * 39 + "1"
        snap = repo / "snapshots" / rev
        snap.mkdir(parents=True)
        for f in FILES:
            (snap / f).write_bytes(f"synthetic {f}\n".encode() * 3)
        entry = {"model_id": REPO, "revision": rev, "license": "apache-2.0", "loader": "organs/qwen_reranker.py",
                 "status": "active", "files": list(FILES)}
        entry["weights_hash"] = el.compute_entry_hash(entry, snapshot=snap, code_snapshot=None)
        if mutate:
            (snap / "model.safetensors").write_bytes(b"swapped")
        if partial:
            (snap / "model.safetensors").unlink()
        if absent:
            shutil.rmtree(snap)
        organs = self.tmp / "organs"
        organs.mkdir()
        for m in ("embedder_lock.py", "qwen_reranker.py"):
            shutil.copyfile(ROOT / "organs" / m, organs / m)
        (organs / "voidloom_device.py").write_text(FAKE_DEVICE)
        cat = json.loads((ROOT / "organs" / "embedders.lock.json").read_text())
        cat["rerankers"] = {"qwen3-reranker-0.6b": entry}
        (organs / "embedders.lock.json").write_text(json.dumps(cat))
        fakes = self.tmp / "fakes"
        fakes.mkdir()
        (fakes / "transformers.py").write_text(FAKE_TRANSFORMERS)
        (fakes / "torch.py").write_text(FAKE_TORCH)
        return hub, organs, fakes, entry

    def _load(self, hub, organs, fakes, *, offline=False, preimport=False):
        code = ("import json, os, sys\n"
                + ("import huggingface_hub\n" if preimport else "")
                + "import qwen_reranker as q\n"
                "b = q.load_model(force_reload=True)\n"
                "import huggingface_hub.constants as c\n"
                "print(json.dumps({'loaded': b is not None, 'reason': q.absence_reason(), 'pin': q.pin_verified(),"
                " 'hub_flag_after': bool(c.HF_HUB_OFFLINE), 'env_after': os.environ.get('HF_HUB_OFFLINE')}))\n")
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(self.tmp), "HF_HUB_CACHE": str(hub),
               "VL_CALL_LOG": str(self.log), "PYTHONPATH": str(fakes), "PYTHONDONTWRITEBYTECODE": "1"}
        if offline:
            env.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")
        r = subprocess.run([sys.executable, "-c", code], cwd=str(organs), capture_output=True, text=True,
                           timeout=120, env=env)
        self.assertEqual(r.returncode, 0, r.stderr[-800:])
        return json.loads(r.stdout.strip().splitlines()[-1])

    def _calls(self):
        return [json.loads(x) for x in self.log.read_text().splitlines()] if self.log.exists() else []


class LoadTimeVerification(_FakeHub):
    def test_control_verified_bytes_load_at_the_pinned_revision(self):
        hub, organs, fakes, entry = self._world()
        out = self._load(hub, organs, fakes)
        self.assertTrue(out["loaded"], out)
        self.assertEqual(out["pin"]["computed"], entry["weights_hash"])
        calls = self._calls()
        self.assertEqual([c["api"] for c in calls], ["tokenizer", "model", "to"])
        for c in calls[:2]:
            self.assertEqual((c["arg0"], c["local_files_only"]), (REPO, "<absent>"), "public's form, by name")

    def test_saboteur_a_swapped_file_refuses_typed_and_nothing_loads(self):
        hub, organs, fakes, _e = self._world(mutate=True)
        out = self._load(hub, organs, fakes)
        self.assertFalse(out["loaded"])
        self.assertTrue(out["reason"].startswith("WEIGHTS_MISMATCH"), out)
        self.assertIsNone(out["pin"])
        self.assertEqual(self._calls(), [], "no load call on bytes that did not match")

    def test_saboteur_a_missing_listed_file_refuses_typed_and_nothing_loads(self):
        hub, organs, fakes, _e = self._world(partial=True)
        out = self._load(hub, organs, fakes)
        self.assertTrue(out["reason"].startswith("SNAPSHOT_ABSENT"), out)
        self.assertFalse(out["loaded"], out)
        self.assertEqual([c for c in self._calls() if c["api"] == "to"], [], "bytes nobody hashed never reach a device")

    def test_saboteur_a_cold_host_says_snapshot_absent_online_and_offline(self):
        for offline in (False, True):
            with self.subTest(offline=offline):
                d = tempfile.mkdtemp(dir=self.tmp)
                self.tmp, keep = Path(d), self.tmp
                try:
                    hub, organs, fakes, _e = self._world(absent=True)
                    self.log = self.tmp / "calls.jsonl"
                    out = self._load(hub, organs, fakes, offline=offline)
                    placed = [c for c in self._calls() if c["api"] == "to"]
                finally:
                    self.tmp = keep
                self.assertFalse(out["loaded"], "a load that found no verified bytes is not a load")
                self.assertEqual(placed, [], "bytes nobody hashed never reach a device")
                self.assertTrue(out["reason"].startswith("SNAPSHOT_ABSENT"), out)
                self.assertIsNone(out["pin"])

    def test_control_no_load_call_goes_out_with_the_hub_open(self):
        for preimport in (False, True):
            with self.subTest(preimport=preimport):
                d = Path(tempfile.mkdtemp(dir=self.tmp))
                keep, self.tmp = self.tmp, d
                try:
                    hub, organs, fakes, _e = self._world()
                    self.log = d / "calls.jsonl"
                    out = self._load(hub, organs, fakes, preimport=preimport)
                    calls = self._calls()
                finally:
                    self.tmp = keep
                self.assertTrue(calls)
                for c in calls[:2]:
                    self.assertTrue(c["hub_flag"], c)
                    self.assertEqual(c["env"], "1")
                self.assertIsNone(out["env_after"], "the variable is put back")
                self.assertFalse(out["hub_flag_after"], "a later fetch in this process is not left offline")


class RealStackReachesNoSocket(unittest.TestCase):
    """The real libraries, an empty cache and an open network: the socket layer sees nothing."""

    GUARD = r'''
import json, os, socket, sys
_seen = []
_real = socket.socket.connect
def _connect(self, addr):
    _seen.append(repr(addr))
    raise OSError("network refused by the test")
socket.socket.connect = _connect
_gai = socket.getaddrinfo
def _lookup(*a, **k):
    _seen.append("dns:" + repr(a[:2]))
    raise socket.gaierror("network refused by the test")
socket.getaddrinfo = _lookup
sys.path.insert(0, sys.argv[1])
import hybrid_retrieval as hr
import qwen_reranker as q
which = sys.argv[2]
if which == "qwen":
    b = q.load_model(force_reload=True)
    print(json.dumps({"loaded": b is not None, "reason": q.absence_reason(), "seen": _seen}))
else:
    m = hr._load_reranker(force_reload=True)
    print(json.dumps({"loaded": m is not None, "reason": None, "seen": _seen}))
'''

    def _run(self, which):
        with tempfile.TemporaryDirectory() as td:
            env = {k: v for k, v in os.environ.items() if not k.startswith(("HF_", "TRANSFORMERS_"))}
            env.update(HOME=td, HF_HUB_CACHE=str(Path(td) / "hub"), WIZARD_ACCELERATOR="cpu",
                       PYTHONDONTWRITEBYTECODE="1")
            r = subprocess.run([sys.executable, "-c", self.GUARD, str(ROOT / "organs"), which], capture_output=True,
                               text=True, timeout=240, env=env, cwd=td)
        self.assertEqual(r.returncode, 0, r.stderr[-800:])
        return json.loads(r.stdout.strip().splitlines()[-1])

    def _stack(self):
        try:
            import sentence_transformers  # noqa: F401
            import transformers  # noqa: F401
        except Exception as e:
            self.skipTest(f"the semantic stack is absent: NOT MEASURED ({e})")

    def test_saboteur_the_default_reranker_on_a_cold_host_touches_no_socket(self):
        self._stack()
        out = self._run("qwen")
        self.assertFalse(out["loaded"])
        self.assertTrue(out["reason"].startswith("SNAPSHOT_ABSENT"), out)
        self.assertEqual(out["seen"], [], "the load reached for the network")

    def test_saboteur_the_fallback_on_a_cold_host_touches_no_socket(self):
        self._stack()
        out = self._run("bge")
        self.assertFalse(out["loaded"])
        self.assertEqual(out["seen"], [], "the fallback reached for the network")


class ColdRecallIsFlagged(unittest.TestCase):
    def setUp(self):
        import hybrid_retrieval as hr
        self.hr = hr
        self._td = tempfile.TemporaryDirectory()
        import sqlite3
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute("CREATE TABLE memories (id INTEGER PRIMARY KEY, summary TEXT, content TEXT)")
        self.conn.executemany("INSERT INTO memories VALUES (?, ?, ?)", [(1, "a", "alpha"), (2, "b", "beta")])
        self.gate = mock.patch.object(hr, "reranker_gate", return_value={"enabled": True})
        self.gate.start()

    def tearDown(self):
        self.gate.stop()
        self.conn.close()
        self._td.cleanup()

    def test_saboteur_no_reranker_keeps_the_fused_order_and_says_degraded(self):
        with mock.patch.object(self.hr, "_qwen_rerank", return_value=None), \
                mock.patch.object(self.hr, "_load_reranker", return_value=None):
            got = self.hr.reranker_score(self.conn, "q", [2, 1], top_k=2)
        self.assertEqual([m for m, _s in got], [2, 1])
        self.assertEqual(self.hr._rerank_outcome["state"], "degraded")
        self.assertIn("reason", self.hr._rerank_outcome)

    def test_control_a_reranker_that_ran_says_which(self):
        with mock.patch.object(self.hr, "_qwen_rerank", return_value=[(1, 0.9), (2, 0.1)]):
            self.hr.reranker_score(self.conn, "q", [2, 1], top_k=2)
        self.assertEqual(self.hr._rerank_outcome, {"state": "ran", "backend": "qwen"})

    def test_control_the_status_note_no_longer_promises_a_download(self):
        with mock.patch.object(self.hr, "_reranker_in_cache", return_value=False), \
                mock.patch.object(self.hr, "reranker_gate", return_value={
                    "enabled": True, "model": REPO, "accelerator": "cuda", "config_enabled": True}):
            st = self.hr.reranker_status()
        self.assertNotIn("downloads", " ".join(st["notes"]))
        self.assertIn("model_fetch.py", " ".join(st["notes"]))

    def test_saboteur_offline_does_not_read_as_cached(self):
        with tempfile.TemporaryDirectory() as td, \
                mock.patch.dict(os.environ, {"HF_HUB_CACHE": td, "HF_HUB_OFFLINE": "1"}):
            self.assertFalse(self.hr._reranker_in_cache(REPO))


if __name__ == "__main__":
    unittest.main()
