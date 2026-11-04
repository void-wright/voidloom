"""`re-embed` and `tier set` on the brains the lock path did not reach.

THE CLAIM UNDER TEST
  * a brain with no install lock that embeds under a SHIPPED tier (nomic or qwen) is
    re-embedded under the shipped catalogue entry for the tier it already uses: the
    jina loader never runs, nothing lands in memory_embeddings_v2, no lock is written
    and the metadata rows stay as found;
  * only a brain that really is jina takes the jina path, and it still does;
  * a brain whose tier cannot be read refuses, typed, and loads nothing;
  * a batch that runs out of memory on the accelerator is retried one item at a time,
    on `tier set` and on `re-embed`, so a long memory cannot stall either verb for good;
    an item that cannot be embedded alone is named in a typed refusal, finished rows kept;
  * a sidecar that predates migration_042 is brought up before the first stamp.
No model is loaded: the same two seams test_tier_set uses.
"""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from voidloom.tests.test_tier_set import (ROOT, TABLES, _connect, _vec, _vec_available, fake_encode, fake_load,
                                          lock_bytes, make_brain, snapshot)


def _args(db, batch=4):
    return type("A", (), {"db_path": str(db), "batch_size": batch})()


def _strip_lock(db: Path) -> None:
    (db.parent / "embedder.lock.json").unlink()
    h = db.parent / "embedder.lock.history.jsonl"
    if h.exists():
        h.unlink()


def _make_prelock(d: Path, tier: str) -> Path:
    """A shipped-tier brain from before the lock: no lock file, stamps 'unresolved'."""
    db = make_brain(d, tier)
    _strip_lock(db)
    c = _connect(db)
    c.execute("UPDATE embedding_provenance SET weights_hash = 'unresolved'")
    c.commit(); c.close()
    return db


class _Seamed(unittest.TestCase):
    def setUp(self):
        import embedder_lock as el
        import embedding_upgrade as eu
        import tier_set
        self.el, self.eu, self.ts = el, eu, tier_set
        el.clear_loaded()
        self._seams = (tier_set._load, tier_set._encode)
        tier_set._load, tier_set._encode = fake_load, fake_encode
        self._keep_jina = eu._get_jina_model
        self.jina_calls = []

        def jina_spy():
            self.jina_calls.append(1)
            raise AssertionError("the jina loader ran")
        eu._get_jina_model = jina_spy
        self._td = tempfile.TemporaryDirectory()
        self.dir = Path(self._td.name)

    def tearDown(self):
        self.ts._load, self.ts._encode = self._seams
        self.eu._get_jina_model = self._keep_jina
        self.el.clear_loaded()
        self._td.cleanup()


