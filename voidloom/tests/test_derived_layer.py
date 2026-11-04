"""The derived layer: rows computed from vectors say which model they were computed under.

A link strength from a cosine distance, a community's membership and a community
summary all change meaning when the model changes, and vector coverage cannot see
them. Each writer stamps the row with the lock's checksum; `derived_layer_report`
counts stale, unstamped and current rows, and says how many old episodic memories a
locked brain cannot consolidate until they are re-embedded. Real brains here: every
migration in this tree, the real writers.
"""
from __future__ import annotations

import re
import sqlite3
import struct
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "organs"))
sys.path.insert(0, str(ROOT))

import embedder_lock as el  # noqa: E402
import memory_write as mw  # noqa: E402
import schema_migrator  # noqa: E402

MIGRATIONS = ROOT / "migrations"
FOREIGN = "a" * 64


def _vec_conn(db):
    import sqlite_vec
    c = sqlite3.connect(str(db))
    c.enable_load_extension(True)
    sqlite_vec.load(c)
    c.enable_load_extension(False)
    return c


def _stamp(s):
    return struct.pack("768f", *([float(len(s) % 7 + 1)] + [0.0] * 767))


class _Brain(unittest.TestCase):
    LOCK = "cpu"

    def setUp(self):
        el.clear_loaded()
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)
        self.db = self.d / "claude_memory.db"
        out = schema_migrator.apply(str(self.db), MIGRATIONS)
        self.assertEqual(out.get("failed") or [], [], out)
        self.lock = el.write_lock(self.d, self.LOCK) if self.LOCK else None
        self.h = self.lock["weights_hash"] if self.lock else None
        self.conn = _vec_conn(self.db)
        self.conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS memory_embeddings USING vec0("
                          "memory_id INTEGER PRIMARY KEY, embedding float[768])")
        if not self.lock:
            self.conn.execute("INSERT OR REPLACE INTO metadata (key, value) "
                              "VALUES ('active_embedding_table', 'memory_embeddings')")
        for i in range(1, 8):
            self.conn.execute("INSERT INTO memories (content, summary, category, importance) "
                              "VALUES (?, ?, 'insight', 0.5)", (f"m{i}", f"m{i}"))
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        el.clear_loaded()
        self.tmp.cleanup()

    def link(self, s, t, h=None):
        self.conn.execute("INSERT INTO memory_links (source_id, target_id, link_type, strength, weights_hash) "
                          "VALUES (?, ?, 'related', 0.7, ?)", (s, t, h))

    def plant(self):
        """Two links under the lock, three under another model, one a person asserted, one foreign community."""
        self.link(1, 2, self.h)
        self.link(1, 3, self.h)
        for s, t in ((2, 3), (2, 4), (3, 4)):
            self.link(s, t, FOREIGN)
        self.link(4, 5)
        self.conn.execute("INSERT INTO memory_communities (community_id, epoch, method, size, weights_hash) "
                          "VALUES (1, 1, 'connected_components', 3, ?)", (FOREIGN,))
        self.conn.commit()

    def episodic(self, age_days=1, stamp=None, archived=0, vector=True):
        """One episodic memory with a vector. `stamp` is the sidecar's weights_hash, None for no stamp row."""
        when = (datetime.now(timezone.utc) - timedelta(days=age_days)).strftime("%Y-%m-%d %H:%M:%S")
        mid = self.conn.execute(
            "INSERT INTO memories (content, summary, category, kind, importance, is_archived, created_at) "
            "VALUES ('e', 'e', 'insight', 'episodic', 0.5, ?, ?)", (archived, when)).lastrowid
        if vector:
            self.conn.execute("INSERT INTO memory_embeddings (memory_id, embedding) VALUES (?, ?)", (mid, _stamp("e")))
        if stamp is not None:
            self.conn.execute(
                "INSERT INTO embedding_provenance (memory_id, vec_table, model_id, model_ref, weights_hash, dim, "
                "content_hash, embedded_at, organ, input_kind) VALUES (?, 'memory_embeddings', 'm', 'r', ?, 768, ?, "
                "'now', 't', 'text')", (mid, stamp, mw.content_hash("e")))
        self.conn.commit()
        return mid


