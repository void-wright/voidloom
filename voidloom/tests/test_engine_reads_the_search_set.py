# -*- coding: utf-8 -*-
"""The engine's verbs read the search set, and report what they wrote.

Each of these surfaces used to read the vector table directly, or report what it
meant to do rather than what it did. Real brains here: every migration in this
tree, the real verbs through `claude_memory.main()`, a stub in place of the model
only (the property is about which rows are read, and an absent model is a fact
about the host). Every check is a control/saboteur pair:

  dedup      a vector outside the search set never archives a memory
  stats      the figure is the reader's, not a presence count
  novelty    a foreign vector does not make a memory look familiar
  bm25       a keyword-only recall needs no embedder
  legacy     the fallback recall carries a filter, or says it has none
  update     `re_embedded` is the stamp written
  delete     a hard delete leaves no vector on any tier, and no stamp
  stamp      a stamp names the table the write touched
  encoder    the encoder follows the caller's lock, not the global path
  refusal    a typed refusal's coverage reaches the envelope
"""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import math
import os
import sqlite3
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(os.environ.get("VOIDLOOM_ROOT") or Path(__file__).resolve().parents[2]).resolve()
sys.path.insert(0, str(ROOT / "organs"))

MIGRATIONS = ROOT / "migrations"
NOMIC, QWEN, JINA = "memory_embeddings", "memory_embeddings_v3", "memory_embeddings_v2"


def _unit(dim, *pairs):
    v = [0.0] * dim
    for i, w in pairs:
        v[i] = w
    n = math.sqrt(sum(x * x for x in v))
    return struct.pack(f"{dim}f", *[x / n for x in v])


class _Engine(unittest.TestCase):
    """A brain on `cm.DB_PATH`, locked to the cpu tier unless LOCK says otherwise."""
    LOCK = "cpu"
    TABLE, DIM = NOMIC, 768
    METADATA_TABLE = None

    def setUp(self):
        import claude_memory as cm
        import embedder_lock as el
        import memory_write as mw
        import schema_migrator
        import sqlite_vec
        self.cm, self.el, self.mw, self._vec = cm, el, mw, sqlite_vec
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)
        self.db = self.d / "claude_memory.db"
        out = schema_migrator.apply(str(self.db), MIGRATIONS)
        self.assertEqual(out.get("failed") or [], [], out)
        self.conn = self.open()
        for t, w in ((NOMIC, 768), (JINA, 1024), (QWEN, 1024)):
            self.conn.execute(f"CREATE VIRTUAL TABLE IF NOT EXISTS {t} USING vec0("
                              f"memory_id INTEGER PRIMARY KEY, embedding float[{w}])")
        self.conn.execute("DELETE FROM metadata WHERE key LIKE 'active_embedding%'")
        if self.METADATA_TABLE:
            self.conn.execute("INSERT INTO metadata (key, value) VALUES ('active_embedding_table', ?)",
                              (self.METADATA_TABLE,))
        self.conn.commit()
        self.lock = None
        if self.LOCK:
            self.lock = el.write_lock(self.d, self.LOCK)
            el.register_loaded(model_id=self.lock["model_id"], revision=self.lock["revision"],
                               weights_hash=self.lock["weights_hash"], dim=self.DIM, vec_table=self.TABLE)
        self.foreign_hash = "a" * 64
        self._db_was = cm.DB_PATH
        cm.DB_PATH = self.db
        cm._active_model_cache.clear()

    def tearDown(self):
        self.cm.DB_PATH = self._db_was
        self.conn.close()
        self.el.clear_loaded()
        self.tmp.cleanup()

    def open(self):
        c = sqlite3.connect(str(self.db))
        c.enable_load_extension(True)
        self._vec.load(c)
        c.enable_load_extension(False)
        return c

    def v(self, *pairs):
        return _unit(self.DIM, *pairs)

    def add(self, text, category="insight", importance=0.5):
        mid = int(self.conn.execute(
            "INSERT INTO memories (content, summary, category, importance) VALUES (?, ?, ?, ?)",
            (text, text[:40], category, importance)).lastrowid)
        self.conn.commit()
        return mid

    def bare(self, text, vec, **kw):
        mid = self.add(text, **kw)
        self.conn.execute(f"INSERT INTO {self.TABLE} (memory_id, embedding) VALUES (?, ?)", (mid, vec))
        self.conn.commit()
        return mid

    def stamp_row(self, mid, table, dim, whash, text="x", model="m/m"):
        self.conn.execute(
            "INSERT OR REPLACE INTO embedding_provenance (memory_id, vec_table, model_id, model_ref, weights_hash, "
            "dim, content_hash, embedded_at, organ, input_kind, inputs) "
            "VALUES (?, ?, ?, 'r', ?, ?, ?, '2026-01-01T00:00:00+00:00', 'test', 'text', NULL)",
            (mid, table, model, whash, dim, hashlib.sha256(text.encode()).hexdigest()))
        self.conn.commit()

    def verified(self, text, vec, **kw):
        mid = self.bare(text, vec, **kw)
        self.stamp_row(mid, self.TABLE, self.DIM, self.lock["weights_hash"], text)
        return mid

    def foreign(self, text, vec, **kw):
        mid = self.bare(text, vec, **kw)
        self.stamp_row(mid, self.TABLE, self.DIM, self.foreign_hash, text)
        return mid

    def verb(self, *argv):
        """(rc, envelope) of the real verb through main()."""
        buf = io.StringIO()
        rc = 0
        self.conn.commit()
        with mock.patch.object(sys, "argv", ["claude_memory.py", *argv]), \
                mock.patch.object(self.cm, "_wizard_library_db_guard", lambda: None), \
                contextlib.redirect_stdout(buf):
            try:
                self.cm.main()
            except SystemExit as e:
                rc = e.code or 0
        return rc, json.loads(buf.getvalue().strip().splitlines()[-1])

    def is_archived(self, mid):
        return self.conn.execute("SELECT is_archived FROM memories WHERE id = ?", (mid,)).fetchone()[0]