@unittest.skipUnless(_vec_available(), "sqlite_vec absent on this host: NOT MEASURED")
class PreLockBrains(_Seamed):
    def _control(self, tier):
        db = _make_prelock(self.dir, tier)
        table, dim, model, cat = TABLES[tier]
        meta0 = snapshot(db, table)[2]
        env = self.eu.cmd_re_embed(_args(db))
        self.assertEqual(self.jina_calls, [], "the cc-by-nc loader must not run on a shipped-tier brain")
        self.assertEqual(env["status"], "ok", env)
        self.assertEqual(env["action"], "re-embed")
        self.assertEqual(env["receipt"]["embedded"], 7)
        self.assertIsNone(lock_bytes(self.dir), "a re-embed does not pin a brain: `init` does that")
        vecs, stamps, meta = snapshot(db, table)
        self.assertEqual(meta, meta0, "the metadata rows are left as found")
        want = self.el.catalogue_tiers()[cat]["weights_hash"]
        self.assertEqual({s[4] for s in stamps}, {want}, "the stamps name the computed checksum")
        self.assertEqual(len(vecs), 7)
        c = _connect(db)
        self.assertEqual(c.execute("SELECT COUNT(*) FROM sqlite_master WHERE name = 'memory_embeddings_v2'")
                         .fetchone()[0] if tier == "nomic" else 0, 0, "nothing lands in the jina table")
        c.close()
        again = self.eu.cmd_re_embed(_args(db))
        self.assertEqual(again["receipt"]["embedded"], 0, "a second run finds nothing")

    def test_control_prelock_nomic_resolves_to_the_catalogue_entry(self):
        self._control("nomic")

    def test_control_prelock_qwen_resolves_to_the_catalogue_entry(self):
        self._control("qwen")

    def test_control_prelock_edited_memory_is_work_again(self):
        db = _make_prelock(self.dir, "nomic")
        self.eu.cmd_re_embed(_args(db))
        c = _connect(db)
        c.execute("UPDATE memories SET content = 'rewritten text' WHERE id = 3")
        c.commit(); c.close()
        self.assertEqual(self.eu.cmd_re_embed(_args(db))["receipt"]["embedded"], 1)

    def test_saboteur_jina_brain_takes_the_jina_path_and_nothing_else(self):
        db = make_brain(self.dir, "jina")
        self.assertIsNone(self.ts.re_embed_under_lock(db), "jina never touches the lock path")
        self.assertIsNone(lock_bytes(self.dir))

    def test_saboteur_a_brain_with_no_declared_tier_refuses_typed(self):
        db = _make_prelock(self.dir, "nomic")
        c = _connect(db)
        c.execute("DELETE FROM metadata WHERE key = 'active_embedding_table'")
        c.commit(); c.close()
        with self.assertRaises(self.eu.VerbRefused) as cm:
            self.eu.cmd_re_embed(_args(db))
        self.assertEqual(cm.exception.code, "TIER_UNRESOLVED")
        self.assertEqual(self.jina_calls, [])

    def test_saboteur_rows_naming_another_model_on_the_same_table_refuse(self):
        db = _make_prelock(self.dir, "nomic")
        c = _connect(db)
        c.execute("UPDATE metadata SET value = 'someone/else' WHERE key = 'active_embedding_model'")
        c.commit(); c.close()
        before = snapshot(db, "memory_embeddings")
        with self.assertRaises(self.eu.VerbRefused) as cm:
            self.eu.cmd_re_embed(_args(db))
        self.assertEqual(cm.exception.code, "SAME_TABLE_REVISION_CHANGE")
        self.assertEqual(snapshot(db, "memory_embeddings"), before, "the brain is as found")

    def test_saboteur_a_refused_load_leaves_the_prelock_brain_as_found(self):
        db = _make_prelock(self.dir, "nomic")

        def refuse(lock):
            raise self.el.EmbedderLockError("WEIGHTS_MISMATCH", "planted")
        self.ts._load = refuse
        before = snapshot(db, "memory_embeddings")
        with self.assertRaises(self.eu.VerbRefused) as cm:
            self.eu.cmd_re_embed(_args(db))
        self.assertEqual(cm.exception.code, "WEIGHTS_MISMATCH")
        self.assertEqual(snapshot(db, "memory_embeddings"), before)

    def test_control_a_033_shape_sidecar_is_brought_up_before_the_first_stamp(self):
        db = _make_prelock(self.dir, "nomic")
        c = _connect(db)
        import memory_write as mw
        c.execute("DROP TABLE embedding_provenance")
        for stmt in mw._provenance_statements():
            c.execute(stmt)
        self.assertNotIn("weights_hash", mw._sidecar_columns(c), "the fixture is the 033 shape")
        c.commit(); c.close()
        env = self.eu.cmd_re_embed(_args(db))
        self.assertEqual((env["status"], env["receipt"]["embedded"]), ("ok", 7), env)

    def test_control_no_sidecar_at_all_is_built_before_the_first_stamp(self):
        db = _make_prelock(self.dir, "nomic")
        c = _connect(db)
        c.execute("DROP TABLE embedding_provenance")
        c.commit(); c.close()
        env = self.eu.cmd_re_embed(_args(db))
        self.assertEqual((env["status"], env["receipt"]["embedded"]), ("ok", 7), env)

    def test_control_plan_reads_a_brain_with_no_sidecar(self):
        db = _make_prelock(self.dir, "nomic")
        c = _connect(db)
        c.execute("DROP TABLE embedding_provenance")
        c.commit(); c.close()
        self.assertEqual(self.ts.plan(db, "qwen")["pending"], 7)
        self.assertEqual(self.ts.plan(db, "nomic")["pending"], 7, "the table exists, the stamps do not")


