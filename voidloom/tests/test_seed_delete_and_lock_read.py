"""Two small truths: a deleted seed row leaves no vector behind, and a lock that cannot be read is not absent.

THE CLAIM UNDER TEST
  * seed delete-one removes the row's vector from EVERY tier's table: after `tier set` the old
    tier keeps one otherwise, with its stamp gone so nothing counts it;
  * a row something still names is refused, typed, with nothing removed, not left dangling;
  * a lock path that exists and cannot be read refuses LOCK_MALFORMED: read as absent, a locked
    brain would drop to the lock-less rule and search its whole table.
"""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from voidloom.tests.test_tier_set import _connect, _vec, _vec_available

ORIGIN = "seed:1#abc"


@unittest.skipUnless(_vec_available(), "sqlite_vec absent on this host: NOT MEASURED")
class SeedDelete(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.db = Path(self._td.name) / "claude_memory.db"
        c = _connect(self.db)
        c.executescript("""
            CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE memories (id INTEGER PRIMARY KEY, content TEXT, seed INTEGER, origin TEXT, is_archived INTEGER DEFAULT 0);
            CREATE TABLE memory_tags (memory_id INTEGER, tag TEXT);
            CREATE TABLE memory_links (source_id INTEGER, target_id INTEGER);
            CREATE TABLE embedding_provenance (memory_id INTEGER, vec_table TEXT);
            CREATE TABLE resurfacing_log (memory_id INTEGER REFERENCES memories(id));
            CREATE VIRTUAL TABLE memory_embeddings USING vec0(memory_id INTEGER PRIMARY KEY, embedding float[768]);
            CREATE VIRTUAL TABLE memory_embeddings_v3 USING vec0(memory_id INTEGER PRIMARY KEY, embedding float[1024]);
        """)
        c.execute("INSERT INTO memories (id, content, seed, origin) VALUES (1, 'a seed', 1, ?)", (ORIGIN,))
        c.execute("INSERT INTO memory_embeddings VALUES (1, ?)", (_vec("m", "a", 768),))
        c.execute("INSERT INTO memory_embeddings_v3 VALUES (1, ?)", (_vec("m", "a", 1024),))
        c.commit(); c.close()

    def tearDown(self):
        self._td.cleanup()

    def _counts(self):
        c = _connect(self.db)
        try:
            return tuple(c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                         for t in ("memories", "memory_embeddings", "memory_embeddings_v3"))
        finally:
            c.close()

    def test_saboteur_the_old_tier_keeps_no_vector_after_a_delete(self):
        import seed_loader
        out = seed_loader.delete_one(str(self.db), ORIGIN)
        self.assertTrue(out["row_gone"])
        self.assertEqual(self._counts(), (0, 0, 0))

    def test_saboteur_a_row_something_names_is_refused_and_nothing_goes(self):
        import seed_loader
        c = _connect(self.db)
        c.execute("INSERT INTO resurfacing_log VALUES (1)")
        c.commit(); c.close()
        with self.assertRaises(seed_loader.SeedLoadError) as cm:
            seed_loader.delete_one(str(self.db), ORIGIN)
        self.assertEqual(cm.exception.code, "HARD_DELETE_REFERENCED")
        self.assertEqual(self._counts(), (1, 1, 1), "nothing was removed")


class LockThatCannotBeRead(unittest.TestCase):
    def test_saboteur_an_unreadable_lock_is_malformed_not_missing(self):
        import embedder_lock as el
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / el.LOCK_NAME
            p.mkdir()                                  # there, and read_bytes raises OSError
            with self.assertRaises(el.EmbedderLockError) as cm:
                el.read_lock(p)
            self.assertEqual(cm.exception.code, "LOCK_MALFORMED")

    def test_control_an_absent_lock_stays_missing(self):
        import embedder_lock as el
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(el.EmbedderLockError) as cm:
                el.read_lock(Path(td) / el.LOCK_NAME)
            self.assertEqual(cm.exception.code, "LOCK_MISSING")


if __name__ == "__main__":
    unittest.main()
