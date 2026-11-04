"""The website store stamps what it writes and reads through a search set.

THE CLAIM UNDER TEST: organs/website_memory.py keeps its own nomic database beside the brain.
  * every vector it writes (store, update, migrate) carries a stamp naming the shipped nomic
    checksum and the text, in the same transaction, whatever tier the BRAIN beside it is on;
  * `init` gives the store its sidecar and writes no lock: the lock in that directory is the
    brain's;
  * recall searches the search set and carries coverage; a row stamped under other weights
    is out of it; a row from before stamps is legacy and stays searchable;
  * a store with memories and nothing to search refuses, typed, rather than answering empty;
  * hard delete takes the stamp with the vector; stats counts stamps; maintain compares only
    vectors in the search set.
No model is loaded.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from voidloom.tests.test_tier_set import _connect, _vec, _vec_available

MODEL = "nomic-ai/nomic-embed-text-v1.5"


def _ns(**k):
    base = dict(category="insight", summary=None, importance=0.5, project=None, context=None, tags=None,
                top_k=5, id=None, content=None, hard=False, threshold=0.0, min_importance=0.0, category_filter=None)
    base.update(k)
    return argparse.Namespace(**base)


@unittest.skipUnless(_vec_available(), "sqlite_vec absent on this host: NOT MEASURED")
class WebsiteStoreStamps(unittest.TestCase):
    def setUp(self):
        import embedder_lock as el
        import memory_write as mw
        import website_memory as wm
        self.wm, self.el, self.mw = wm, el, mw
        self._td = tempfile.TemporaryDirectory()
        self.dir = Path(self._td.name)
        self.db = self.dir / "website_memory.db"
        self.cpu = dict(el.catalogue_tiers()["cpu"], tier="cpu")
        el.clear_loaded()
        el.register_loaded(model_id=self.cpu["model_id"], revision=self.cpu["revision"],
                           weights_hash=self.cpu["weights_hash"], dim=768, vec_table=self.cpu["vec_table"], tier="cpu")
        self.patches = [mock.patch.object(wm, "DB_PATH", self.db),
                        mock.patch.object(wm, "_encode", lambda t, prefix="search_document": _vec(MODEL, t, 768))]
        for p in self.patches:
            p.start()
        self._run(wm.cmd_init, _ns())

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.el.clear_loaded()
        self._td.cleanup()

    def _run(self, fn, ns):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            fn(ns)
        return json.loads(buf.getvalue().strip().splitlines()[-1])

    def _store(self, text):
        return self._run(self.wm.cmd_store, _ns(content=text))["memory_id"]

    def _stamp(self, mid):
        c = _connect(self.db)
        try:
            return c.execute("SELECT vec_table, weights_hash, dim, content_hash, organ, input_kind "
                             "FROM embedding_provenance WHERE memory_id = ?", (mid,)).fetchone()
        finally:
            c.close()

    def test_control_init_gives_the_store_a_sidecar_and_writes_no_lock(self):
        c = _connect(self.db)
        self.assertIn("weights_hash", self.mw._sidecar_columns(c))
        c.close()
        self.assertFalse((self.dir / "embedder.lock.json").exists(), "the lock in this directory is the brain's")

    def test_control_store_stamps_the_shipped_checksum_and_the_text(self):
        mid = self._store("a page about tides")
        self.assertEqual(self._stamp(mid), ("memory_embeddings", self.cpu["weights_hash"], 768,
                                            self.mw.content_hash("a page about tides"), "website_memory.store", "text"))

    def test_saboteur_a_gpu_brain_beside_the_store_changes_nothing(self):
        self.el.write_lock(self.dir, "gpu")
        mid = self._store("still nomic")
        self.assertEqual(self._stamp(mid)[:3], ("memory_embeddings", self.cpu["weights_hash"], 768))
        env = self._run(self.wm.cmd_recall, _ns(query="still nomic"))
        self.assertEqual(env["coverage"]["matched"], 1)

    def test_control_update_restamps_for_the_new_text(self):
        mid = self._store("old words")
        self._run(self.wm.cmd_update, _ns(id=mid, content="new words", summary=None, importance=None,
                                          category=None, tags=None))
        s = self._stamp(mid)
        self.assertEqual((s[3], s[4]), (self.mw.content_hash("new words"), "website_memory.update"))

    def test_control_recall_carries_coverage(self):
        self._store("alpha")
        env = self._run(self.wm.cmd_recall, _ns(query="alpha"))
        self.assertEqual((env["result_count"], env["coverage"]["matched"], env["coverage"]["total"]), (1, 1, 1))

    def test_saboteur_a_row_under_other_weights_leaves_the_search_set(self):
        keep, gone = self._store("keep me"), self._store("gone")
        c = _connect(self.db)
        c.execute("UPDATE embedding_provenance SET weights_hash = ? WHERE memory_id = ?", ("f" * 64, gone))
        c.commit(); c.close()
        env = self._run(self.wm.cmd_recall, _ns(query="gone"))
        self.assertEqual([r["id"] for r in env["results"]], [keep])

    def test_control_a_row_from_before_stamps_stays_searchable(self):
        mid = self._store("legacy page")
        c = _connect(self.db)
        c.execute("DELETE FROM embedding_provenance")
        c.commit(); c.close()
        env = self._run(self.wm.cmd_recall, _ns(query="legacy page"))
        self.assertEqual([r["id"] for r in env["results"]], [mid])
        self.assertEqual(env["coverage"]["legacy_unverified"]["legacy"], 1)

    def test_control_a_store_from_before_the_sidecar_still_recalls(self):
        mid = self._store("old store")
        c = _connect(self.db)
        c.execute("DROP TABLE embedding_provenance")
        c.commit(); c.close()
        env = self._run(self.wm.cmd_recall, _ns(query="old store"))
        self.assertEqual([r["id"] for r in env["results"]], [mid])

    def test_saboteur_memories_with_nothing_to_search_refuse_typed(self):
        mid = self._store("no vector left")
        c = _connect(self.db)
        c.execute("DELETE FROM memory_embeddings WHERE memory_id = ?", (mid,))
        c.commit(); c.close()
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), self.assertRaises(SystemExit):
            self.wm.cmd_recall(_ns(query="x"))
        self.assertIn("NO_ROWS_FOR_LOCK", buf.getvalue())

    def test_control_hard_delete_takes_the_stamp_with_the_vector(self):
        mid = self._store("to be removed")
        self._run(self.wm.cmd_delete, _ns(id=mid, hard=True))
        self.assertIsNone(self._stamp(mid))

    def test_saboteur_stats_counts_stamps_not_presence(self):
        keep, foreign = self._store("one"), self._store("two")
        c = _connect(self.db)
        c.execute("UPDATE embedding_provenance SET weights_hash = ? WHERE memory_id = ?", ("f" * 64, foreign))
        c.commit(); c.close()
        env = self._run(self.wm.cmd_stats, _ns())
        self.assertEqual(env["embedded_memories"], 1)
        self.assertEqual((env["coverage"]["matched"], env["coverage"]["total"]), (1, 2))

    def test_saboteur_maintain_compares_only_the_search_set(self):
        a, b, c3 = self._store("same words"), self._store("same words"), self._store("same words")
        c = _connect(self.db)
        c.execute("UPDATE embedding_provenance SET weights_hash = ? WHERE memory_id = ?", ("f" * 64, b))
        c.commit(); c.close()
        self._run(self.wm.cmd_maintain, _ns(skip_dedup=False))
        c = _connect(self.db)
        archived = dict(c.execute("SELECT id, is_archived FROM memories").fetchall())
        c.close()
        self.assertEqual(archived[b], 0, "a vector in another space is not a duplicate of anything")
        self.assertEqual(sorted(archived.values()), [0, 0, 1], "the two in the search set did dedupe")

    def test_control_migrate_stamps_every_row(self):
        note = self.dir / "note.md"
        note.write_text("x", encoding="utf-8")
        mem = {"content": "a migrated note", "summary": "s", "category": "insight", "importance": 0.5, "tags": []}
        with mock.patch.object(self.wm, "_parse_markdown_memories", return_value=[mem]):
            self._run(self.wm.cmd_migrate, _ns(source=[str(note)]))
        s = self._stamp(1)
        self.assertEqual((s[1], s[3], s[4]), (self.cpu["weights_hash"], self.mw.content_hash("a migrated note"),
                                              "website_memory.migrate"))


if __name__ == "__main__":
    unittest.main()
