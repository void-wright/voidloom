"""A consolidated memory keeps its centroid when vectors change.

THE CLAIM UNDER TEST: a consolidated memory's vector is the average of its members'. Its text
is a template, and a text vector buries it (measured: top 10 for 62 of 240 member queries where
the centroid gave 235). So:
  * a centroid stamped under the lock is not work, for `re-embed`, `tier set` or the backfill;
  * `tier set` rebuilds it in the target table from the members' target vectors and stamps it
    as a centroid, the members first;
  * `re-embed` and the backfill rebuild a legacy consolidated row from verified members;
  * a member that is gone leaves a text vector, the only one still possible, and coverage still
    reaches 100%.
No model is loaded.
"""
from __future__ import annotations

import json
import struct
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from voidloom.tests.test_tier_set import (TABLES, _connect, _vec, _vec_available, fake_encode, fake_load, make_brain,
                                          snapshot)

CONS_ID = 8


def _consolidate(db: Path, tier: str, members=(1, 3, 4), stamp=True):
    """Row 8 is a consolidated memory over `members`, with their average as its vector."""
    import embedder_lock as el
    import memory_write as mw
    from sleep_loop import _centroid_embedding
    table, dim, model, _cat = TABLES[tier]
    c = _connect(db)
    c.execute("ALTER TABLE memories ADD COLUMN source_project TEXT")
    c.execute("ALTER TABLE memories ADD COLUMN derived_from TEXT")
    c.execute("INSERT INTO memories (id, content, source_project, derived_from) VALUES (?, ?, ?, ?)",
              (CONS_ID, "Distilled from 3 episodic memories.", "sleep-loop-consolidation", json.dumps(list(members))))
    vecs = [bytes(c.execute(f"SELECT embedding FROM {table} WHERE memory_id = ?", (m,)).fetchone()[0]) for m in members]
    c.execute(f"INSERT INTO {table} (memory_id, embedding) VALUES (?, ?)", (CONS_ID, _centroid_embedding(vecs)))
    if stamp:
        mw.stamp_centroid(c, CONS_ID, table, dim, list(members), vecs, "sleep_loop.consolidate_episodics")
    c.commit(); c.close()


def _row(db, table):
    c = _connect(db)
    try:
        v = c.execute(f"SELECT embedding FROM {table} WHERE memory_id = ?", (CONS_ID,)).fetchone()
        s = c.execute("SELECT input_kind, inputs, weights_hash, organ FROM embedding_provenance "
                      "WHERE memory_id = ? AND vec_table = ?", (CONS_ID, table)).fetchone()
        return (bytes(v[0]) if v else None), s
    finally:
        c.close()


def _expected_centroid(db, table, members):
    from sleep_loop import _centroid_embedding
    c = _connect(db)
    try:
        return _centroid_embedding([bytes(c.execute(f"SELECT embedding FROM {table} WHERE memory_id = ?", (m,))
                                          .fetchone()[0]) for m in sorted(members)])
    finally:
        c.close()