class _Jina(_Engine):
    """The owner's shape: no lock, metadata declares the tier the catalogue does not ship."""
    LOCK = None
    TABLE, DIM = JINA, 1024
    METADATA_TABLE = JINA


class DedupComparesTheSearchSet(_Engine):
    def test_control_two_verified_twins_are_deduplicated(self):
        a = self.verified("twin one", self.v((0, 1.0)), importance=0.9)
        b = self.verified("twin two", self.v((0, 1.0)), importance=0.4)
        rc, out = self.verb("maintain")
        self.assertEqual((rc, out["deduplicated"], self.is_archived(a), self.is_archived(b)), (0, 1, 0, 1), out)

    def test_control_legacy_rows_are_in_the_set_and_are_compared(self):
        self.verified("anchor", self.v((5, 1.0)))
        a = self.bare("legacy twin one", self.v((0, 1.0)), importance=0.9)
        b = self.bare("legacy twin two", self.v((0, 1.0)), importance=0.4)
        rc, out = self.verb("maintain")
        self.assertEqual((rc, out["deduplicated"], self.is_archived(a), self.is_archived(b)), (0, 1, 0, 1), out)

    def test_saboteur_a_foreign_vector_archives_nothing(self):
        a = self.verified("my real memory", self.v((0, 1.0)), importance=0.4)
        b = self.foreign("another model's row", self.v((0, 1.0)), importance=0.9)
        rc, out = self.verb("maintain")
        self.assertEqual((rc, out["deduplicated"], self.is_archived(a), self.is_archived(b)), (0, 0, 0, 0), out)

    def test_saboteur_a_refused_set_archives_nothing_and_the_decay_pass_stands(self):
        a = self.foreign("only foreign one", self.v((0, 1.0)))
        b = self.foreign("only foreign two", self.v((0, 1.0)))
        rc, out = self.verb("maintain")
        self.assertEqual((rc, out["deduplicated"]), (0, 0), out)
        self.assertEqual(out["dedup_refused"]["error"], "NO_ROWS_FOR_LOCK", out)
        self.assertIn("Dedup refused: NO_ROWS_FOR_LOCK", out["actions"])
        self.assertEqual((self.is_archived(a), self.is_archived(b)), (0, 0))

    def test_control_a_similarity_link_carries_the_checksum_it_was_computed_under(self):
        a = self.verified("near one", self.v((0, 1.0)))
        b = self.verified("near two", self.v((0, 0.8), (1, 0.6)))
        rc, out = self.verb("maintain")
        self.assertEqual((rc, out["linked"]), (0, 1), out)
        rows = self.conn.execute("SELECT source_id, target_id, weights_hash FROM memory_links").fetchall()
        self.assertEqual(rows, [(a, b, self.lock["weights_hash"])])

    def test_control_an_empty_brain_is_no_refusal(self):
        rc, out = self.verb("maintain")
        self.assertEqual((rc, out["deduplicated"], out["dedup_refused"]), (0, 0, None), out)


