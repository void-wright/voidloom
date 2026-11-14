"""An unstamped table is not a brain's own legacy when the brain holds stamps under another checksum.

THE CLAIM UNDER TEST
  * a locked brain searches its verified rows and its legacy rows: a vector in the lock's own table whose stamp is
    'unresolved', or that has no stamp there, beside any foreign row (another checksum), which stays outside;
  * the lock's own table is one that carries a stamp under the lock's checksum or 'unresolved' -- or any table of a
    brain that holds no stamp under another checksum, which never said otherwise. A brain that holds stamps under
    another checksum and none for this table has not made it its own. The sibling trap: a lock re-pointed at another
    tier's table without re-embedding finds no rows there and refuses, instead of serving its unstamped vectors as
    legacy;
  * the stdlib reader's coverage and the legacy-episodic count draw the same line as the engine's reader.
"""
from __future__ import annotations

import sqlite3
import struct
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "organs") not in sys.path:
    sys.path.insert(0, str(ROOT / "organs"))

from voidloom.tests.test_doctor_brain_verdict import NOMIC, NOMIC_DIM, _Brain, el, mw  # noqa: E402

V3, V3_DIM = "memory_embeddings_v3", 1024


class _Stamped(_Brain):
    """A locked cpu brain: four memories, each with a vector in the cpu table stamped under the lock."""

    def setUp(self):
        super().setUp()
        self.ids = [self.memory(f"memory {i} about tides", whash=self.h) for i in range(4)]

    def search_ids(self):
        flt = mw.search_set(self.conn)
        return sorted(r[0] for r in self.conn.execute(
            f"SELECT emb.memory_id FROM {flt.vec_table} emb WHERE emb.{flt.search_set_sql}", flt.search_set_params))

    def lite_matched(self):
        import claude_memory_lite as lite
        saved = lite.DB_PATH
        lite.DB_PATH = self.db
        try:
            c = sqlite3.connect(str(self.db))
            try:
                return lite._lock_coverage(c)
            finally:
                c.close()
        finally:
            lite.DB_PATH = saved

    def unstamped_v3(self, ids):
        self.conn.execute(f"CREATE VIRTUAL TABLE IF NOT EXISTS {V3} USING vec0(memory_id INTEGER PRIMARY KEY, "
                          f"embedding FLOAT[{V3_DIM}])")
        for mid in ids:
            self.conn.execute(f"INSERT OR REPLACE INTO {V3} (memory_id, embedding) VALUES (?, ?)",
                              (mid, struct.pack(f"{V3_DIM}f", *([0.02] * V3_DIM))))
        self.conn.commit()

    def repoint_to_gpu(self):
        el.write_lock(self.d, "gpu", replace=True, brain=self.conn)
        self.conn.close()
        self.conn = self.connect()


class TheSiblingTrap(_Stamped):
    def test_saboteur_a_lock_repointed_without_re_embedding_refuses_rather_than_serving_the_table(self):
        self.unstamped_v3(self.ids[:2])          # those memories hold cpu stamps on the cpu table, none on v3
        self.repoint_to_gpu()
        cov = mw.coverage_envelope(self.conn, detail=False)
        self.assertEqual((cov["ok"], cov.get("error"), cov.get("matched")), (False, "NO_ROWS_FOR_LOCK", 0), cov)
        with self.assertRaises(mw.ProvenanceUnmatched):
            mw.search_set(self.conn)
        lite = self.lite_matched()
        self.assertEqual((lite["matched"], lite.get("error")), (0, "NO_ROWS_FOR_LOCK"), lite)

    def test_saboteur_memories_with_no_stamp_anywhere_are_not_legacy_either(self):
        bare = [self.memory(f"bare {i}", vector=False) for i in range(2)]
        self.unstamped_v3(bare)
        self.repoint_to_gpu()
        cov = mw.coverage_envelope(self.conn, detail=False)
        self.assertEqual((cov["ok"], cov.get("error")), (False, "NO_ROWS_FOR_LOCK"), cov)

    def test_saboteur_a_repointed_table_whose_only_stamp_is_foreign_is_not_made_its_own(self):
        self.unstamped_v3(self.ids[:2])
        self.repoint_to_gpu()
        self.stamp(self.ids[0], "a" * 64, table=V3, dim=V3_DIM)     # one stamp on v3, under neither the lock nor 'unresolved'
        cov = mw.coverage_envelope(self.conn, detail=False)
        self.assertEqual((cov["ok"], cov.get("error")), (False, "NO_ROWS_FOR_LOCK"), cov)
        self.assertEqual(self.lite_matched()["matched"], 0)


