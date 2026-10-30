"""Two stdlib readers count THIS brain's vector table, chosen lock-first, on a stdlib connection.

`claude_memory_lite.py stats` and `install/self_check.py`'s "is there anything to verify" probe both
counted the nomic table by name, whatever the brain's tier, and both asked the vec0 table itself, which
a stdlib connection cannot read (`no such module: vec0`). Measured on a copy of a 31,904-memory brain
holding 28,063 vectors: lite reported `embedded_memories: None`, "not present (Tier 0 / keyword-only)".
They now take the table from `embed_resolver.resolve` (the install lock first) and count it through
`embed_resolver.stored_vector_count` (sqlite-vec's ordinary `<table>_rowids` shadow table).
  * lite control: a lockless nomic brain reports its vectors and "active";
  * lite saboteur: a qwen-locked brain reports the qwen table's count, never the nomic table's;
  * lite saboteur: a malformed lock with vector tables present is "unknown", never "Tier 0";
  * lite control: a bare Tier-0 brain is still "not present";
  * self-check: the probe skips only when THIS brain's table is empty, and runs when it holds vectors.
"""
import json
import os
import sqlite3
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(os.environ.get("VOIDLOOM_ROOT") or Path(__file__).resolve().parents[2]).resolve()
sys.path.insert(0, str(ROOT / "organs"))

try:
    import sqlite_vec  # noqa: F401
    HAVE_VEC = True
except Exception:                                   # a Tier-0 interpreter cannot build the fixtures
    HAVE_VEC = False

_SC_PROBE = r"""
import json, sys
sys.path.insert(0, sys.argv[1] + "/install")
import self_check as sc
c = sc.Checks()
sc.check_models(c, sc.Path(sys.argv[2]))
print(json.dumps({"names": [r["name"] for r in c.results],
                  "notes": [str(n) for n in getattr(c, "notes", [])]}))
"""


def _vec_brain(db: Path, tables: dict, metadata_table=None) -> None:
    """A brain with `n` memories and, per vec0 table, the first k of them embedded."""
    import sqlite_vec
    c = sqlite3.connect(str(db))
    c.enable_load_extension(True)
    sqlite_vec.load(c)
    c.execute("CREATE TABLE memories (id INTEGER PRIMARY KEY, content TEXT, summary TEXT, category TEXT, "
              "kind TEXT, created_at TEXT, is_archived INTEGER DEFAULT 0)")
    c.execute("CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT)")
    for i in range(1, 7):
        c.execute("INSERT INTO memories(id, content, summary, category, kind, created_at) "
                  "VALUES (?, ?, ?, 'insight', 'semantic', '2026-10-30')", (i, f"memory {i}", f"m{i}"))
    if metadata_table:
        c.execute("INSERT INTO metadata VALUES ('active_embedding_table', ?)", (metadata_table,))
    dims = {"memory_embeddings": 768, "memory_embeddings_v2": 1024, "memory_embeddings_v3": 1024}
    for t, k in tables.items():
        c.execute(f"CREATE VIRTUAL TABLE {t} USING vec0(memory_id INTEGER PRIMARY KEY, embedding float[{dims[t]}])")
        for i in range(1, k + 1):
            c.execute(f"INSERT INTO {t}(memory_id, embedding) VALUES (?, ?)",
                      (i, struct.pack(f"{dims[t]}f", *([0.0] * (dims[t] - 1) + [1.0]))))
    c.commit()
    c.close()


