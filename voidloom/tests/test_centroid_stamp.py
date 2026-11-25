"""The nightly centroid carries a stamp, on every kind of brain.

consolidate_episodics averages member vectors and writes the result itself, so no
embedder stamps it. These tests run the real organ over a real brain file:

  locked    the row is stamped input_kind 'centroid' under the lock's checksum, every
            member under that checksum; a member outside it is never clustered, and a
            write that slips past selection is refused and rolled back whole;
  pre-lock  the brain keeps writing, never a refusal: the checksum the members agree
            on, else 'unresolved';
  jina      a tier the catalogue does not ship: no lock path, stamped 'unresolved'.

No model is loaded: vectors are fixed and the checksum is registered.
"""
from __future__ import annotations

import json
import math
import random
import sqlite3
import struct
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "organs"))

import embedder_lock as el  # noqa: E402
import marks  # noqa: E402
import memory_write as mw  # noqa: E402
import sleep_loop  # noqa: E402

NOMIC, NOMIC_DIM = "memory_embeddings", 768
JINA, JINA_DIM = "memory_embeddings_v2", 1024
MIGRATIONS = ROOT / "migrations"

_SCHEMA = """
CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT, updated_at TEXT DEFAULT (datetime('now')));
CREATE TABLE memories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    content TEXT NOT NULL, summary TEXT, category TEXT DEFAULT 'project_knowledge', importance REAL DEFAULT 0.5,
    source_project TEXT, source_context TEXT, kind TEXT DEFAULT 'semantic', confidence REAL DEFAULT 1.0,
    derived_from TEXT, consolidated_at TEXT, is_archived INTEGER DEFAULT 0, access_count INTEGER DEFAULT 0,
    last_accessed TEXT, created_at TEXT DEFAULT (datetime('now')), updated_at TEXT DEFAULT (datetime('now')),
    version INTEGER DEFAULT 1
);
CREATE TABLE memory_tags (memory_id INTEGER, tag TEXT, UNIQUE(memory_id, tag));
"""


def _near(rng, dim):
    """A cluster's worth of vectors: unit vectors a small step from one axis."""
    base = [0.0] * dim
    base[0] = 1.0
    v = [b + rng.gauss(0, 0.01 / math.sqrt(dim)) for b in base]
    n = math.sqrt(sum(x * x for x in v))
    return struct.pack(f"{dim}f", *[x / n for x in v])