class DedupOnAJinaBrain(_Jina):
    def test_control_a_link_on_a_brain_with_no_lock_stays_unstamped(self):
        self.bare("near one", self.v((0, 1.0)))
        self.bare("near two", self.v((0, 0.8), (1, 0.6)))
        rc, out = self.verb("maintain")
        self.assertEqual((rc, out["linked"]), (0, 1), out)
        self.assertEqual(self.conn.execute("SELECT weights_hash FROM memory_links").fetchall(), [(None,)])

    def test_control_the_whole_table_is_the_set_as_today(self):
        a = self.bare("jina twin one", self.v((0, 1.0)), importance=0.9)
        b = self.bare("jina twin two", self.v((0, 1.0)), importance=0.4)
        rc, out = self.verb("maintain")
        self.assertEqual((rc, out["deduplicated"], self.is_archived(a), self.is_archived(b)), (0, 1, 0, 1), out)


class StatsIsTheReadersFigure(_Engine):
    def _population(self):
        self.verified("verified", self.v((0, 1.0)))
        self.bare("legacy", self.v((1, 1.0)))
        self.foreign("foreign", self.v((2, 1.0)))
        gone = self.verified("archived", self.v((3, 1.0)))
        self.conn.execute("UPDATE memories SET is_archived = 1 WHERE id = ?", (gone,))
        self.add("no vector at all")

    def test_control_matched_of_total_with_the_split_beside_it(self):
        self._population()
        rc, out = self.verb("stats")
        cov = out["coverage"]
        self.assertEqual((rc, out["embedded_memories"]), (0, 2), out)
        self.assertEqual((cov["matched"], cov["total"], cov["verified"]), (2, 4, 1), cov)
        self.assertEqual(cov["legacy_unverified"]["text"], "1 of 2")

    def test_saboteur_presence_would_have_read_four(self):
        self._population()
        presence = self.conn.execute(f"SELECT COUNT(*) FROM {NOMIC}").fetchone()[0]
        self.assertEqual(presence, 4)
        self.assertNotEqual(self.verb("stats")[1]["embedded_memories"], presence)

    def test_control_the_derived_layer_rides_on_stats(self):
        rc, out = self.verb("stats")
        self.assertEqual(rc, 0, out)
        self.assertIn("stale_derived", out["derived_layer"])
        self.assertNotIn("error", out["derived_layer"])

    def test_saboteur_a_malformed_lock_reads_zero_with_its_code(self):
        self.verified("verified", self.v((0, 1.0)))
        (self.d / "embedder.lock.json").write_text("{not json", encoding="utf-8")
        rc, out = self.verb("stats")
        self.assertEqual((rc, out["embedded_memories"]), (0, 0), out)
        self.assertEqual(out["coverage"]["error"], "LOCK_MALFORMED", out["coverage"])


