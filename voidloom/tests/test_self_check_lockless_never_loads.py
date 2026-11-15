"""install/self_check.py never instantiates an embedder whose bytes it has not checked, on a brain with no lock either.

The lockless branch of the "full L4 models load" check loaded the model BY NAME with its remote code trusted, and hashed
nothing. Now a model the shipped catalogue carries is verified against that entry's checksum, and one it does not carry
is refused, typed, or (jina, pinned in the engine) only looked for:
  * saboteur: with the embedding library replaced by a recorder, the check on a lockless brain constructs nothing,
    whether the cache is empty or the brain names a model nobody ships;
  * saboteur: a name nobody ships is refused LOCK_NOT_SHIPPED, never loaded;
  * control: on this host's cache, a lockless nomic brain verifies against the shipped entry (skipped where the snapshot
    is not on this disk).
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
import json, sys, types
built = []
st = types.ModuleType("sentence_transformers")
class SentenceTransformer:
    def __init__(self, *a, **k):
        built.append(a[0] if a else k.get("model_name_or_path"))
st.SentenceTransformer = SentenceTransformer
sys.modules["sentence_transformers"] = st
sys.path.insert(0, sys.argv[1] + "/install")
import self_check as sc
c = sc.Checks()
sc.check_models(c, sc.Path(sys.argv[2]))
r = [r for r in c.results if r["name"].startswith("full L4 models load")][0]
r["constructed"] = built
print(json.dumps(r))
"""


class TheLocklessBranchLoadsNothingUnchecked(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="vl_sc_lockless_")
        self.brain = Path(self._tmp.name) / "brain"
        self.brain.mkdir()
        self.db = self.brain / "claude_memory.db"

    def tearDown(self):
        self._tmp.cleanup()

    def brain_naming(self, model_id):
        c = sqlite3.connect(str(self.db))
        c.execute("CREATE TABLE memory_embeddings (memory_id INTEGER PRIMARY KEY)")   # the Tier-1 gate's marker
        c.execute("INSERT INTO memory_embeddings VALUES (1)")
        c.execute("CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT)")
        c.execute("INSERT INTO metadata VALUES ('active_embedding_model', ?)", (model_id,))
        c.execute("INSERT INTO metadata VALUES ('active_embedding_table', 'memory_embeddings')")
        c.commit()
        c.close()

    def probe(self, **env):
        e = {k: v for k, v in os.environ.items() if k not in ("VOIDLOOM_RERANKER",)}
        e.update({"HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", "PYTHONDONTWRITEBYTECODE": "1",
                  "VOIDLOOM_RERANKER": "none"}, **env)
        r = subprocess.run([sys.executable, "-c", _PROBE, str(ROOT), str(self.db)], capture_output=True, text=True,
                           env=e, timeout=600)
        self.assertEqual(r.returncode, 0, r.stderr[-1500:])
        return json.loads(r.stdout.strip().splitlines()[-1])

    def test_saboteur_an_empty_cache_is_refused_and_nothing_is_constructed(self):
        self.brain_naming("nomic-ai/nomic-embed-text-v1.5")
        empty = Path(self._tmp.name) / "empty_hub"
        empty.mkdir()
        res = self.probe(HF_HUB_CACHE=str(empty))
        self.assertEqual(res["constructed"], [], res)
        self.assertFalse(res["ok"], res["detail"])
        self.assertIn("SNAPSHOT_ABSENT", res["detail"])

    def test_saboteur_a_model_nobody_ships_is_refused_typed_and_never_loaded(self):
        self.brain_naming("acme/unshipped-embedder")
        res = self.probe()
        self.assertEqual(res["constructed"], [], res)
        self.assertFalse(res["ok"], res["detail"])
        self.assertIn("LOCK_NOT_SHIPPED", res["detail"])

    def test_saboteur_the_hosts_nomic_snapshot_verifies_against_the_shipped_entry_constructing_nothing(self):
        import embedder_lock as el
        cpu = el.catalogue_tiers()["cpu"]
        if el.snapshot_dir_for(cpu["model_id"], cpu["revision"]) is None:
            self.skipTest("the cpu snapshot is not on this host's disk")
        self.brain_naming(cpu["model_id"])
        res = self.probe()
        self.assertEqual(res["constructed"], [], res)
        self.assertTrue(res["ok"], res["detail"])
        self.assertIn("verifies against the shipped catalogue entry", res["detail"])


if __name__ == "__main__":
    unittest.main()
