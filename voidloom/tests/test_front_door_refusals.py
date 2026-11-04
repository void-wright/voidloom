"""The `voidloom` front door keeps the engine's typed refusals, in a package box with a real lock.

The engine's `main()` turns a typed refusal into an envelope, but the CLI calls the engine in process, so a refusal reached
the front door as an exception and printed `unhandled`, losing its code and its way out.
  * control: a recall whose model bytes are not on this disk (SNAPSHOT_ABSENT) is the embedding capability being absent, so
    it answers from Tier 0 over the same brain, stamped degraded, with the refusal named;
  * saboteur: an integrity refusal (WEIGHTS_MISMATCH: the lock and the environment disagree) is never masked by a keyword
    answer, and is never `unhandled`: it returns the engine's own code;
  * saboteur: a store with the bytes absent is typed or degraded, never `unhandled`.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(os.environ.get("VOIDLOOM_ROOT") or Path(__file__).resolve().parents[2]).resolve()


def _envelope(text):
    t = text or ""
    return json.loads(t[t.index("{"):]) if "{" in t else {}


class TypedRefusalsAtTheFrontDoor(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory(prefix="vl_frontdoor_")
        cls.box = Path(cls._tmp.name) / "pkg"
        out = subprocess.run(["git", "ls-files"], cwd=str(ROOT), capture_output=True, text=True, timeout=60)
        if out.returncode != 0:
            raise unittest.SkipTest("git unavailable; cannot build a package box")
        for rel in [p for p in out.stdout.split("\n") if p.strip()]:
            if (ROOT / rel).is_file():
                (cls.box / rel).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(ROOT / rel, cls.box / rel)
        cls.empty = Path(cls._tmp.name) / "empty_home"
        cls.empty.mkdir()
        cls.db = cls.box / "claude_memory.db"
        for args in (["organs/claude_memory.py", "init", "--tier", "cpu"], ["organs/claude_memory_lite.py", "load-seed", "--file", "seed/seed_pack.json"]):
            p = subprocess.run([sys.executable] + args, cwd=str(cls.box), env=cls.env(cls), capture_output=True, text=True,
                               timeout=180)
            if p.returncode != 0:
                raise AssertionError((args, p.stdout[-600:], p.stderr[-600:]))
        if not (cls.box / "embedder.lock.json").is_file():
            raise AssertionError("init wrote no lock")

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def env(self, **kw):
        e = {k: v for k, v in os.environ.items()
             if k not in ("WIZARD_EMBED_MODEL", "HF_HUB_CACHE", "HUGGINGFACE_HUB_CACHE", "XDG_CACHE_HOME", "PYTHONPATH")}
        e.update({"HOME": str(self.empty), "HF_HOME": str(self.empty / "hf"), "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1",
                  "PYTHONDONTWRITEBYTECODE": "1", "WIZARD_LIBRARY_DB": str(self.db), "PYTHONPATH": str(self.box)})
        e.update(kw)
        return e

    def _voidloom(self, *args, **env):
        p = subprocess.run([sys.executable, "-m", "voidloom"] + list(args) + ["--json"], cwd=str(self.box),
                           env=self.env(**env), capture_output=True, text=True, timeout=300)
        return p, _envelope(p.stdout)

    def test_control_recall_with_the_bytes_absent_answers_from_tier_0_and_says_so(self):
        p, env = self._voidloom("recall", "--query", "capture knowledge as I work", "--top-k", "3")
        self.assertNotIn("unhandled", p.stdout + p.stderr)
        data = env.get("data") or env
        self.assertEqual(data.get("status"), "ok", p.stdout[-800:])
        self.assertEqual(data.get("tier"), 0, p.stdout[-800:])
        self.assertTrue(data.get("degraded"), p.stdout[-800:])
        self.assertIn("SNAPSHOT_ABSENT", json.dumps(data.get("degraded")))
        self.assertGreater(data.get("result_count", 0), 0, "the keyword arm answered nothing")

    def test_saboteur_an_integrity_refusal_is_typed_and_never_a_keyword_answer(self):
        p, env = self._voidloom("recall", "--query", "capture knowledge", "--top-k", "3", WIZARD_EMBED_MODEL="qwen")
        self.assertNotIn("unhandled", p.stdout + p.stderr)
        self.assertIn("WEIGHTS_MISMATCH", p.stdout, p.stdout[-800:])
        self.assertNotEqual(p.returncode, 0)
        self.assertFalse((env.get("data") or {}).get("results"), "an integrity refusal fell through to keywords")

    def test_saboteur_a_store_with_the_bytes_absent_is_never_unhandled(self):
        p, _env = self._voidloom("store", "--content", "a note for the front door", "--category", "insight")
        self.assertNotIn("unhandled", p.stdout + p.stderr)


if __name__ == "__main__":
    unittest.main()
