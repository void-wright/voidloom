"""Every component that picks a vector table reads the install lock FIRST.

THE CLAIM UNDER TEST
  `voidloom tier set` moves the install lock and the three metadata rows
  (`active_embedding_table/_model/_dim`) in one transaction. If the process dies
  after the lock's rename and before the COMMIT, the lock names the NEW table while
  the rows still name the OLD one, until the same `tier set` is run again. A reader
  that trusts the rows in that window searches another model's table: for
  jina -> qwen both tables are 1024 wide, so nothing errors and recall is silently
  wrong. So every reader resolves lock-first (organs/embed_resolver.py), and reads
  the rows only for a brain with no lock (every brain installed before the lock,
  and the jina tier, which the shipped catalogue does not carry).

The readers covered, each a control/saboteur pair (the names carry the pairing):
  dream_pipeline._detect_embed_table, memory_dreaming._detect_embedding_table,
  recall_diversity._active_embedding_table, hybrid_retrieval._active_embedding_table,
  install/tier1_probe.db_facts, voidloom doctor's embedding coherence, and
  embedding_upgrade stats.

No model is loaded; the tables are plain BLOB tables (the readers resolve a NAME;
sqlite-vec is not needed to ask which table).
"""
from __future__ import annotations

import importlib
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for _p in (ROOT / "organs", ROOT / "install", ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

NOMIC = ("memory_embeddings", 768, "nomic-ai/nomic-embed-text-v1.5")
JINA = ("memory_embeddings_v2", 1024, "jinaai/jina-embeddings-v5-text-small")
QWEN = ("memory_embeddings_v3", 1024, "Qwen/Qwen3-Embedding-0.6B")


def make_brain(d: Path, rows, lock_tier=None) -> Path:
    """A brain whose metadata rows name `rows` (a (table, dim, model) triple, or None
    for no rows), with one vector in each tier's table at that table's width, and an
    install lock for `lock_tier` ('cpu' | 'gpu') or none."""
    db = d / "claude_memory.db"
    c = sqlite3.connect(db)
    c.executescript(
        "CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT, updated_at TEXT);"
        "CREATE TABLE memories (id INTEGER PRIMARY KEY, content TEXT, is_archived INTEGER DEFAULT 0);"
        "INSERT INTO memories(id, content) VALUES (1, 'a'), (2, 'b');")
    for table, dim, _model in (NOMIC, JINA, QWEN):
        c.execute(f"CREATE TABLE {table} (memory_id INTEGER PRIMARY KEY, embedding BLOB)")
        c.execute(f"INSERT INTO {table} VALUES (1, ?)", (b"\0" * 4 * dim,))
    if rows is not None:
        table, dim, model = rows
        c.executemany("INSERT INTO metadata(key, value) VALUES (?, ?)",
                      [("active_embedding_table", table), ("active_embedding_model", model),
                       ("active_embedding_dim", str(dim))])
    c.commit()
    c.close()
    if lock_tier:
        import embedder_lock
        embedder_lock.write_lock(d, lock_tier)
    return db


def malform_lock(d: Path) -> None:
    lp = d / "embedder.lock.json"
    lock = json.loads(lp.read_text())
    lock["vec_table"] = "memory_embeddings_v9"
    lp.write_text(json.dumps(lock))


class ReadersLockFirst(unittest.TestCase):
    def setUp(self):
        self._env = os.environ.pop("WIZARD_EMBED_MODEL", None)
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()
        if self._env is not None:
            os.environ["WIZARD_EMBED_MODEL"] = self._env

    # ---- the table each reader picks, as one dict, so a pair compares like with like
    def picks(self, db: Path) -> dict:
        conn = sqlite3.connect(db)
        out = {}

        def ask(name, fn):
            try:
                out[name] = fn()
            except Exception as e:  # a typed refusal is an answer too
                out[name] = ("REFUSED", getattr(e, "code", type(e).__name__))

        try:
            ask("dream_pipeline", lambda: importlib.import_module("dream_pipeline")._detect_embed_table(conn)[0])
            ask("memory_dreaming", lambda: importlib.import_module("memory_dreaming")._detect_embedding_table(conn)[0])
            ask("recall_diversity", lambda: importlib.import_module("recall_diversity")._active_embedding_table(conn))
            ask("hybrid_retrieval", lambda: importlib.import_module("hybrid_retrieval")._active_embedding_table(conn))
            ask("tier1_probe", lambda: importlib.import_module("tier1_probe").db_facts(str(db)).get("active_embedding_table")
                or importlib.import_module("tier1_probe").db_facts(str(db)).get("error", "")[:14])

            def doctor():
                import voidloom.cli as cli
                r = cli._embedding_coherence(str(db))
                if (r.get("reason") or "").startswith("LOCK_"):
                    return r["reason"][:14]
                return r.get("resolved_table") or r.get("recorded_table")
            ask("doctor", doctor)

            def stats():
                eu = importlib.import_module("embedding_upgrade")

                class A:
                    db_path = str(db)
                r = eu.cmd_stats(A())
                return r["active_table"] if not r.get("lock_error") else r["lock_error"][:14]
            ask("stats", stats)
        finally:
            conn.close()
        return out

    def test_control_lock_and_rows_agree_every_reader_picks_that_table(self):
        db = make_brain(self.dir, QWEN, lock_tier="gpu")
        self.assertEqual(set(self.picks(db).values()), {"memory_embeddings_v3"}, self.picks(db))

    def test_saboteur_lock_moved_rows_did_not_every_reader_still_picks_the_lock(self):
        # the crash-after-rename window: lock = qwen, rows = nomic
        db = make_brain(self.dir, NOMIC, lock_tier="gpu")
        got = self.picks(db)
        self.assertEqual(set(got.values()), {"memory_embeddings_v3"}, got)

    def test_saboteur_lock_nomic_rows_jina_same_story_the_other_way(self):
        db = make_brain(self.dir, JINA, lock_tier="cpu")
        got = self.picks(db)
        self.assertEqual(set(got.values()), {"memory_embeddings"}, got)

    def test_control_no_lock_jina_rows_are_read_from_metadata(self):
        # a brain installed before the lock, on the tier the catalogue does not ship
        db = make_brain(self.dir, JINA)
        got = self.picks(db)
        self.assertEqual(set(got.values()), {"memory_embeddings_v2"}, got)

    def test_saboteur_no_lock_qwen_rows_are_not_read_as_nomic(self):
        # dream_pipeline used to answer two ways (jina or nomic): a qwen brain read the nomic table
        db = make_brain(self.dir, QWEN)
        got = self.picks(db)
        self.assertEqual(set(got.values()), {"memory_embeddings_v3"}, got)

    def test_saboteur_a_malformed_lock_refuses_and_no_reader_substitutes_a_table(self):
        db = make_brain(self.dir, NOMIC, lock_tier="cpu")
        malform_lock(self.dir)
        got = self.picks(db)
        self.assertEqual(got["dream_pipeline"], ("REFUSED", "LOCK_MALFORMED"))
        self.assertEqual(got["memory_dreaming"], ("REFUSED", "LOCK_MALFORMED"))
        self.assertIsNone(got["recall_diversity"], "MMR must drop its signal, not read another table")
        self.assertIsNone(got["hybrid_retrieval"], "the vector arm turns off; it never searches the jina table")
        self.assertEqual(got["tier1_probe"], "LOCK_MALFORMED")
        self.assertEqual(got["doctor"], "LOCK_MALFORMED")
        self.assertEqual(got["stats"], "LOCK_MALFORMED")

    def test_control_doctor_says_nothing_when_lock_and_rows_agree(self):
        db = make_brain(self.dir, NOMIC, lock_tier="cpu")
        import voidloom.cli as cli
        r = cli._embedding_coherence(str(db))
        self.assertEqual(r["table_source"], "lock")
        self.assertNotIn("metadata_disagrees", r)
        self.assertTrue(r["agrees"], r)

    def test_saboteur_doctor_names_an_interrupted_switch_and_the_command_that_finishes_it(self):
        db = make_brain(self.dir, NOMIC, lock_tier="gpu")
        import voidloom.cli as cli
        r = cli._embedding_coherence(str(db))
        self.assertEqual((r["resolved_table"], r["resolved_dim"]), ("memory_embeddings_v3", 1024))
        self.assertIn("tier set", r["metadata_disagrees"])
        self.assertEqual(r["observed_dim"], 1024)
        self.assertTrue(r["agrees"], r)


if __name__ == "__main__":
    unittest.main()
