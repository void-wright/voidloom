"""What a public user sees when they re-run `init` on a brain that predates the install lock.

`init` locks an existing pre-lock nomic/qwen brain (it writes embedder.lock.json for the tier the brain already
declares); it never loads or hashes a model, so it succeeds whether or not the weights are on this disk. These two
cases pin what happens next, on a pre-lock nomic brain in a package box:

  * snapshot ABSENT (an empty HF cache, HF-offline): init rc 0 and the lock is written; the first act that needs
    the embedder refuses typed, SNAPSHOT_ABSENT, and the refusal names the public fetch verb,
    `python3 organs/model_fetch.py --db <brain>/claude_memory.db` -- never a command this repository does not ship;
  * snapshot PRESENT (this host's own cache): init rc 0, the lock is written, and the bytes on disk hash to the
    lock's weights_hash without instantiating anything. Skipped where the snapshot is not on disk.
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
sys.path.insert(0, str(ROOT / "organs"))


def _last_json(text):
    lines = [ln for ln in (text or "").strip().splitlines() if ln.startswith("{")]
    return json.loads(lines[-1]) if lines else {}


class InitOnAPreLockBrain(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory(prefix="vl_prelock_init_")
        box = Path(cls._tmp.name) / "pkg"
        out = subprocess.run(["git", "ls-files"], cwd=str(ROOT), capture_output=True, text=True, timeout=60)
        if out.returncode != 0:
            raise unittest.SkipTest("git unavailable; cannot build a package box")
        for rel in [p for p in out.stdout.split("\n") if p.strip()]:
            if (ROOT / rel).is_file():
                (box / rel).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(ROOT / rel, box / rel)
        cls.box = box
        cls.empty = Path(cls._tmp.name) / "empty_home"
        cls.empty.mkdir()

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def env(self, absent):
        e = {k: v for k, v in os.environ.items() if k not in ("WIZARD_EMBED_MODEL", "WIZARD_LIBRARY_DB")}
        e.update({"CUDA_VISIBLE_DEVICES": "", "PYTHONDONTWRITEBYTECODE": "1", "HF_HUB_OFFLINE": "1",
                  "TRANSFORMERS_OFFLINE": "1"})
        if absent:
            e.update({"HOME": str(self.empty), "HF_HOME": str(self.empty / "hf")})
            e.pop("HF_HUB_CACHE", None)
            e.pop("HUGGINGFACE_HUB_CACHE", None)
        return e

    def engine(self, absent, *argv, tier=None):
        e = self.env(absent)
        if tier:
            e["WIZARD_EMBED_MODEL"] = tier
        return subprocess.run([sys.executable, "organs/claude_memory.py", *argv], cwd=str(self.box),
                              capture_output=True, text=True, env=e, timeout=300)

    def prelock_brain(self):
        for f in list(self.box.iterdir()):
            if f.name.startswith(("claude_memory.db", "embedder.lock")):
                f.unlink()
        r = self.engine(True, "init", tier="nomic")
        self.assertEqual(r.returncode, 0, r.stderr[-1500:])
        for f in list(self.box.iterdir()):
            if f.name.startswith("embedder.lock"):
                f.unlink()                       # the brain now predates the lock, as every older install does
        self.assertFalse((self.box / "embedder.lock.json").exists())

    def test_absent_init_locks_and_the_first_embed_names_the_public_fetch_verb(self):
        self.prelock_brain()
        r = self.engine(True, "init")
        self.assertEqual(r.returncode, 0, r.stderr[-1500:])
        self.assertTrue((self.box / "embedder.lock.json").exists())
        s = self.engine(True, "store", "--content", "tides follow the moon", "--category", "insight")
        self.assertNotEqual(s.returncode, 0)
        env = _last_json(s.stdout)
        self.assertEqual(env.get("error"), "SNAPSHOT_ABSENT", s.stdout[-800:] + s.stderr[-800:])
        said = json.dumps(env) + s.stderr
        self.assertIn("python3 organs/model_fetch.py --db", env.get("message", ""))
        self.assertTrue(any("organs/model_fetch.py" in x for x in env.get("retry", [])), env.get("retry"))
        self.assertTrue((self.box / "organs" / "model_fetch.py").is_file())
        self.assertNotIn("memory install", said)      # the retry once named a command this repository never shipped

    def test_present_init_locks_and_the_bytes_on_disk_hash_to_the_lock(self):
        import embedder_lock as el
        cpu = el.catalogue_tiers()["cpu"]
        if el.snapshot_dir_for(cpu["model_id"], cpu["revision"]) is None:
            self.skipTest("the cpu snapshot is not on this host's disk (the ABSENT case covers this host)")
        self.prelock_brain()
        r = self.engine(False, "init")
        self.assertEqual(r.returncode, 0, r.stderr[-1500:])
        lock = el.read_lock(self.box / el.LOCK_NAME)
        self.assertEqual((lock["tier"], lock["weights_hash"]), ("cpu", cpu["weights_hash"]))
        _snap, computed = el.verify_snapshot_against_lock(lock, allow_fetch=False)
        self.assertEqual(computed, lock["weights_hash"])


if __name__ == "__main__":
    unittest.main()