class StatsOnAJinaBrain(_Jina):
    def test_control_the_whole_table_is_the_set_and_coverage_rides_beside_it(self):
        self.bare("one", self.v((0, 1.0)))
        self.bare("two", self.v((1, 1.0)))
        rc, out = self.verb("stats")
        self.assertEqual((rc, out["embedded_memories"]), (0, 2), out)
        self.assertEqual(out["coverage"]["basis"], "lockless")

    def test_saboteur_an_archived_row_is_not_embedded_for_any_search(self):
        self.bare("one", self.v((0, 1.0)))
        gone = self.bare("two", self.v((1, 1.0)))
        self.conn.execute("UPDATE memories SET is_archived = 1 WHERE id = ?", (gone,))
        self.assertEqual(self.verb("stats")[1]["embedded_memories"], 1)


class NoveltyReadsTheSearchSet(_Engine):
    def test_saboteur_a_foreign_twin_does_not_make_a_memory_familiar(self):
        vec = self.v((0, 1.0))
        self.foreign("a twin in another space", vec)
        self.verified("a far neighbour", self.v((1, 1.0)))
        mid = self.verified("the row just written", vec)
        self.assertGreater(self.cm._compute_write_novelty(self.conn, mid, vec), 0.5)

    def test_control_a_verified_twin_does(self):
        vec = self.v((0, 1.0))
        self.verified("a twin in this space", vec)
        mid = self.verified("the row just written", vec)
        self.assertLess(self.cm._compute_write_novelty(self.conn, mid, vec), 0.01)

    def test_control_a_legacy_twin_does_too(self):
        vec = self.v((0, 1.0))
        self.bare("a legacy twin", vec)
        mid = self.verified("the row just written", vec)
        self.assertLess(self.cm._compute_write_novelty(self.conn, mid, vec), 0.01)


class NoveltyOnAJinaBrain(_Jina):
    def test_control_the_whole_table_as_today(self):
        vec = self.v((0, 1.0))
        self.bare("a twin", vec)
        mid = self.bare("the row just written", vec)
        self.assertLess(self.cm._compute_write_novelty(self.conn, mid, vec), 0.01)


class NoveltyWithNoDeclaredTier(_Engine):
    LOCK = None

    def test_control_a_brain_declaring_nothing_keeps_its_figure(self):
        vec = self.v((0, 1.0))
        self.bare("a twin", vec)
        mid = self.bare("the row just written", vec)
        self.assertLess(self.cm._compute_write_novelty(self.conn, mid, vec), 0.01)


class KeywordRecallNeedsNoEmbedder(_Engine):
    def _child(self, *flags):
        self.verified("the cinder lantern", self.v((0, 1.0)))
        self.conn.close()
        self.conn = self.open()
        empty = self.d / "empty_cache"
        empty.mkdir(exist_ok=True)
        env = {k: v for k, v in os.environ.items() if k not in ("WIZARD_LIBRARY_HOME", "CLAUDE_MEMORY_DB",
                                                               "WIZARD_EMBED_MODEL")}
        env.pop("WIZARD_LIBRARY_DB", None)
        env.update({"HF_HUB_OFFLINE": "1", "HF_HUB_CACHE": str(empty), "PYTHONDONTWRITEBYTECODE": "1"})
        # the real verb in a child; the engine finds its brain beside itself, so the child points it here
        child = ("import sys, pathlib\n"
                 f"sys.path.insert(0, {str(ROOT / 'organs')!r})\n"
                 "import claude_memory as cm\n"
                 f"cm.DB_PATH = pathlib.Path({str(self.db)!r})\n"
                 "cm.main()\n")
        return subprocess.run([sys.executable, "-c", child, "recall", "--query", "lantern", "--top_k", "3", *flags],
                              capture_output=True, text=True, timeout=180, env=env, cwd=str(self.d))

    def test_control_bm25_only_answers_on_an_empty_cache(self):
        r = self._child("--bm25-only")
        self.assertEqual(r.returncode, 0, r.stdout[-400:] + r.stderr[-400:])
        out = json.loads(r.stdout.strip().splitlines()[-1])
        self.assertEqual((out["status"], out["result_count"]), ("ok", 1), out)
        self.assertEqual((out["coverage"]["matched"], out["coverage"]["total"]), (1, 1))
        self.assertNotIn("loading embedding model", r.stderr)

    def test_saboteur_the_same_child_in_hybrid_mode_reaches_for_the_embedder(self):
        r = self._child()
        self.assertNotEqual(r.returncode, 0, r.stdout[-400:])
        self.assertIn("SNAPSHOT_ABSENT", r.stdout + r.stderr)