@unittest.skipUnless(_vec_available(), "sqlite_vec absent on this host: NOT MEASURED")
class JinaBrainKeepsItsPath(_Seamed):
    """A brain that really is jina: the jina encoder, its own table, no lock, and every
    vector carries a stamp naming the declared absence."""

    def _brain(self):
        db = make_brain(self.dir, "jina")
        c = _connect(db)
        c.execute("DELETE FROM memory_embeddings_v2 WHERE memory_id IN (1, 3, 4)")
        c.execute("DELETE FROM embedding_provenance WHERE memory_id IN (1, 3, 4)")
        c.execute("CREATE TABLE embedding_upgrade_log (id INTEGER PRIMARY KEY, started_at TEXT, completed_at TEXT, "
                  "model_name TEXT, embedding_dim INTEGER, memories_processed INTEGER, memories_total INTEGER, "
                  "status TEXT, notes TEXT)")
        c.execute("ALTER TABLE memories ADD COLUMN summary TEXT")
        c.commit(); c.close()
        return db

    def _jina(self, enc):
        self.eu._get_jina_model = lambda: None
        keep = self.eu.encode_jina
        self.addCleanup(setattr, self.eu, "encode_jina", keep)
        self.eu.encode_jina = enc

    def test_control_the_jina_path_runs_and_stamps_the_declared_absence(self):
        db = self._brain()
        model = TABLES["jina"][2]
        self._jina(lambda texts, task=None, prompt_name=None: [_vec(model, t, 1024) for t in texts])
        env = self.eu.cmd_re_embed(_args(db, batch=2))
        self.assertEqual((env["status"], env["processed"], env["errors"]), ("completed", 3, 0), env)
        self.assertIsNone(lock_bytes(self.dir))
        c = _connect(db)
        self.assertEqual(c.execute("SELECT COUNT(*) FROM memory_embeddings_v2").fetchone()[0], 7)
        rows = c.execute("SELECT weights_hash, organ FROM embedding_provenance WHERE memory_id IN (1, 3, 4)").fetchall()
        c.close()
        self.assertEqual(rows, [("unresolved", "embedding_upgrade.re_embed")] * 3)

    def test_saboteur_a_batch_that_fails_leaves_no_vector_without_a_stamp(self):
        db = self._brain()
        model = TABLES["jina"][2]

        def enc(texts, task=None, prompt_name=None):
            if len(texts) > 1:
                raise RuntimeError("CUDA out of memory")
            if "memory 3 " in texts[0]:
                raise RuntimeError("CUDA out of memory")
            return [_vec(model, t, 1024) for t in texts]
        self._jina(enc)
        env = self.eu.cmd_re_embed(_args(db, batch=3))
        self.assertEqual((env["status"], env["processed"], env["errors"]), ("completed_with_errors", 2, 1), env)
        c = _connect(db)
        vec = {r[0] for r in c.execute("SELECT memory_id FROM memory_embeddings_v2")}
        st = {r[0] for r in c.execute("SELECT memory_id FROM embedding_provenance")}
        c.close()
        self.assertEqual(vec, st, "a vector and its stamp land together")
        self.assertNotIn(3, vec)