class Report(_Brain):
    def test_control_stale_unstamped_and_current_per_table(self):
        self.plant()
        rep = mw.derived_layer_report(self.conn)
        self.assertEqual(rep["lock_weights_hash"], self.h)
        t = rep["tables"]["memory_links"]
        self.assertEqual((t["total"], t["current"], t["stale"], t["unstamped"]), (6, 2, 3, 1))
        self.assertEqual(t["histogram"], {self.h: 2, FOREIGN: 3})
        self.assertEqual(rep["tables"]["memory_communities"]["stale"], 1)
        self.assertEqual(rep["tables"]["community_summaries"]["total"], 0)
        self.assertEqual((rep["stale_derived"], rep["unstamped"], rep["current"], rep["total"]), (4, 1, 2, 7))
        self.assertEqual(rep["unmeasured"], [])
        self.assertIn("4 stale", rep["line"])

    def test_saboteur_the_model_moves_under_a_derived_row_and_it_reads_stale(self):
        """A model change: nothing about the row changes, the checksum it is compared to does."""
        self.link(1, 2, self.h)
        self.conn.commit()
        self.assertEqual(mw.derived_layer_report(self.conn)["stale_derived"], 0)
        (self.d / el.LOCK_NAME).unlink()
        el.write_lock(self.d, "gpu")
        rep = mw.derived_layer_report(self.conn)
        self.assertEqual((rep["stale_derived"], rep["current"]), (1, 0))

    def test_saboteur_an_empty_string_stamp_is_unstamped_never_current_or_stale(self):
        self.link(1, 2, "")
        self.conn.commit()
        rep = mw.derived_layer_report(self.conn)
        self.assertEqual((rep["stale_derived"], rep["unstamped"], rep["current"]), (0, 1, 0))

    def test_saboteur_without_the_column_the_layer_is_unmeasured_never_zero(self):
        self.plant()
        self.conn.execute("ALTER TABLE memory_links DROP COLUMN weights_hash")
        self.conn.commit()
        rep = mw.derived_layer_report(self.conn)
        t = rep["tables"]["memory_links"]
        self.assertIsNone(t["stale"])
        self.assertEqual(t["total"], 6)
        self.assertTrue(any("memory_links" in u for u in rep["unmeasured"]))
        self.assertIn("UNMEASURED", rep["line"])
        self.assertIsNone(mw.derived_stamp(self.conn, "memory_links"))

    def test_saboteur_a_table_that_is_gone_is_unmeasured_never_zero(self):
        self.conn.execute("DROP TABLE community_summaries")
        self.conn.commit()
        rep = mw.derived_layer_report(self.conn)
        self.assertFalse(rep["tables"]["community_summaries"]["present"])
        self.assertIsNone(rep["tables"]["community_summaries"]["stale"])
        self.assertIn("community_summaries", rep["line"])

    def test_a_malformed_lock_is_named_and_counts_nothing_current(self):
        self.plant()
        (self.d / el.LOCK_NAME).write_text("{ not json", encoding="utf-8")
        rep = mw.derived_layer_report(self.conn)
        self.assertIn("LOCK_MALFORMED", rep["basis"])
        self.assertEqual((rep["current"], rep["stale_derived"]), (0, 6))

    def test_the_stamp_answers_the_locks_checksum(self):
        for t in mw.DERIVED_TABLES:
            self.assertEqual(mw.derived_stamp(self.conn, t), self.h, t)

    def test_the_report_writes_nothing(self):
        self.plant()
        before = self.conn.total_changes
        mw.derived_layer_report(self.conn)
        self.assertEqual(self.conn.total_changes, before)

    def test_it_reads_through_a_read_only_connection(self):
        self.plant()
        self.episodic(stamp=mw.WEIGHTS_HASH_UNRESOLVED)
        import sqlite_vec
        ro = sqlite3.connect(self.db.resolve().as_uri() + "?mode=ro", uri=True)
        ro.enable_load_extension(True)
        sqlite_vec.load(ro)
        try:
            rep = mw.derived_layer_report(ro)
        finally:
            ro.close()
        self.assertEqual((rep["stale_derived"], rep["consolidation"]["legacy_held"]), (4, 1))