class LegacyRecallCarriesAFilter(_Engine):
    def recall(self, *more):
        q = self.v((0, 1.0))
        with mock.patch.object(self.cm, "hybrid_retrieval", None), \
                mock.patch.object(self.cm, "_encode", lambda text, prefix="search_document", **kw: q):
            return self.verb("recall", "--query", "anything", "--top_k", "5", *more)

    def test_control_verified_and_legacy_rows_with_coverage_and_never_the_foreign_row(self):
        a = self.verified("verified", self.v((0, 1.0)))
        b = self.bare("legacy", self.v((0, 1.0), (1, 0.2)))
        f = self.foreign("foreign and nearest", self.v((0, 1.0)))
        rc, out = self.recall()
        self.assertEqual((rc, out["mode"]), (0, "vector-only-legacy"), out)
        self.assertEqual(sorted(r["id"] for r in out["results"]), sorted([a, b]))
        self.assertNotIn(f, [r["id"] for r in out["results"]])
        self.assertEqual((out["coverage"]["matched"], out["coverage"]["total"]), (2, 3))

    def test_saboteur_a_refusal_is_the_answer_with_its_coverage_and_loads_no_model(self):
        self.foreign("only foreign", self.v((0, 1.0)))

        def must_not_run(*_a, **_k):
            raise AssertionError("encoded a query for a search that was refused")
        with mock.patch.object(self.cm, "hybrid_retrieval", None), mock.patch.object(self.cm, "_encode", must_not_run):
            rc, out = self.verb("recall", "--query", "anything", "--top_k", "5")
        self.assertEqual((rc, out["status"], out["error"]), (1, "error", "NO_ROWS_FOR_LOCK"), out)
        self.assertEqual((out["coverage"]["matched"], out["coverage"]["total"]), (0, 1))

    def test_control_an_empty_brain_answers_empty_with_coverage(self):
        rc, out = self.recall()
        self.assertEqual((rc, out["status"], out["result_count"]), (0, "ok", 0), out)
        self.assertEqual((out["coverage"]["matched"], out["coverage"]["total"]), (0, 0))


class LegacyRecallOnAJinaBrain(_Jina):
    def test_control_the_whole_table_answers_as_today(self):
        q = self.v((0, 1.0))
        a = self.bare("one", q)
        with mock.patch.object(self.cm, "hybrid_retrieval", None), \
                mock.patch.object(self.cm, "_encode", lambda text, prefix="search_document", **kw: q):
            rc, out = self.verb("recall", "--query", "anything", "--top_k", "5")
        self.assertEqual((rc, [r["id"] for r in out["results"]]), (0, [a]), out)
        self.assertIn("coverage", out)


class LegacyRecallWithNoDeclaredTier(_Engine):
    LOCK = None

    def test_control_the_rows_carry_an_honest_label(self):
        q = self.v((0, 1.0))
        a = self.bare("one", q)
        with mock.patch.object(self.cm, "hybrid_retrieval", None), \
                mock.patch.object(self.cm, "_encode", lambda text, prefix="search_document", **kw: q):
            rc, out = self.verb("recall", "--query", "anything", "--top_k", "5")
        self.assertEqual((rc, [r["id"] for r in out["results"]]), (0, [a]), out)
        self.assertTrue(out["coverage"]["filter"].startswith("none:"), out["coverage"])