@unittest.skipUnless(_vec_available(), "sqlite_vec absent on this host: NOT MEASURED")
class OutOfMemoryRetry(_Seamed):
    def _oom_when_batched(self, poison=None):
        """Encodes alone; a batch holding more than one text runs out of memory, and a
        `poison` text runs out of memory even alone."""
        calls = []

        def enc(texts, lock):
            calls.append(len(texts))
            if len(texts) > 1 or (poison and poison in texts[0]):
                raise RuntimeError("CUDA out of memory. Tried to allocate 2.00 GiB")
            return fake_encode(texts, lock)
        self.ts._encode = enc
        return calls

    def test_control_tier_set_finishes_through_an_out_of_memory_batch(self):
        db = make_brain(self.dir, "nomic")
        calls = self._oom_when_batched()
        env = self.ts.set_tier(db, "qwen", batch_size=4)
        self.assertEqual(env["status"], "ok", env)
        self.assertEqual(env["receipt"]["embedded"], 7)
        self.assertIn(1, calls, "the retry went item by item")
        self.assertEqual(self.el.resolve(db)["tier"], "gpu")

    def test_control_re_embed_finishes_through_an_out_of_memory_batch(self):
        db = make_brain(self.dir, "nomic")
        c = _connect(db)
        c.execute("UPDATE embedding_provenance SET weights_hash = 'unresolved'")
        c.commit(); c.close()
        self._oom_when_batched()
        env = self.eu.cmd_re_embed(_args(db))
        self.assertEqual((env["status"], env["receipt"]["embedded"]), ("ok", 7), env)

    def test_saboteur_an_item_too_long_alone_is_named_and_finished_rows_are_kept(self):
        db = make_brain(self.dir, "nomic")
        c = _connect(db)
        c.execute("UPDATE memories SET content = 'poison pill' WHERE id = 4")
        c.commit(); c.close()
        self._oom_when_batched(poison="poison")
        with self.assertRaises(self.ts.TierRefused) as cm:
            self.ts.set_tier(db, "qwen", batch_size=4)
        self.assertEqual(cm.exception.code, "COVERAGE_INCOMPLETE")
        self.assertEqual(cm.exception.detail["unembedded"], [4])
        self.assertEqual(self.el.resolve(db)["tier"], "cpu", "nothing switched")
        self.assertEqual(len(snapshot(db, "memory_embeddings_v3")[0]), 6, "the other rows are kept for the rerun")

    def test_control_a_brain_with_no_metadata_table_can_still_converge(self):
        db = make_brain(self.dir, "nomic")
        c = _connect(db)
        c.execute("DROP TABLE metadata")
        c.execute("UPDATE embedding_provenance SET weights_hash = 'unresolved'")
        c.commit(); c.close()
        env = self.eu.cmd_re_embed(_args(db))
        self.assertEqual((env["status"], env["receipt"]["embedded"]), ("ok", 7), env)

    def _long_memory(self, n=100000):
        db = make_brain(self.dir, "nomic")
        c = _connect(db)
        c.execute("UPDATE memories SET content = ? WHERE id = 4", ("tide " * (n // 5),))
        c.commit(); c.close()
        return db

    def _fits_up_to(self, limit):
        sizes = []

        def enc(texts, lock):
            sizes.append(sum(len(t) for t in texts))
            if sum(len(t) for t in texts) > limit:
                raise RuntimeError("CUDA out of memory. Tried to allocate 64.00 GiB")
            return fake_encode(texts, lock)
        self.ts._encode = enc
        return sizes

    def test_control_a_memory_too_long_alone_is_embedded_from_its_head_and_counted(self):
        import memory_write as mw
        db = self._long_memory()
        sizes = self._fits_up_to(10000)
        env = self.ts.set_tier(db, "qwen", batch_size=4)
        self.assertEqual(env["status"], "ok", env)
        self.assertEqual(env["receipt"]["shortened_to_fit"], 1)
        self.assertIn(8000, sizes, "halved from the first shortened length")
        c = _connect(db)
        text = c.execute("SELECT content FROM memories WHERE id = 4").fetchone()[0]
        h = c.execute("SELECT content_hash FROM embedding_provenance WHERE memory_id = 4 AND "
                      "vec_table = 'memory_embeddings_v3'").fetchone()[0]
        c.close()
        self.assertEqual(h, mw.content_hash(text), "the stamp names the whole memory")
        again = self.ts.set_tier(db, "qwen", batch_size=4)
        self.assertEqual(again["receipt"]["embedded"], 0, "a rerun finds nothing: it cannot stall here")

    def test_control_the_cpu_allocators_words_count_as_out_of_memory(self):
        self.assertTrue(self.ts._is_out_of_memory(RuntimeError(
            "[enforce fail at alloc_cpu.cpp:121] DefaultCPUAllocator: not enough memory: you tried to allocate 68719476736 bytes.")))

    def test_saboteur_an_error_that_is_not_out_of_memory_still_raises(self):
        db = make_brain(self.dir, "nomic")

        def enc(texts, lock):
            raise RuntimeError("the tokenizer is on fire")
        self.ts._encode = enc
        with self.assertRaises(RuntimeError):
            self.ts.set_tier(db, "qwen", batch_size=4)
        self.assertEqual(self.el.resolve(db)["tier"], "cpu")


if __name__ == "__main__":
    unittest.main()