class NoLock(_Brain):
    LOCK = None

    def test_control_a_row_written_with_no_lock_stays_unstamped(self):
        self.assertIsNone(mw.derived_stamp(self.conn, "memory_links"))
        self.link(1, 2)
        self.conn.commit()
        rep = mw.derived_layer_report(self.conn)
        self.assertEqual((rep["stale_derived"], rep["unstamped"], rep["current"]), (0, 1, 0))
        self.assertIn("no lock", rep["basis"])

    def test_saboteur_a_stamp_left_by_a_lock_that_is_gone_is_not_current(self):
        """A rollback to a tier with no lock leaves rows stamped under the model it left."""
        self.link(1, 2, FOREIGN)
        self.conn.commit()
        rep = mw.derived_layer_report(self.conn)
        self.assertEqual((rep["stale_derived"], rep["current"]), (1, 0))

    def test_every_writer_keeps_writing(self):
        import communities
        communities._persist_partition(self.conn, 1, "connected_components",
                                       [{"community_id": 7, "size": 2, "members": [1, 2], "member_edge_hash": "x"}],
                                       1, None)
        self.assertEqual(self.conn.execute("SELECT weights_hash FROM memory_communities").fetchall(), [(None,)])

    def test_nothing_is_held_out_of_consolidation_without_a_lock(self):
        self.episodic()
        self.episodic(stamp=mw.WEIGHTS_HASH_UNRESOLVED)
        c = mw.derived_layer_report(self.conn)["consolidation"]
        self.assertEqual((c["legacy_held"], c["legacy_held_recent"]), (0, 0))
        self.assertIn("no lock", c["basis"])


class Writers(_Brain):
    def _partition(self):
        import communities
        communities._persist_partition(self.conn, 1, "connected_components",
                                       [{"community_id": 7, "size": 3, "members": [1, 2, 3], "member_edge_hash": "x"}],
                                       1, None)
        return communities

    def test_control_a_partition_carries_the_locks_checksum(self):
        self._partition()
        self.assertEqual(self.conn.execute("SELECT weights_hash FROM memory_communities").fetchall(), [(self.h,)])

    def test_control_a_community_summary_carries_the_locks_checksum(self):
        communities = self._partition()
        res = communities.compute_summaries(self.conn, epoch=1, force=True)
        self.assertEqual(res["regenerated"], 1, res)
        self.assertEqual(self.conn.execute("SELECT weights_hash FROM community_summaries").fetchall(), [(self.h,)])

    def _summarised(self, stamp):
        """A partition whose summary is cached, its stamp set to `stamp`."""
        communities = self._partition()
        communities.compute_summaries(self.conn, epoch=1, force=True)
        self.conn.execute("UPDATE community_summaries SET weights_hash = ?", (stamp,))
        self.conn.commit()
        return communities

    def test_saboteur_a_summary_from_another_model_does_not_stay_stale_for_good(self):
        """The cache key is the members' edges, so after a model change a matching key would keep the old stamp."""
        communities = self._summarised(FOREIGN)
        res = communities.compute_summaries(self.conn, epoch=1)
        self.assertEqual(res["regenerated"], 1, res)
        self.assertEqual(self.conn.execute("SELECT weights_hash FROM community_summaries").fetchall(), [(self.h,)])
        self.assertEqual(mw.derived_layer_report(self.conn)["stale_derived"], 0)

    def test_control_a_summary_under_the_lock_stays_cached(self):
        communities = self._summarised(self.h)
        res = communities.compute_summaries(self.conn, epoch=1)
        self.assertEqual((res["regenerated"], res["skipped"]), (0, 1), res)

    def test_control_an_unstamped_summary_is_not_rewritten_in_bulk(self):
        """Old rows carry no stamp and nothing says they are wrong: a brain's first night under a lock leaves them."""
        communities = self._summarised(None)
        res = communities.compute_summaries(self.conn, epoch=1)
        self.assertEqual((res["regenerated"], res["skipped"]), (0, 1), res)

    def test_saboteur_a_brain_without_the_column_still_writes_its_partition(self):
        self.conn.execute("ALTER TABLE memory_communities DROP COLUMN weights_hash")
        self.conn.commit()
        self._partition()
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM memory_communities").fetchone()[0], 1)

    def test_control_the_dreams_links_carry_the_locks_checksum(self):
        """The real organ, with a stand-in encoder registered under the lock."""
        import memory_dreaming
        el.register_loaded(model_id=self.lock["model_id"], revision=self.lock["revision"],
                           weights_hash=self.h, dim=768, vec_table="memory_embeddings", tier="cpu")
        keep = mw.default_encoder
        mw.default_encoder = lambda text, conn=None: _stamp(text)
        try:
            mem_id = memory_dreaming._store_dream_as_reflection(self.conn, "a dream that joins the two", 1, 2, "divergent")
        finally:
            mw.default_encoder = keep
        self.assertIsNotNone(mem_id)
        rows = self.conn.execute("SELECT link_type, weights_hash FROM memory_links WHERE source_id = ?", (mem_id,)).fetchall()
        self.assertEqual(rows, [("elaborates", self.h), ("elaborates", self.h)])
        self.assertEqual(mw.derived_layer_report(self.conn)["tables"]["memory_links"]["current"], 2)

    def test_saboteur_a_dream_on_a_brain_with_no_stamp_to_give_still_links(self):
        import memory_dreaming
        self.conn.execute("ALTER TABLE memory_links DROP COLUMN weights_hash")
        self.conn.commit()
        el.register_loaded(model_id=self.lock["model_id"], revision=self.lock["revision"],
                           weights_hash=self.h, dim=768, vec_table="memory_embeddings", tier="cpu")
        keep = mw.default_encoder
        mw.default_encoder = lambda text, conn=None: _stamp(text)
        try:
            mem_id = memory_dreaming._store_dream_as_reflection(self.conn, "a dream", 1, 2, "divergent")
        finally:
            mw.default_encoder = keep
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM memory_links WHERE source_id = ?", (mem_id,)).fetchone()[0], 2)