class UpdateReportsTheStampWritten(_Engine):
    def test_control_a_real_update_re_embeds_and_says_so(self):
        mid = self.verified("old words", self.v((0, 1.0)))
        with mock.patch.object(self.cm, "_encode", lambda t, p="search_document", **k: self.v((1, 1.0))):
            rc, out = self.verb("update", "--id", str(mid), "--content", "new words")
        self.assertEqual((rc, out["re_embedded"]), (0, True), out)

    def test_saboteur_a_stamp_that_wrote_nothing_is_not_a_re_embed(self):
        mid = self.verified("old words", self.v((0, 1.0)))
        with mock.patch.object(self.cm, "_encode", lambda t, p="search_document", **k: self.v((1, 1.0))), \
                mock.patch.object(self.cm, "_stamp_vector_provenance", lambda *a, **k: None):
            rc, out = self.verb("update", "--id", str(mid), "--content", "new words")
        self.assertEqual((rc, out["re_embedded"]), (0, False), out)

    def test_control_an_update_with_no_new_words_re_embeds_nothing(self):
        mid = self.verified("old words", self.v((0, 1.0)))
        rc, out = self.verb("update", "--id", str(mid), "--importance", "0.9")
        self.assertEqual((rc, out["re_embedded"]), (0, False), out)


class UpdateOnAJinaBrain(_Jina):
    def test_control_an_unresolved_stamp_is_still_a_stamp_written(self):
        mid = self.bare("old words", self.v((0, 1.0)))
        with mock.patch.object(self.cm, "_encode", lambda t, p="search_document", **k: self.v((1, 1.0))):
            rc, out = self.verb("update", "--id", str(mid), "--content", "new words")
        self.assertEqual((rc, out["re_embedded"]), (0, True), out)
        row = self.conn.execute("SELECT vec_table, weights_hash FROM embedding_provenance WHERE memory_id = ?",
                                (mid,)).fetchone()
        self.assertEqual(row, (JINA, "unresolved"))


class HardDeleteRemovesTheRowForGood(_Engine):
    def setUp(self):
        super().setUp()
        self.mid = self.verified("to be deleted", self.v((0, 1.0)))
        self.keep = self.verified("kept", self.v((1, 1.0)))
        # the shape `tier set` leaves: the old tier's vector and stamp kept for rollback
        self.conn.execute(f"INSERT INTO {QWEN} (memory_id, embedding) VALUES (?, ?)", (self.mid, _unit(1024, (0, 1.0))))
        self.stamp_row(self.mid, QWEN, 1024, self.foreign_hash)
        self.conn.execute(f"INSERT INTO {JINA} (memory_id, embedding) VALUES (?, ?)", (self.mid, _unit(1024, (0, 1.0))))
        self.stamp_row(self.mid, JINA, 1024, "unresolved")

    def left(self):
        return {t: self.conn.execute(f"SELECT COUNT(*) FROM {t} WHERE memory_id = ?", (self.mid,)).fetchone()[0]
                for t in (NOMIC, JINA, QWEN, "embedding_provenance")}

    def delete(self):
        env = {**os.environ, "VOIDLOOM_ALLOW_HARD_DELETE": "1"}
        with mock.patch.dict(os.environ, env):
            return self.verb("delete", "--id", str(self.mid), "--hard")

    def test_control_no_vector_on_any_tier_and_no_stamp(self):
        rc, out = self.delete()
        self.assertEqual((rc, out["hard_delete"]), (0, True), out)
        self.assertEqual(self.left(), {NOMIC: 0, JINA: 0, QWEN: 0, "embedding_provenance": 0})
        self.assertIsNone(self.conn.execute("SELECT 1 FROM memories WHERE id = ?", (self.mid,)).fetchone())
        cov = self.mw.provenance_coverage(self.conn)
        self.assertEqual((cov.stamped_without_vector, cov.matched, cov.total), (0, 1, 1))

    def test_control_the_other_memory_is_untouched(self):
        self.delete()
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM embedding_provenance WHERE memory_id = ?",
                                           (self.keep,)).fetchone()[0], 1)

    def test_saboteur_a_failure_mid_delete_leaves_everything_as_it_was(self):
        self.conn.execute("CREATE TRIGGER refuse_delete BEFORE DELETE ON memories BEGIN "
                          "SELECT RAISE(ABORT, 'planted'); END")
        self.conn.commit()
        with self.assertRaises(sqlite3.Error):
            self.delete()
        self.assertEqual(self.left(), {NOMIC: 1, JINA: 1, QWEN: 1, "embedding_provenance": 3})

    def test_saboteur_a_row_with_no_delete_rule_is_a_typed_refusal_and_removes_nothing(self):
        self.conn.execute("INSERT INTO resurfacing_log (memory_id, reason) VALUES (?, 'test')", (self.mid,))
        self.conn.commit()
        rc, out = self.delete()
        self.assertEqual((rc, out["error"], out["held_by"]),
                         (1, "HARD_DELETE_REFERENCED", {"resurfacing_log.memory_id": 1}), out)
        self.assertEqual(self.left(), {NOMIC: 1, JINA: 1, QWEN: 1, "embedding_provenance": 3})
        self.assertIsNotNone(self.conn.execute("SELECT 1 FROM memories WHERE id = ?", (self.mid,)).fetchone())

    def test_control_without_the_gate_it_is_a_soft_archive_and_nothing_is_removed(self):
        env = {k: v for k, v in os.environ.items() if k != "VOIDLOOM_ALLOW_HARD_DELETE"}
        with mock.patch.dict(os.environ, env, clear=True):
            rc, out = self.verb("delete", "--id", str(self.mid), "--hard")
        self.assertEqual((rc, out["hard_delete"], self.is_archived(self.mid)), (0, False, 1), out)
        self.assertEqual(self.left(), {NOMIC: 1, JINA: 1, QWEN: 1, "embedding_provenance": 3})