@unittest.skipUnless(_vec_available(), "sqlite_vec absent on this host: NOT MEASURED")
class CentroidSurvives(unittest.TestCase):
    def setUp(self):
        import embed_backfill
        import embedder_lock as el
        import embedding_upgrade as eu
        import memory_write as mw
        import tier_set
        self.el, self.eu, self.mw, self.ts, self.eb = el, eu, mw, tier_set, embed_backfill
        el.clear_loaded()
        self._seams = (tier_set._load, tier_set._encode)
        tier_set._load, tier_set._encode = fake_load, fake_encode
        self._td = tempfile.TemporaryDirectory()
        self.dir = Path(self._td.name)

    def tearDown(self):
        self.ts._load, self.ts._encode = self._seams
        self.el.clear_loaded()
        self._td.cleanup()

    def _pending(self, db, tier):
        lock = self.ts.target_lock(tier)
        c = self.ts._open(db, readonly=True)
        try:
            return self.ts.pending_ids(c, lock)
        finally:
            c.close()

    def test_control_a_stamped_centroid_is_not_work_for_re_embed(self):
        db = make_brain(self.dir, "nomic")
        _consolidate(db, "nomic")
        self.assertEqual(self._pending(db, "nomic"), [])
        before = _row(db, "memory_embeddings")
        env = self.ts.re_embed_under_lock(db)
        self.assertEqual(env["receipt"]["embedded"], 0)
        self.assertEqual(_row(db, "memory_embeddings"), before, "the centroid is untouched, byte for byte")

    def test_saboteur_a_stamped_centroid_is_not_work_for_the_backfill(self):
        db = make_brain(self.dir, "nomic")
        _consolidate(db, "nomic")
        c = _connect(db)
        self.assertEqual(self.eb.count_unembedded(c, "memory_embeddings"), 0)
        c.close()
        env = self.eb.backfill(str(db))
        self.assertEqual(env["code"], "backfill_nothing_to_do", env)

    def test_control_tier_set_rebuilds_the_centroid_in_the_target_table(self):
        db = make_brain(self.dir, "nomic")
        _consolidate(db, "nomic")
        env = self.ts.set_tier(db, "qwen", batch_size=3)
        self.assertEqual(env["status"], "ok", env)
        self.assertEqual(env["receipt"]["coverage"]["matched"], env["receipt"]["coverage"]["total"])
        vec, stamp = _row(db, "memory_embeddings_v3")
        self.assertEqual(vec, _expected_centroid(db, "memory_embeddings_v3", (1, 3, 4)))
        want = self.el.catalogue_tiers()["gpu"]["weights_hash"]
        self.assertEqual(stamp[:3], ("centroid", "[1, 3, 4]", want))

    def test_control_a_legacy_consolidated_row_is_rebuilt_by_re_embed(self):
        db = make_brain(self.dir, "nomic")
        _consolidate(db, "nomic", stamp=False)
        self.ts.re_embed_under_lock(db)
        vec, stamp = _row(db, "memory_embeddings")
        self.assertEqual(stamp[0], "centroid")
        self.assertEqual(vec, _expected_centroid(db, "memory_embeddings", (1, 3, 4)))

    def test_saboteur_a_gone_member_leaves_a_text_vector_and_full_coverage(self):
        db = make_brain(self.dir, "nomic")
        _consolidate(db, "nomic")
        c = _connect(db)
        c.execute("DELETE FROM memories WHERE id = 3")
        c.commit(); c.close()
        env = self.ts.set_tier(db, "qwen", batch_size=3)
        self.assertEqual(env["status"], "ok", env)
        vec, stamp = _row(db, "memory_embeddings_v3")
        self.assertEqual(stamp[0], "text")
        self.assertEqual(vec, _vec(TABLES["qwen"][2], "Distilled from 3 episodic memories.", 1024))

    def test_control_the_backfill_rebuilds_a_legacy_row_once_its_members_are_verified(self):
        db = make_brain(self.dir, "nomic")
        _consolidate(db, "nomic", stamp=False)
        lock = self.el.resolve(db)
        fake_load(lock)
        table, dim, model, _cat = TABLES["nomic"]
        with mock.patch.object(self.mw, "default_encoder", lambda t, conn=None: _vec(model, t, dim)):
            env = self.eb.backfill(str(db))
        self.assertEqual((env["status"], env["embedded"], env["failed"]), ("ok", 1, 0), env)
        self.assertEqual(_row(db, table)[1][0], "centroid")

    def test_saboteur_the_backfill_makes_a_row_wait_for_a_stale_member(self):
        db = make_brain(self.dir, "nomic")
        _consolidate(db, "nomic", stamp=False)
        c = _connect(db)
        c.execute("UPDATE embedding_provenance SET weights_hash = 'unresolved' WHERE memory_id = 3")
        c.commit(); c.close()
        fake_load(self.el.resolve(db))
        table, dim, model, _cat = TABLES["nomic"]
        with mock.patch.object(self.mw, "default_encoder", lambda t, conn=None: _vec(model, t, dim)):
            first = self.eb.backfill(str(db))
            self.assertEqual(first["centroids_waiting_for_members"], 1)
            second = self.eb.backfill(str(db))
        self.assertEqual(_row(db, table)[1][0], "centroid", "the next run finishes it")
        self.assertEqual(second["unembedded_after"], 0)


if __name__ == "__main__":
    unittest.main()