@unittest.skipUnless(HAVE_VEC, "sqlite_vec is not importable here: the vec0 fixtures cannot be built")
class LiteStatsCountsThisBrainsTable(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="vl_lite_count_")
        self.brain = Path(self._tmp.name) / "brain"
        self.brain.mkdir()
        self.db = self.brain / "claude_memory.db"

    def tearDown(self):
        self._tmp.cleanup()

    def stats(self):
        env = {k: v for k, v in os.environ.items() if k not in ("WIZARD_EMBED_MODEL",)}
        env.update({"WIZARD_LIBRARY_DB": str(self.db), "HOME": self._tmp.name})
        r = subprocess.run([sys.executable, str(ROOT / "organs" / "claude_memory_lite.py"), "stats"],
                           capture_output=True, text=True, env=env, timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr[-1500:])
        return json.loads(r.stdout.strip().splitlines()[-1])

    def test_control_a_lockless_nomic_brain_reports_its_vectors(self):
        _vec_brain(self.db, {"memory_embeddings": 4}, metadata_table="memory_embeddings")
        out = self.stats()
        self.assertEqual((out["embedded_memories"], out["embedding_table"], out["semantic_tier"]),
                         (4, "memory_embeddings", "active"))

    def test_saboteur_a_qwen_locked_brain_is_never_read_from_the_nomic_table(self):
        import embedder_lock
        _vec_brain(self.db, {"memory_embeddings": 5, "memory_embeddings_v3": 2},
                   metadata_table="memory_embeddings")          # a stale row: the lock wins
        embedder_lock.write_lock(self.brain, "gpu")
        out = self.stats()
        self.assertEqual((out["embedded_memories"], out["embedding_table"]), (2, "memory_embeddings_v3"))

    def test_saboteur_a_malformed_lock_is_unknown_never_tier0(self):
        _vec_brain(self.db, {"memory_embeddings": 3}, metadata_table="memory_embeddings")
        (self.brain / "embedder.lock.json").write_text("{not json", encoding="utf-8")
        out = self.stats()
        self.assertIsNone(out["embedded_memories"])
        self.assertTrue(out["semantic_tier"].startswith("unknown: LOCK_MALFORMED"), out["semantic_tier"])

    def test_control_a_bare_tier0_brain_is_still_not_present(self):
        _vec_brain(self.db, {})
        out = self.stats()
        self.assertEqual((out["embedded_memories"], out["semantic_tier"]),
                         (0, "not present (Tier 0 / keyword-only)"))


@unittest.skipUnless(HAVE_VEC, "sqlite_vec is not importable here: the vec0 fixtures cannot be built")
class SelfCheckSkipsOnlyWhenThisBrainsTableIsEmpty(unittest.TestCase):
    """The probe's "nothing to verify" branch asks THIS brain's table (a qwen lock: the qwen table)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="vl_sc_count_")
        self.brain = Path(self._tmp.name) / "brain"
        self.brain.mkdir()
        self.db = self.brain / "claude_memory.db"
        self.empty_hub = Path(self._tmp.name) / "empty_hub"
        self.empty_hub.mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def probe(self):
        e = {k: v for k, v in os.environ.items() if k not in ("VOIDLOOM_RERANKER",)}
        e.update({"HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", "HF_HUB_CACHE": str(self.empty_hub),
                  "PYTHONDONTWRITEBYTECODE": "1"})
        r = subprocess.run([sys.executable, "-c", _SC_PROBE, str(ROOT), str(self.db)], capture_output=True,
                           text=True, env=e, timeout=600)
        self.assertEqual(r.returncode, 0, r.stderr[-1500:])
        return json.loads(r.stdout.strip().splitlines()[-1])

    def test_saboteur_an_empty_qwen_table_skips_even_when_the_nomic_table_holds_vectors(self):
        import embedder_lock
        _vec_brain(self.db, {"memory_embeddings": 3, "memory_embeddings_v3": 0})
        embedder_lock.write_lock(self.brain, "gpu")
        res = self.probe()
        self.assertFalse(any(n.startswith("full L4 models load") for n in res["names"]), res)
        self.assertTrue(any("holds 0 vectors" in n for n in res["notes"]), res)

    def test_control_a_qwen_table_with_vectors_is_verified(self):
        import embedder_lock
        _vec_brain(self.db, {"memory_embeddings": 0, "memory_embeddings_v3": 2})
        embedder_lock.write_lock(self.brain, "gpu")
        res = self.probe()
        self.assertTrue(any(n.startswith("full L4 models load") for n in res["names"]), res)


if __name__ == "__main__":
    unittest.main()
