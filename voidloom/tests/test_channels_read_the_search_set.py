"""A recall channel is a search: it reads no vector the search set keeps out.

THE CLAIM UNDER TEST
  * a row in the lock's table under another model's checksum never reaches a channel, by KNN or by cosine;
  * a legacy row is in the set, as it is for every other reader, and a lock-less brain reads its whole table;
  * when the reader refuses, the channel's vector arm is off and says so; a channel never breaks a recall.
"""
from __future__ import annotations

import unittest
from unittest import mock

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "organs"))

import recall_channels as rc  # noqa: E402

from voidloom.tests.test_doctor_brain_verdict import NOMIC, NOMIC_DIM, _Brain, el
from voidloom.tests.test_engine_reads_the_search_set import _unit

VEC = _unit(NOMIC_DIM, (0, 1.0))


class _Channels(_Brain):
    def setUp(self):
        super().setUp()
        self.good = self.vector(self.memory(whash=self.h, vector=False))
        self.legacy = self.vector(self.memory(whash="unresolved", vector=False))
        self.foreign = self.vector(self.memory(whash="f" * 64, vector=False))

    def vector(self, mid):
        self.conn.execute(f"INSERT INTO {NOMIC} (memory_id, embedding) VALUES (?, ?)", (mid, VEC))
        self.conn.commit()
        return mid

    def build(self):
        ids = (self.good, self.legacy, self.foreign)
        elig = {i: {"birth": 1.0, "created_at": None, "effective": 1.0, "pair": None, "evidence": None} for i in ids}
        reg = {"dreams": {**rc.CHANNELS["dreams"], "enabled": True, "recipe": ("vector_subset",)}}
        with mock.patch.object(rc, "_eligible_dreams", return_value=elig), \
                mock.patch.object(rc, "_row", side_effect=lambda c, n, s, mid, *a: {"id": mid}):
            return rc.build(self.conn, query="q", query_vec=VEC, results=[], top_k=10, explore=True, reg=reg)


class ALockedBrain(_Channels):
    def test_saboteur_another_models_row_gets_no_cosine(self):
        cos = rc._cosines(self.conn, NOMIC, VEC, [self.good, self.legacy, self.foreign])
        self.assertEqual(set(cos), {self.good, self.legacy})

    def test_saboteur_another_models_row_is_not_a_knn_candidate(self):
        out, diag = self.build()
        self.assertEqual(diag["dreams"]["vector_hits"], 2, diag)
        self.assertNotIn(self.foreign, [r["id"] for r in out["dreams"]])

    def test_saboteur_a_refusing_reader_turns_the_vector_arm_off_and_says_so(self):
        self.declare(NOMIC, NOMIC_DIM)              # metadata still names the table, so only the reader stands in the way
        self.conn.commit()
        (self.d / el.LOCK_NAME).unlink()
        self.assertEqual(rc._cosines(self.conn, NOMIC, VEC, [self.good]), {})
        out, diag = self.build()
        self.assertIn("LOCK_MISSING", diag["dreams"]["vector_error"])
        self.assertEqual(out["dreams"], [])


class ABrainWithNoLock(_Channels):
    LOCK = None

    def setUp(self):
        self.h = None
        _Brain.setUp(self)
        self.good = self.vector(self.memory(vector=False))
        self.legacy = self.vector(self.memory(whash="unresolved", vector=False))
        self.foreign = self.vector(self.memory(vector=False))

    def test_control_the_whole_table_is_read(self):
        self.assertEqual(len(rc._cosines(self.conn, NOMIC, VEC, [self.good, self.legacy, self.foreign])), 3)


if __name__ == "__main__":
    unittest.main()
