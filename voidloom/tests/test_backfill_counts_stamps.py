"""`embed_backfill` agrees with `re-embed` about the same brain.

THE CLAIM UNDER TEST
  * on a LOCKED brain the unit of work is the stamp, not the row: an unstamped vector, a
    legacy 'unresolved' stamp, another checksum and an edited memory are all work, and a
    row with a vector is replaced in place rather than failing the INSERT;
  * its count equals memory_write.count_pending_embeddings, and a second run finds nothing;
  * on a brain with NO lock nothing changes: presence is the only fact it has, including
    for jina;
  * a dry run writes nothing, even on a sidecar that needs bringing up.
No model is loaded: a stand-in encoder and the process register's own seam.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from voidloom.tests.test_tier_set import TABLES, _connect, _vec, _vec_available, make_brain, snapshot


def _register(lock):
    import embedder_lock as el
    el.clear_loaded()
    el.register_loaded(model_id=lock["model_id"], revision=lock["revision"], weights_hash=lock["weights_hash"],
                       dim=int(lock["dim"]), vec_table=lock["vec_table"], tier=lock.get("tier"))


@unittest.skipUnless(_vec_available(), "sqlite_vec absent on this host: NOT MEASURED")
class BackfillCountsStamps(unittest.TestCase):
    def setUp(self):
        import embed_backfill
        import embedder_lock as el
        import memory_write as mw
        self.eb, self.el, self.mw = embed_backfill, el, mw
        el.clear_loaded()
        self._td = tempfile.TemporaryDirectory()
        self.dir = Path(self._td.name)

    def tearDown(self):
        self.el.clear_loaded()
        self._td.cleanup()

    def _encoder(self, table, model, dim):
        return mock.patch.object(self.mw, "default_encoder", lambda text, conn=None: _vec(model, text, dim))

    def _stale_locked_brain(self):
        """Nomic, locked, 7 memories (5 active). Three active rows are stale, one each way."""
        db = make_brain(self.dir, "nomic")
        c = _connect(db)
        c.execute("UPDATE memories SET content = 'rewritten' WHERE id = 1")                         # edited
        c.execute("UPDATE embedding_provenance SET weights_hash = 'unresolved' WHERE memory_id = 3")  # legacy
        c.execute("DELETE FROM embedding_provenance WHERE memory_id = 4")                           # unstamped
        c.commit(); c.close()
        return db

    def test_control_a_locked_brain_counts_what_re_embed_counts(self):
        db = self._stale_locked_brain()
        c = _connect(db)
        self.assertEqual(self.eb.count_unembedded(c, "memory_embeddings"), 3)
        self.assertEqual(self.mw.count_pending_embeddings(c), 3)
        c.close()
        env = self.eb.backfill(str(db), dry_run=True)
        self.assertEqual((env["code"], env["unembedded_before"], env["would_embed"]), ("backfill_plan", 3, 3), env)

    def test_saboteur_stale_rows_are_replaced_in_place_not_refused_by_the_insert(self):
        db = self._stale_locked_brain()
        _register(self.el.resolve(db))
        table, dim, model, _cat = TABLES["nomic"]
        with self._encoder(table, model, dim):
            env = self.eb.backfill(str(db))
        self.assertEqual((env["status"], env["embedded"], env["failed"], env["unembedded_after"]),
                         ("ok", 3, 0, 0), env)
        c = _connect(db)
        self.assertEqual(self.mw.count_pending_embeddings(c), 0)
        blob = c.execute("SELECT embedding FROM memory_embeddings WHERE memory_id = 1").fetchone()[0]
        self.assertEqual(bytes(blob), _vec(model, "rewritten", dim), "the vector was replaced, not kept")
        want = self.el.resolve(db)["weights_hash"]
        self.assertEqual(c.execute("SELECT COUNT(*) FROM embedding_provenance WHERE memory_id IN (1, 3, 4) "
                                   "AND weights_hash = ?", (want,)).fetchone()[0], 3)
        c.close()
        with self._encoder(table, model, dim):
            again = self.eb.backfill(str(db))
        self.assertEqual((again["code"], again["embedded"]), ("backfill_nothing_to_do", 0))

    def test_saboteur_a_failed_row_is_tagged_and_keeps_its_old_vector(self):
        db = self._stale_locked_brain()
        _register(self.el.resolve(db))
        table, dim, model, _cat = TABLES["nomic"]
        good = lambda text, conn=None: (_ for _ in ()).throw(RuntimeError("no")) if text == "rewritten" \
            else _vec(model, text, dim)
        before = snapshot(db, table)[0]
        with mock.patch.object(self.mw, "default_encoder", good):
            env = self.eb.backfill(str(db))
        self.assertEqual((env["status"], env["embedded"], env["failed"]), ("incomplete", 2, 1), env)
        c = _connect(db)
        self.assertEqual(c.execute("SELECT tag FROM memory_tags WHERE memory_id = 1").fetchall(),
                         [(self.mw.MISSING_TAG,)])
        c.close()
        self.assertEqual(dict(snapshot(db, table)[0])[1], dict(before)[1], "the old vector survives")

    def test_control_limit_bounds_the_batch_and_the_rest_stays_queued(self):
        db = self._stale_locked_brain()
        _register(self.el.resolve(db))
        table, dim, model, _cat = TABLES["nomic"]
        with self._encoder(table, model, dim):
            env = self.eb.backfill(str(db), limit=2)
        self.assertEqual((env["embedded"], env["unembedded_after"]), (2, 1), env)

    def test_control_a_brain_with_no_lock_keeps_presence(self):
        for tier in ("nomic", "jina"):
            d = Path(tempfile.mkdtemp(dir=self.dir))
            db = make_brain(d, tier)
            lock = d / "embedder.lock.json"
            if lock.exists():
                lock.unlink()
            table = TABLES[tier][0]
            c = _connect(db)
            c.execute("UPDATE embedding_provenance SET weights_hash = 'unresolved' WHERE memory_id = 1")
            c.execute("UPDATE memories SET content = 'edited' WHERE id = 3")
            c.execute(f"DELETE FROM {table} WHERE memory_id = 4")
            c.commit()
            self.assertEqual(self.eb.count_unembedded(c, table), 1, f"{tier}: only the row with no vector")
            c.close()

    def test_control_dry_run_on_an_old_sidecar_counts_all_and_writes_nothing(self):
        db = make_brain(self.dir, "nomic")
        c = _connect(db)
        c.execute("DROP TABLE embedding_provenance")
        c.commit(); c.close()
        env = self.eb.backfill(str(db), dry_run=True)
        self.assertEqual((env["code"], env["would_embed"]), ("backfill_plan", 5), env)
        c = _connect(db)
        self.assertEqual(c.execute("SELECT COUNT(*) FROM sqlite_master WHERE name = 'embedding_provenance'")
                         .fetchone()[0], 0)
        c.close()


if __name__ == "__main__":
    unittest.main()
