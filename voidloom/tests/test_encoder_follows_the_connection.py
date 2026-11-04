"""The encoder follows the connection, not the module's DB_PATH.

A process can hold a connection to a brain other than the one `claude_memory.DB_PATH`
names: a restored copy, a second data dir. Width catches a mismatch only when the two
tiers differ in width; nomic-vs-qwen does, jina-vs-qwen does not, and a qwen vector in
a jina table is stamped 'unresolved' and never noticed. The brain beside the connection
decides, on every kind of brain. No model loads: the encoders are recorded, not run.
"""
from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "organs"))

import claude_memory as cm  # noqa: E402
import embedder_lock as el  # noqa: E402
import embedding_upgrade as eu  # noqa: E402
import memory_write as mw  # noqa: E402


def _brain(d: Path, *, lock=None, table=None):
    d.mkdir(parents=True, exist_ok=True)
    db = d / "claude_memory.db"
    c = sqlite3.connect(str(db))
    c.execute("CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT)")
    if table:
        c.execute("INSERT INTO metadata VALUES ('active_embedding_table', ?)", (table,))
    c.commit()
    if lock:
        el.write_lock(d, lock)
    return db, c


class _Two(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.d = Path(self.tmp.name)
        self.seen = []

        def fake_encode(text, prefix="search_document", *, lock=None):
            self.seen.append(lock)
            return b"vector"
        patches = [mock.patch.object(cm, "_encode", fake_encode)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def at_db_path(self, **kw):
        db, c = _brain(self.d / "module", **kw)
        c.close()
        p = mock.patch.object(cm, "DB_PATH", db)
        p.start()
        self.addCleanup(p.stop)

    def conn(self, **kw):
        _db, c = _brain(self.d / "other", **kw)
        self.addCleanup(c.close)
        return c


class ALockedConnection(_Two):
    def test_control_the_connections_lock_is_handed_to_the_encoder(self):
        self.at_db_path(lock="cpu")
        c = self.conn(lock="gpu")
        self.assertEqual(mw.default_encoder("text", c), b"vector")
        self.assertEqual(self.seen[0]["weights_hash"], el.catalogue_tiers()["gpu"]["weights_hash"])

    def test_saboteur_a_module_brain_of_another_tier_does_not_win(self):
        self.at_db_path(table="memory_embeddings_v2")
        c = self.conn(lock="cpu")
        mw.default_encoder("text", c)
        self.assertEqual(self.seen[0]["vec_table"], "memory_embeddings")

    def test_control_no_process_level_cache_is_set(self):
        self.at_db_path(lock="cpu")
        c = self.conn(lock="gpu")
        before = dict(cm._active_model_cache)
        mw.default_encoder("text", c)
        self.assertEqual(cm._active_model_cache, before)


class APreLockConnection(_Two):
    def test_control_a_pre_lock_brain_resolves_to_the_shipped_entry_for_its_tier(self):
        self.at_db_path(lock="cpu")
        c = self.conn(table="memory_embeddings_v3")
        mw.default_encoder("text", c)
        self.assertEqual(self.seen[0]["weights_hash"], el.catalogue_tiers()["gpu"]["weights_hash"])
        self.assertIn("pre-lock", self.seen[0]["_source"])


class AJinaConnection(_Two):
    def test_control_jina_never_touches_the_lock_path_and_keeps_its_encoder(self):
        self.at_db_path(lock="gpu")                  # the same width: the case width cannot catch
        c = self.conn(table="memory_embeddings_v2")
        with mock.patch.object(eu, "encode_jina_document", lambda text: b"jina") as _f, \
                mock.patch.object(eu, "brain_lock", side_effect=AssertionError("the lock path was touched")):
            self.assertEqual(mw.default_encoder("text", c), b"jina")
        self.assertEqual(self.seen, [])

    def test_saboteur_an_unmapped_table_is_refused_never_guessed(self):
        self.at_db_path(lock="cpu")
        c = self.conn(table="memory_embeddings_v9")
        with mock.patch.object(mw, "_resolve_tier", lambda _c: ("memory_embeddings_v9", 1024)):
            with self.assertRaises(mw.EmbeddingUnavailable):
                mw.default_encoder("text", c)


class NoConnection(_Two):
    def test_control_a_caller_with_no_connection_keeps_the_module_answer(self):
        self.at_db_path(lock="cpu")
        mw.default_encoder("text")
        self.assertEqual(self.seen, [None])


if __name__ == "__main__":
    unittest.main()