class AStampNamesTheTableTheWriteTouched(_Engine):
    """A pre-lock brain whose environment disagrees with its metadata: the encoder and the
    write follow the variable; a stamp that resolved the tier again would follow the row."""
    LOCK = None
    TABLE, DIM = NOMIC, 768
    METADATA_TABLE = QWEN

    def setUp(self):
        super().setUp()
        p = mock.patch.dict(os.environ, {"WIZARD_EMBED_MODEL": "nomic"})
        p.start()
        self.addCleanup(p.stop)
        self.cm._active_model_cache.clear()

    def stamped(self, mid):
        return self.conn.execute("SELECT vec_table, dim FROM embedding_provenance WHERE memory_id = ?",
                                 (mid,)).fetchall()

    def test_control_store_stamps_the_table_it_wrote(self):
        with mock.patch.object(self.cm, "_encode", lambda t, p="search_document", **k: self.v((0, 1.0))):
            rc, out = self.verb("store", "--content", "a new memory", "--category", "insight")
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.stamped(out["memory_id"]), [(NOMIC, 768)])
        self.assertEqual(self.conn.execute(f"SELECT COUNT(*) FROM {NOMIC} WHERE memory_id = ?",
                                           (out["memory_id"],)).fetchone()[0], 1)

    def test_control_update_stamps_the_table_it_wrote(self):
        mid = self.bare("old words", self.v((0, 1.0)))
        with mock.patch.object(self.cm, "_encode", lambda t, p="search_document", **k: self.v((1, 1.0))):
            rc, out = self.verb("update", "--id", str(mid), "--content", "new words")
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.stamped(mid), [(NOMIC, 768)])

    def test_control_migrate_stamps_the_table_it_wrote(self):
        src = self.d / "projects" / "p" / "memory"
        src.mkdir(parents=True)
        (src / "MEMORY.md").write_text("## A heading\n\nA remembered thing, long enough to import as a memory "
                                       "row by the parser that reads these files.\n", encoding="utf-8")
        with mock.patch.object(self.cm, "_encode", lambda t, p="search_document", **k: self.v((2, 1.0))):
            rc, out = self.verb("migrate", "--source", str(src / "*.md"))
        self.assertEqual(rc, 0, out)
        rows = self.conn.execute("SELECT DISTINCT vec_table FROM embedding_provenance").fetchall()
        self.assertEqual(rows, [(NOMIC,)], out)

    def test_saboteur_the_helper_requires_what_the_caller_wrote(self):
        with self.assertRaises(TypeError):
            self.cm._stamp_vector_provenance(self.conn, 1, "x", "t")

    def test_control_the_helper_returns_the_stamp_it_wrote(self):
        mid = self.bare("words", self.v((0, 1.0)))
        out = self.cm._stamp_vector_provenance(self.conn, mid, "words", "t", table=NOMIC, dim=768)
        self.assertEqual((out["vec_table"], out["weights_hash"]), (NOMIC, "unresolved"))


