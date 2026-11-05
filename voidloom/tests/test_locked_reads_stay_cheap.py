"""A locked brain's per-row reads must not measure the whole brain on every call.

A per-row reader that derived the search set by full coverage hashed every memory's text per call: 0.53 s each at 31,904
memories. A brain small enough for a test cannot show a time, so this counts: no per-row path reaches the full measure,
and the refusals that measure used to raise are still raised.
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

from voidloom.tests import test_tier_set as tt  # noqa: E402


@unittest.skipUnless(tt._vec_available(), "needs sqlite_vec")
class LockedReads(unittest.TestCase):
    def setUp(self):
        import memory_write as mw
        self.mw = mw
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        self.d = Path(self.td.name).resolve()
        self.db = tt.make_brain(self.d, "nomic", n=20)
        self.c = tt._connect(self.db)
        self.addCleanup(self.c.close)

    def test_no_per_row_reader_reaches_the_full_measure(self):
        real = self.mw._lock_coverage
        with mock.patch.object(self.mw, "_lock_coverage", side_effect=real) as measured:
            for i in range(1, 11):
                self.assertIsNotNone(self.mw.read_vector(self.c, i))
            self.mw.read_vectors(self.c, [1, 3, 4])
            v = self.mw.read_vector(self.c, 1)
            self.mw.knn(self.c, v, 3)
            self.mw.search_set(self.c)
        self.assertEqual(measured.call_count, 0)

    def test_a_recall_does_not_pay_for_the_content_hash_sweep(self):
        calls = []
        self.c.set_trace_callback(calls.append)
        env = self.mw.coverage_envelope(self.c, detail=False)
        self.c.set_trace_callback(None)
        self.assertTrue(env["ok"], env)
        self.assertIsNone(env["stale_content_hash"])
        self.assertFalse([q for q in calls if "vl_sha256" in q])

    def test_doctor_still_gets_the_stale_count(self):
        self.c.execute("UPDATE memories SET content = 'edited' WHERE id = 1")
        self.assertEqual(self.mw.coverage_envelope(self.c)["stale_content_hash"], 1)

    def test_every_refusal_of_the_full_measure_still_refuses(self):
        import embedder_lock as el
        whash = el.resolve(self.c)["weights_hash"]
        # a stamp carrying the lock's checksum on another width
        self.c.execute("UPDATE embedding_provenance SET dim = 1 WHERE memory_id = 1")
        with self.assertRaises(self.mw.ProvenanceInconsistent):
            self.mw.search_set(self.c)
        self.c.execute("UPDATE embedding_provenance SET dim = 768 WHERE memory_id = 1")
        self.assertEqual(self.mw.search_set(self.c).weights_hash, whash)
        # nothing in the set
        self.c.execute("UPDATE embedding_provenance SET weights_hash = 'other'")
        with self.assertRaises(self.mw.ProvenanceUnmatched):
            self.mw.search_set(self.c)

    def test_an_empty_brain_is_still_none(self):
        self.c.executescript("DELETE FROM memories; DELETE FROM memory_embeddings; DELETE FROM embedding_provenance;")
        self.assertIsNone(self.mw.search_set(self.c))

    def test_a_connection_without_vec0_still_reads_unreadable(self):
        bare = sqlite3.connect(str(self.db))
        self.addCleanup(bare.close)
        with self.assertRaises(self.mw.ProvenanceError):
            self.mw.search_set(bare)

    def test_the_edge_pass_derives_its_filter_once(self):
        import temporal_edges
        calls = []
        real = self.mw.search_set

        def counting(conn):
            calls.append(1)
            return real(conn)
        flt = real(self.c)
        with mock.patch.object(self.mw, "search_set", side_effect=counting):
            temporal_edges._get_memory_embedding(self.c, 1, flt)
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
