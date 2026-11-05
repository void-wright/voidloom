"""One memory whose stored text is not valid UTF-8 must not take a verb down with it.

SQLite hands a row like this to Python as text, and the decode raises inside whatever query touches it, so one bad cell
turned off vector recall for a whole locked brain and crashed a hard delete of a different memory. The checksum works on
bytes now, and readers decode with replacement.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "organs"))

from voidloom.tests import test_tier_set as tt  # noqa: E402


@unittest.skipUnless(tt._vec_available(), "needs sqlite_vec")
class DamagedRow(unittest.TestCase):
    def setUp(self):
        import memory_write as mw
        import tier_set
        self.mw, self.ts = mw, tier_set
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        self.d = Path(self.td.name).resolve()
        self.db = tt.make_brain(self.d, "nomic", n=9, archived=())
        c = tt._connect(self.db)
        c.execute("UPDATE memories SET content = CAST(x'ff41ff42' AS TEXT) WHERE id = 5")
        c.commit(); c.close()
        for p in (mock.patch.object(tier_set, "_load", tt.fake_load), mock.patch.object(tier_set, "_encode", tt.fake_encode)):
            p.start()
            self.addCleanup(p.stop)

    def test_the_checksum_takes_bytes_and_agrees_with_text(self):
        self.assertEqual(self.mw.content_hash("plain"), self.mw.content_hash(b"plain"))
        self.assertTrue(self.mw.content_hash(b"\xff\x41"))

    def test_coverage_and_the_search_set_still_answer(self):
        c = tt._connect(self.db)
        env = self.mw.coverage_envelope(c)
        self.assertTrue(env["ok"], env)
        self.assertEqual(env["stale_content_hash"], 1)
        self.assertIsNotNone(self.mw.search_set(c))
        self.assertEqual(len(self.mw.knn(c, tt._vec(tt.TABLES["nomic"][2], "memory 7 about tides and lanterns", 768), 3)), 3)
        c.close()

    def test_hard_delete_of_another_memory_finishes_and_of_this_one_too(self):
        c = tt._connect(self.db)
        self.mw.purge_memory(c, 2)
        c.commit()
        self.mw.purge_memory(c, 5)
        c.commit()
        self.assertEqual(c.execute("SELECT COUNT(*) FROM memories WHERE id IN (2, 5)").fetchone()[0], 0)
        c.close()

    def test_tier_set_reaches_full_coverage_over_it(self):
        out = self.ts.set_tier(self.db, "qwen")
        self.assertEqual(out["status"], "ok")
        self.assertEqual(out["receipt"]["coverage"]["matched"], out["receipt"]["coverage"]["total"])

    def test_a_second_run_finds_no_work(self):
        self.ts.set_tier(self.db, "qwen")
        self.assertEqual(self.ts.set_tier(self.db, "qwen")["receipt"]["embedded"], 0)


if __name__ == "__main__":
    unittest.main()