class TheLegacyDoor(_Stamped):
    def test_control_an_unstamped_vector_beside_a_foreign_row_in_the_locks_own_table_stays_legacy(self):
        self.conn.execute(f"DELETE FROM {mw.PROVENANCE_TABLE} WHERE memory_id = ?", (self.ids[0],))
        self.stamp(self.ids[1], "a" * 64)        # another checksum, on this very table: that row alone is foreign
        self.assertEqual(self.search_ids(), [self.ids[0], *self.ids[2:]])
        cov = mw.coverage_envelope(self.conn, detail=False)
        self.assertEqual((cov["matched"], cov["legacy_unverified"]["legacy"]), (3, 1), cov)
        self.assertEqual(self.lite_matched()["matched"], 3)

    def test_control_a_brain_with_no_stamp_at_all_keeps_every_vector_as_legacy(self):
        self.conn.execute(f"DELETE FROM {mw.PROVENANCE_TABLE}")
        self.conn.commit()
        self.assertEqual(self.search_ids(), self.ids)
        self.assertEqual(self.lite_matched()["matched"], 4)

    def test_control_an_unstamped_vector_on_a_brain_with_no_other_checksum_is_legacy_and_searched(self):
        self.conn.execute(f"DELETE FROM {mw.PROVENANCE_TABLE} WHERE memory_id = ?", (self.ids[0],))
        self.conn.commit()
        self.assertEqual(self.search_ids(), self.ids)
        cov = mw.coverage_envelope(self.conn, detail=False)
        self.assertEqual((cov["matched"], cov["verified"], cov["legacy_unverified"]["legacy"]), (4, 3, 1), cov)
        self.assertEqual(self.lite_matched()["matched"], 4)

    def test_control_an_unresolved_stamp_stays_legacy_beside_a_foreign_stamp(self):
        self.stamp(self.ids[0], mw.WEIGHTS_HASH_UNRESOLVED)
        self.stamp(self.ids[1], "a" * 64)
        self.assertEqual(self.search_ids(), [self.ids[0], *self.ids[2:]])
        cov = mw.coverage_envelope(self.conn, detail=False)
        self.assertEqual((cov["matched"], cov["legacy_unverified"]["legacy"]), (3, 1), cov)
        self.assertEqual(self.lite_matched()["matched"], 3)

    def test_control_the_repoint_onto_vectors_stamped_under_the_new_lock_is_served(self):
        self.unstamped_v3(self.ids[:2])
        self.repoint_to_gpu()
        gpu = el.catalogue_tiers()["gpu"]["weights_hash"]
        for mid in self.ids[:2]:
            self.stamp(mid, gpu, table=V3, dim=V3_DIM)
        self.assertEqual(self.search_ids(), self.ids[:2])


class TheEpisodicCount(_Stamped):
    def episodic(self, mid):
        self.conn.execute("UPDATE memories SET kind = 'episodic' WHERE id = ?", (mid,))
        self.conn.commit()

    def held(self):
        return mw.legacy_episodics_held(self.conn, mw.provenance_filter(self.conn), "1970-01-01")[0]

    def test_control_an_unstamped_episodic_is_held_out_of_consolidation(self):
        self.episodic(self.ids[0])
        self.conn.execute(f"DELETE FROM {mw.PROVENANCE_TABLE} WHERE memory_id = ?", (self.ids[0],))
        self.conn.commit()
        self.assertEqual(self.held(), 1)

    def test_saboteur_an_unstamped_vector_in_a_repointed_table_is_not_held_as_legacy(self):
        self.episodic(self.ids[0])
        self.unstamped_v3(self.ids[:1])
        self.repoint_to_gpu()
        self.assertEqual(self.held(), 0)


if __name__ == "__main__":
    unittest.main()