class _Brain(unittest.TestCase):
    TABLE, DIM = NOMIC, NOMIC_DIM

    def setUp(self):
        el.clear_loaded()
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)
        self.db = self.d / "claude_memory.db"
        c = sqlite3.connect(str(self.db))
        c.executescript(_SCHEMA)
        c.execute(f"CREATE TABLE {self.TABLE} (memory_id INTEGER PRIMARY KEY, embedding BLOB)")
        c.execute("INSERT INTO metadata (key, value) VALUES ('active_embedding_table', ?)", (self.TABLE,))
        c.executescript((MIGRATIONS / "migration_005_sleep_loop.sql").read_text())
        mw.ensure_provenance_table(c)
        marks.fixture_schema(c)                 # a centroid is a derived row: it needs somewhere to record its parents
        c.commit()
        self.conn = c
        self.rng = random.Random(7)

    def tearDown(self):
        self.conn.close()
        el.clear_loaded()
        self.tmp.cleanup()

    def episodic(self, stamp=None, *, vec=None) -> int:
        """One episodic memory with a vector; `stamp` is the weights_hash its stamp carries (None: no stamp)."""
        t = (datetime.now(timezone.utc) - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
        text = f"a thing that happened {self.rng.random()}"
        cur = self.conn.execute(
            "INSERT INTO memories (content, summary, kind, created_at) VALUES (?, ?, 'episodic', ?)", (text, "s", t))
        mid = int(cur.lastrowid)
        self.conn.execute(f"INSERT INTO {self.TABLE} (memory_id, embedding) VALUES (?, ?)",
                          (mid, vec if vec is not None else _near(self.rng, self.DIM)))
        if stamp is not None:
            self.conn.execute(
                f"INSERT INTO {mw.PROVENANCE_TABLE} ({', '.join(mw.PROVENANCE_COLUMNS)}) "
                "VALUES (?, ?, 'm', 'r', ?, ?, ?, '2026-01-01', 't', 'text', NULL)",
                (mid, self.TABLE, stamp, self.DIM, mw.content_hash(text)))
        self.conn.commit()
        return mid

    def consolidate(self):
        return sleep_loop.consolidate_episodics(self.conn, lookback_days=7, similarity_threshold=0.6)

    def stamp_of(self, mid):
        return self.conn.execute(
            "SELECT weights_hash, input_kind, inputs, dim, vec_table, organ, content_hash, model_id "
            f"FROM {mw.PROVENANCE_TABLE} WHERE memory_id = ?", (mid,)).fetchone()

    def assert_centroid_of(self, out, members, weights_hash):
        self.assertEqual(len(out), 1, out)
        self.assertNotIn("refused", out[0])
        cid = out[0]["consolidated_memory_id"]
        self.assertIsNotNone(cid)
        s = self.stamp_of(cid)
        self.assertIsNotNone(s, "the centroid has no stamp")
        self.assertEqual((s[0], s[1], s[3], s[4], s[5]),
                         (weights_hash, "centroid", self.DIM, self.TABLE, "sleep_loop.consolidate_episodics"))
        self.assertEqual(json.loads(s[2]), sorted(members))
        blobs = [self.conn.execute(f"SELECT embedding FROM {self.TABLE} WHERE memory_id = ?", (m,)).fetchone()[0]
                 for m in sorted(members)]
        self.assertEqual(s[6], mw.centroid_content_hash(blobs), "content_hash is over member bytes in id order")
        return cid


class Locked(_Brain):
    def setUp(self):
        super().setUp()
        self.lock = el.write_lock(self.d, "cpu")
        self.h = self.lock["weights_hash"]
        self.other = el.catalogue_tiers()["gpu"]["weights_hash"]

    def test_control_a_verified_cluster_is_stamped_under_the_lock(self):
        ids = [self.episodic(self.h) for _ in range(4)]
        out = self.consolidate()
        cid = self.assert_centroid_of(out, ids, self.h)
        self.assertEqual(self.stamp_of(cid)[7], self.lock["model_id"])

    def test_control_no_embedder_needs_to_be_resident(self):
        ids = [self.episodic(self.h) for _ in range(3)]
        self.assertIsNone(el.loaded())
        self.assert_centroid_of(self.consolidate(), ids, self.h)

    def test_saboteur_a_foreign_member_is_outside_the_cluster(self):
        ids = [self.episodic(self.h) for _ in range(3)]
        foreign = self.episodic(self.other)
        out = self.consolidate()
        self.assert_centroid_of(out, ids, self.h)
        self.assertNotIn(foreign, out[0]["source_ids"])

    def test_saboteur_a_legacy_member_is_outside_the_cluster(self):
        ids = [self.episodic(self.h) for _ in range(3)]
        unstamped = self.episodic(None)
        unresolved = self.episodic(mw.WEIGHTS_HASH_UNRESOLVED)
        out = self.consolidate()
        self.assert_centroid_of(out, ids, self.h)
        self.assertFalse({unstamped, unresolved} & set(out[0]["source_ids"]))

    def test_a_pinned_brain_of_legacy_rows_writes_no_centroid_and_says_why(self):
        for _ in range(4):
            self.episodic(mw.WEIGHTS_HASH_UNRESOLVED)
        with self.assertLogs("sleep_loop", level="INFO") as logs:
            out = self.consolidate()
        self.assertEqual(out, [])
        self.assertTrue(any("4 recent episodic memories are legacy" in m for m in logs.output), logs.output)

    def test_saboteur_a_stamp_that_slips_past_selection_is_refused_and_rolled_back_whole(self):
        """The members are verified when selected and one goes foreign by the time the stamp reads it: the memory, its
        vector and its tags all go, nothing is stamped, the members stay eligible, and the night goes on."""
        ids = [self.episodic(self.h) for _ in range(3)]
        real = mw.stamp_centroid

        def race(conn, *a, **k):
            conn.execute(f"UPDATE {mw.PROVENANCE_TABLE} SET weights_hash = ? WHERE memory_id = ?", (self.other, ids[0]))
            return real(conn, *a, **k)

        before = self.conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        sleep_loop._vl_mw.stamp_centroid = race
        try:
            with self.assertLogs("sleep_loop", level="ERROR"):
                out = self.consolidate()
        finally:
            sleep_loop._vl_mw.stamp_centroid = real
        self.assertEqual(len(out), 1)
        self.assertIsNone(out[0]["consolidated_memory_id"])
        self.assertIn("CentroidRefused", out[0]["refused"])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0], before)
        self.assertEqual(self.conn.execute(f"SELECT COUNT(*) FROM {self.TABLE}").fetchone()[0], before)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM consolidation_sources").fetchone()[0], 0)
        self.assertEqual(self.conn.execute(
            f"SELECT COUNT(*) FROM {mw.PROVENANCE_TABLE} WHERE input_kind = 'centroid'").fetchone()[0], 0)

    def test_saboteur_a_stamp_for_a_table_the_lock_does_not_name_is_refused(self):
        ids = [self.episodic(self.h) for _ in range(3)]
        cid = self.episodic(self.h)
        vecs = [bytes(4)] * 3
        with self.assertRaises(mw.CentroidRefused) as cm:
            mw.stamp_centroid(self.conn, cid, JINA, JINA_DIM, ids, vecs, "t")
        self.assertEqual(cm.exception.detail["lock_vec_table"], NOMIC)

    def test_saboteur_every_refusal_names_its_members(self):
        good = [self.episodic(self.h) for _ in range(2)]
        foreign, unresolved, unstamped = (self.episodic(self.other), self.episodic(mw.WEIGHTS_HASH_UNRESOLVED),
                                          self.episodic(None))
        ids = good + [foreign, unresolved, unstamped]
        with self.assertRaises(mw.CentroidRefused) as cm:
            mw.stamp_centroid(self.conn, self.episodic(self.h), NOMIC, NOMIC_DIM, ids, [bytes(4)] * 5, "t")
        self.assertEqual(cm.exception.detail["missing"], [unstamped])
        self.assertEqual(cm.exception.detail["foreign"], sorted([foreign, unresolved]))

    def test_saboteur_an_unstamped_member_alone_refuses(self):
        ids = [self.episodic(self.h), self.episodic(self.h), self.episodic(None)]
        with self.assertRaises(mw.CentroidRefused) as cm:
            mw.stamp_centroid(self.conn, self.episodic(self.h), NOMIC, NOMIC_DIM, ids, [bytes(4)] * 3, "t")
        self.assertEqual(cm.exception.detail["missing"], [ids[2]])

    def test_saboteur_a_member_at_another_width_refuses(self):
        ids = [self.episodic(self.h) for _ in range(3)]
        self.conn.execute(f"UPDATE {mw.PROVENANCE_TABLE} SET dim = 1024 WHERE memory_id = ?", (ids[1],))
        with self.assertRaises(mw.CentroidRefused) as cm:
            mw.stamp_centroid(self.conn, self.episodic(self.h), NOMIC, NOMIC_DIM, ids, [bytes(4)] * 3, "t")
        self.assertEqual(cm.exception.detail["wrong_dim"], [ids[1]])

    def test_the_readers_refusal_propagates_when_vectors_exist(self):
        for _ in range(3):
            self.episodic(self.other)
        with self.assertRaises(mw.ProvenanceUnmatched) as cm:
            self.consolidate()
        self.assertEqual(cm.exception.code, "NO_ROWS_FOR_LOCK")

    def test_a_malformed_lock_is_never_read_as_absent(self):
        ids = [self.episodic(self.h) for _ in range(3)]
        (self.d / "embedder.lock.json").write_text("{ not json", encoding="utf-8")
        with self.assertRaises(el.EmbedderLockError) as cm:
            mw.stamp_centroid(self.conn, self.episodic(self.h), NOMIC, NOMIC_DIM, ids, [bytes(4)] * 3, "t")
        self.assertEqual(cm.exception.code, "LOCK_MALFORMED")

    def test_an_empty_locked_brain_is_nothing_to_do(self):
        self.assertEqual(self.consolidate(), [])

    def test_a_locked_brain_with_memories_and_no_vectors_is_nothing_to_do(self):
        self.conn.execute("INSERT INTO memories (content, kind) VALUES ('not embedded yet', 'episodic')")
        self.conn.commit()
        self.assertEqual(self.consolidate(), [])

    def test_a_centroid_is_not_stale_against_text_it_never_had(self):
        ids = [self.episodic(self.h) for _ in range(3)]
        self.assert_centroid_of(self.consolidate(), ids, self.h)
        self.assertEqual(mw.provenance_coverage(self.conn).stale_content_hash, 0)

    def test_the_run_reports_refused_clusters(self):
        for _ in range(3):
            self.episodic(self.h)
        real = mw.stamp_centroid

        def no(*a, **k):
            raise mw.CentroidRefused("no")

        sleep_loop._vl_mw.stamp_centroid = no
        try:
            with self.assertLogs("sleep_loop", level="ERROR"):
                out = self.consolidate()
        finally:
            sleep_loop._vl_mw.stamp_centroid = real
        self.assertEqual([bool(c.get("refused")) for c in out], [True])


