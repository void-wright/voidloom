"""install/self_check.py's "full L4 models load" check verifies the INSTALL LOCK, not a model name.

The installer fetches the embedder at the lock's pins, so the check's old by-name load (local_files_only, which
resolves the hub's moving `main` ref) failed on a clean install whose cache held exactly the bytes the lock promised.
With a lock beside the brain the check hashes those bytes against the lock and instantiates nothing:
  * control: this host's snapshot verifies against a cpu lock (skipped where the snapshot is not on disk);
  * saboteur: an empty HF cache fails the check, and its fix names the public fetch verb;
  * saboteur: a malformed lock fails the check -- it is never read as "no lock, try the name".
"""
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(os.environ.get("VOIDLOOM_ROOT") or Path(__file__).resolve().parents[2]).resolve()
sys.path.insert(0, str(ROOT / "organs"))

_PROBE = r"""
import json, sys
sys.path.insert(0, sys.argv[1] + "/install")
import self_check as sc
c = sc.Checks()
sc.check_models(c, sc.Path(sys.argv[2]))
print(json.dumps([r for r in c.results if r["name"].startswith("full L4 models load")][0]))
"""


class SelfCheckVerifiesTheLock(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="vl_sc_lock_")
        self.brain = Path(self._tmp.name) / "brain"
        self.brain.mkdir()
        self.db = self.brain / "claude_memory.db"
        c = sqlite3.connect(str(self.db))
        c.execute("CREATE TABLE memory_embeddings (memory_id INTEGER PRIMARY KEY)")   # the Tier-1 gate's marker
        c.commit()
        c.close()

    def tearDown(self):
        self._tmp.cleanup()

    def probe(self, **env):
        e = {k: v for k, v in os.environ.items() if k not in ("VOIDLOOM_RERANKER",)}
        e.update({"CUDA_VISIBLE_DEVICES": "", "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1",
                  "PYTHONDONTWRITEBYTECODE": "1"}, **env)
        r = subprocess.run([sys.executable, "-c", _PROBE, str(ROOT), str(self.db)], capture_output=True, text=True,
                           env=e, timeout=600)
        self.assertEqual(r.returncode, 0, r.stderr[-1500:])
        return json.loads(r.stdout.strip().splitlines()[-1])

    def test_control_the_hosts_snapshot_verifies_against_a_cpu_lock(self):
        import embedder_lock as el
        cpu = el.catalogue_tiers()["cpu"]
        if el.snapshot_dir_for(cpu["model_id"], cpu["revision"]) is None:
            self.skipTest("the cpu snapshot is not on this host's disk")
        el.write_lock(self.brain, "cpu")
        res = self.probe()
        self.assertTrue(res["ok"], res["detail"])
        self.assertIn("verifies against the install lock", res["detail"])

    def test_saboteur_an_empty_cache_fails_and_names_the_fetch_verb(self):
        import embedder_lock as el
        el.write_lock(self.brain, "cpu")
        empty = Path(self._tmp.name) / "empty_hub"
        empty.mkdir()
        res = self.probe(HF_HUB_CACHE=str(empty))
        self.assertFalse(res["ok"], res["detail"])
        self.assertIn("organs/model_fetch.py", res["fix"])

    def test_saboteur_a_malformed_lock_fails_and_is_never_read_as_absent(self):
        (self.brain / "embedder.lock.json").write_text("{not json", encoding="utf-8")
        res = self.probe()
        self.assertFalse(res["ok"], res["detail"])
        self.assertIn("LOCK_MALFORMED", res["detail"])


if __name__ == "__main__":
    unittest.main()