class EveryWriterIsAccountedFor(unittest.TestCase):
    """Who may write the three derived tables. A new writer has to be placed in one of these rows."""
    STAMPS = {"organs/claude_memory.py", "organs/communities.py", "organs/memory_dreaming.py"}
    # website_memory writes website_memory.db: no lock beside it, no weights_hash column there.
    OWN_DB = {"organs/website_memory.py"}

    def test_the_sweep_finds_exactly_the_known_writers(self):
        found = set()
        for top in ("organs", "voidloom", "tools", "install"):
            for p in (ROOT / top).rglob("*.py"):
                if "tests" in p.relative_to(ROOT).parts:
                    continue
                s = p.read_text(encoding="utf-8", errors="replace")
                if re.search(r"INTO\s+(memory_links|memory_communities|community_summaries)\b", s):
                    found.add(str(p.relative_to(ROOT)))
        self.assertEqual(found, self.STAMPS | self.OWN_DB)


class OutsideConsolidation(_Brain):
    """A brain pinned by `init` keeps its old vectors searchable, but a centroid over them could not carry the
    lock's checksum, so they do not consolidate until re-embedded. That figure ships in the report."""

    def held(self):
        return mw.derived_layer_report(self.conn)["consolidation"]

    def test_control_the_legacy_episodics_are_counted_with_the_recent_ones_beside_them(self):
        self.episodic(age_days=1)                                     # no stamp at all
        self.episodic(age_days=2, stamp=mw.WEIGHTS_HASH_UNRESOLVED)   # the declared absence
        self.episodic(age_days=90, stamp=mw.WEIGHTS_HASH_UNRESOLVED)  # old: held, outside tonight's window
        c = self.held()
        self.assertEqual((c["legacy_held"], c["legacy_held_recent"]), (3, 2))
        self.assertEqual(c["lookback_days"], mw.CONSOLIDATION_LOOKBACK_DAYS)
        self.assertIn("re-embed", c["hint"])
        self.assertIn("3 ", mw.derived_layer_report(self.conn)["line"])

    def test_saboteur_a_verified_row_is_not_held(self):
        self.episodic(stamp=self.h)
        self.assertEqual(self.held()["legacy_held"], 0)

    def test_saboteur_what_consolidation_could_never_read_is_not_held(self):
        self.episodic(archived=1)
        self.episodic(vector=False)
        self.conn.execute("UPDATE memories SET kind = 'semantic' WHERE id = ?", (self.episodic(),))
        self.conn.commit()
        self.assertEqual(self.held()["legacy_held"], 0)

    def test_saboteur_a_memory_already_consolidated_is_not_waiting(self):
        mid = self.episodic()
        self.conn.execute("INSERT INTO consolidation_sources (consolidated_memory_id, source_memory_id, cluster_id) "
                          "VALUES (1, ?, 1)", (mid,))
        self.conn.commit()
        self.assertEqual(self.held()["legacy_held"], 0)

    def test_saboteur_a_null_in_the_ledger_cannot_hide_the_rest(self):
        """NOT IN over a NULL is the empty set: one bad row must not read as nothing held."""
        self.episodic()
        self.conn.execute("CREATE TEMP TABLE keep AS SELECT * FROM consolidation_sources")
        self.conn.execute("DROP TABLE consolidation_sources")
        self.conn.execute("CREATE TABLE consolidation_sources (id INTEGER PRIMARY KEY, consolidated_memory_id INT, "
                          "source_memory_id INT, cluster_id INT)")
        self.conn.execute("INSERT INTO consolidation_sources (source_memory_id) VALUES (NULL)")
        self.conn.commit()
        self.assertEqual(self.held()["legacy_held"], 1)

    def test_saboteur_a_connection_without_vec0_is_unmeasured_never_zero(self):
        self.episodic()
        plain = sqlite3.connect(str(self.db))
        try:
            rep = mw.derived_layer_report(plain)
        finally:
            plain.close()
        c = rep["consolidation"]
        self.assertIsNone(c["legacy_held"])
        self.assertIsNone(c["legacy_held_recent"])
        self.assertTrue(any("consolidation" in u for u in rep["unmeasured"]), rep["unmeasured"])
        self.assertIn("UNMEASURED", rep["line"])
        self.assertEqual(rep["total"], 0)                   # the derived tables were still read

    def test_saboteur_a_malformed_lock_is_unmeasured_never_zero(self):
        self.episodic()
        (self.d / el.LOCK_NAME).write_text("{ not json", encoding="utf-8")
        c = self.held()
        self.assertIsNone(c["legacy_held"])
        self.assertIn("LOCK_MALFORMED", c["basis"])

    def test_the_figure_is_the_one_the_sleep_log_gives(self):
        import sleep_loop
        self.episodic(age_days=1)
        self.episodic(age_days=90)
        flt = mw.provenance_filter(self.conn)
        cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
        self.assertEqual(sleep_loop._legacy_episodics_held(self.conn, flt, cutoff), self.held()["legacy_held_recent"])

    def test_the_default_window_is_the_nights_window(self):
        import sleep_loop
        self.assertEqual(sleep_loop.DEFAULT_CONFIG["lookback_days"], mw.CONSOLIDATION_LOOKBACK_DAYS)