class PreLock(_Brain):
    """No embedder.lock.json: every brain installed before the lock existed."""

    def setUp(self):
        super().setUp()
        self.h = el.catalogue_tiers()["cpu"]["weights_hash"]
        self.other = el.catalogue_tiers()["gpu"]["weights_hash"]
        # a brain with no lock carries a shipped checksum only by way of `re-embed`, which leaves this receipt
        (self.d / el.LOCK_HISTORY_NAME).write_text('{"action": "re-embed"}\n', encoding="utf-8")

    def test_control_members_agreeing_on_a_checksum_hand_it_on(self):
        ids = [self.episodic(self.h) for _ in range(3)]
        self.assert_centroid_of(self.consolidate(), ids, self.h)

    def test_control_unstamped_members_keep_writing_as_unresolved(self):
        ids = [self.episodic(None) for _ in range(3)]
        self.assert_centroid_of(self.consolidate(), ids, mw.WEIGHTS_HASH_UNRESOLVED)

    def test_saboteur_members_that_disagree_never_refuse_and_never_claim_a_checksum(self):
        ids = [self.episodic(self.h), self.episodic(self.other), self.episodic(None)]
        self.assert_centroid_of(self.consolidate(), ids, mw.WEIGHTS_HASH_UNRESOLVED)

    def test_saboteur_one_unstamped_member_is_not_outvoted(self):
        ids = [self.episodic(self.h), self.episodic(self.h), self.episodic(None)]
        self.assert_centroid_of(self.consolidate(), ids, mw.WEIGHTS_HASH_UNRESOLVED)

    def test_saboteur_a_member_at_another_width_never_hands_its_checksum_on(self):
        ids = [self.episodic(self.h) for _ in range(3)]
        self.conn.execute(f"UPDATE {mw.PROVENANCE_TABLE} SET dim = ?", (self.DIM + 1,))
        cid = self.episodic(None)
        stamp = mw.stamp_centroid(self.conn, cid, self.TABLE, self.DIM, ids, [bytes(4)] * 3, "t")
        self.assertEqual(stamp["weights_hash"], mw.WEIGHTS_HASH_UNRESOLVED)

    def test_a_centroid_is_not_stale_on_a_brain_with_no_lock_either(self):
        ids = [self.episodic(None) for _ in range(3)]
        cid = self.assert_centroid_of(self.consolidate(), ids, mw.WEIGHTS_HASH_UNRESOLVED)
        self.assertEqual(self.stamp_of(cid)[1], "centroid")
        self.assertEqual(mw.provenance_coverage(self.conn).stale_content_hash, 0)


class Jina(PreLock):
    """A tier the catalogue does not ship: no lock path, and it writes as it always did."""
    TABLE, DIM = JINA, JINA_DIM

    def test_control_the_stamp_names_the_tiers_own_model(self):
        ids = [self.episodic(None) for _ in range(3)]
        cid = self.assert_centroid_of(self.consolidate(), ids, mw.WEIGHTS_HASH_UNRESOLVED)
        self.assertIn("jina", self.stamp_of(cid)[7])
        self.assertFalse((self.d / "embedder.lock.json").exists(), "a jina brain must not grow a lock")


class ReadsTheSearchSet(unittest.TestCase):
    def test_consolidation_derives_its_table_from_the_reader_alone(self):
        src = (ROOT / "organs" / "sleep_loop.py").read_text(encoding="utf-8")
        self.assertNotIn("embed_resolver import", src)
        self.assertIn("_vl_mw.search_set(conn)", src)
        self.assertIn("_vl_mw.stamp_centroid(", src)


if __name__ == "__main__":
    unittest.main()