class TheEncoderFollowsTheCallersLock(_Engine):
    def test_control_a_passed_lock_is_the_one_encoded_under(self):
        import embedding_upgrade as eu
        theirs = self.el.catalogue_tiers()["gpu"]
        seen = {}

        def fake(text, *, is_query, lock):
            seen.update(text=text, is_query=is_query, lock=lock)
            return b"x"
        with mock.patch.object(eu, "encode_locked", fake):
            self.assertEqual(self.cm._encode("hello", "search_query", lock=theirs), b"x")
        self.assertIs(seen["lock"], theirs)
        self.assertEqual((seen["text"], seen["is_query"]), ("hello", True))

    def test_saboteur_a_passed_lock_never_reads_the_global_path(self):
        import embedding_upgrade as eu
        theirs = self.el.catalogue_tiers()["gpu"]
        (self.d / "embedder.lock.json").write_text("{not json", encoding="utf-8")
        with mock.patch.object(eu, "encode_locked", lambda text, *, is_query, lock: b"x"):
            self.assertEqual(self.cm._encode("hello", lock=theirs), b"x")

    def test_control_the_caller_on_another_brain_gets_that_brains_lock(self):
        """memory_write.default_encoder, on a connection to a qwen brain, while DB_PATH is a nomic brain."""
        import embedding_upgrade as eu
        import schema_migrator
        other = self.d / "other"
        other.mkdir()
        odb = other / "claude_memory.db"
        schema_migrator.apply(str(odb), MIGRATIONS)
        olock = self.el.write_lock(other, "gpu")
        c = sqlite3.connect(str(odb))
        seen = {}

        def fake(text, *, is_query, lock):
            seen["lock"] = lock
            return _unit(1024, (0, 1.0))
        def wrong_model(*_a, **_k):
            raise AssertionError("loaded the embedder the global path names, not the connection's")
        try:
            with mock.patch.object(eu, "encode_locked", fake), \
                    mock.patch.object(eu, "get_locked_embedder", wrong_model):
                self.mw.default_encoder("words", c)
        finally:
            c.close()
        self.assertEqual((seen["lock"]["vec_table"], seen["lock"]["weights_hash"]),
                         (QWEN, olock["weights_hash"]))


class TheJinaTierKeepsItsOwnEncoder(_Jina):
    def test_control_no_lock_passed_stays_on_the_jina_branch(self):
        import embedding_upgrade as eu
        with mock.patch.object(eu, "encode_jina_document", lambda t: b"doc"), \
                mock.patch.object(eu, "encode_jina_query", lambda t: b"query"):
            self.cm._active_model_cache.clear()
            self.assertEqual(self.cm._encode("x", "search_document"), b"doc")
            self.assertEqual(self.cm._encode("x", "search_query"), b"query")


class ARefusalsCoverageReachesTheEnvelope(_Engine):
    def test_control_a_verb_that_raises_the_readers_refusal_carries_its_figures(self):
        self.foreign("only foreign", self.v((0, 1.0)))

        def raises(_args):
            self.mw.provenance_coverage(self.conn)
        with mock.patch.object(self.cm, "cmd_stats", raises):
            rc, out = self.verb("stats")
        self.assertEqual((rc, out["error"]), (1, "NO_ROWS_FOR_LOCK"), out)
        self.assertEqual((out["coverage"]["matched"], out["coverage"]["total"]), (0, 1))


if __name__ == "__main__":
    unittest.main()