class NoPerRowLookup(_Brain):
    """The report runs on every `doctor` and `status`, so it must not do work per row. Read from what SQLite was
    actually asked: the statements the report ran, their plans, and their number against the number of rows."""

    def _statements(self):
        seen = []
        self.conn.set_trace_callback(seen.append)
        try:
            mw.derived_layer_report(self.conn)
        finally:
            self.conn.set_trace_callback(None)
        return [s for s in seen if s.lstrip().upper().startswith("SELECT")]

    def _plan_details(self, statement):
        return [r[3] for r in self.conn.execute("EXPLAIN QUERY PLAN " + statement).fetchall()]

    def _fill(self, n):
        for i in range(n):
            mid = self.episodic(stamp=mw.WEIGHTS_HASH_UNRESOLVED if i % 2 else None)
            self.link(mid, 1, FOREIGN if i % 3 else self.h)
        self.conn.commit()

    def test_no_statement_has_a_correlated_subquery(self):
        self._fill(5)
        statements = self._statements()
        self.assertGreaterEqual(len(statements), len(mw.DERIVED_TABLES) + 1)
        for s in statements:
            for detail in self._plan_details(s):
                self.assertNotIn("CORRELATED", detail, s)

    def test_the_detector_fires_on_the_shape_it_forbids(self):
        planted = ("SELECT COUNT(*) FROM memories m WHERE EXISTS "
                   "(SELECT 1 FROM memory_links l WHERE l.source_id = m.id)")
        self.assertTrue(any("CORRELATED" in d for d in self._plan_details(planted)))

    def test_the_statement_count_does_not_grow_with_the_rows(self):
        self._fill(3)
        small = len(self._statements())
        self._fill(60)
        self.assertEqual(len(self._statements()), small)


if __name__ == "__main__":
    unittest.main()
