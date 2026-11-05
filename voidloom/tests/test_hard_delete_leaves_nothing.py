"""A hard delete leaves nothing readable, on a brain with a history, not a fresh one.

The live row was never the hard part. An earlier UPDATE leaves the row's older copy in the page's free space, where no
delete of the live cell reaches; vec0's DELETE clears a validity bit and leaves the floats in the chunk; a connection
without vec0 cannot reach the vector at all and used to say done. Each case greps the whole file, its write-ahead log
included, for the bytes.
"""
from __future__ import annotations

import os
import sqlite3
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "organs"))

from voidloom.tests import test_tier_set as tt  # noqa: E402

MARK = "zanzibarquokka"
DIM = 768
PATTERN = struct.pack("4f", 1234.5678, -8765.4321, 1111.2222, -3333.4444)


def on_disk(db: Path) -> bytes:
    out = b""
    for suffix in ("", "-wal"):
        p = Path(str(db) + suffix)
        if p.exists():
            out += p.read_bytes()
    return out


@unittest.skipUnless(tt._vec_available(), "needs sqlite_vec")
class HardDelete(unittest.TestCase):
    def setUp(self):
        import embedder_lock as el
        import memory_write as mw
        self.mw, self.el = mw, el
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        self.d = Path(self.td.name).resolve()
        self.db = tt.make_brain(self.d, "nomic", n=40)
        c = tt._connect(self.db)
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA secure_delete=OFF")      # the default of a stock build, whatever this interpreter's was compiled to
        # the history: a row rewritten under an ordinary connection leaves its old cell behind, in the page
        for i in range(1, 6):
            c.execute("UPDATE memories SET content = ? WHERE id = 3", (f"{MARK} {i} " + "tide " * 40,))
            c.commit()
        c.execute("UPDATE memories SET content = 'plain now' WHERE id = 3")
        c.execute("UPDATE memory_embeddings SET embedding = ? WHERE memory_id = 3", (PATTERN + bytes(4 * DIM - 16),))
        c.commit()
        c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        c.close()
        self.assertTrue(MARK.encode() in on_disk(self.db), "the plant: the old text is in the file before the delete")
        self.assertTrue(PATTERN in on_disk(self.db), "the plant: the vector is in the file before the delete")

    def _purge(self, mid=3):
        c = tt._connect(self.db)
        receipt = self.mw.purge_memory(c, mid)
        c.commit()
        receipt.update(self.mw.finish_purge(c))
        c.close()
        return receipt

    def test_the_old_text_in_free_space_goes(self):
        out = self._purge()
        self.assertTrue(out["compacted"], out)
        self.assertTrue(out["wal_emptied"], out)
        self.assertFalse(MARK.encode() in on_disk(self.db))

    def test_the_vector_bytes_go_and_search_stays_intact(self):
        self._purge()
        self.assertFalse(PATTERN in on_disk(self.db))
        c = tt._connect(self.db)
        hits = self.mw.knn(c, tt._vec(tt.TABLES["nomic"][2], "memory 7 about tides and lanterns", DIM), 3)
        self.assertEqual(hits[0][0], 7)
        self.assertEqual(c.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        c.close()

    def test_a_process_that_blocks_the_rebuild_is_named_in_the_receipt(self):
        c = tt._connect(self.db)
        c.execute("PRAGMA busy_timeout=50")
        receipt = self.mw.purge_memory(c, 3)
        c.commit()
        other = sqlite3.connect(str(self.db), isolation_level=None)
        other.execute("BEGIN IMMEDIATE")
        receipt.update(self.mw.finish_purge(c))
        other.execute("ROLLBACK")
        other.close()
        c.close()
        self.assertFalse(receipt["compacted"])
        self.assertIn("locked", receipt["not_compacted_because"])

    def test_a_reader_holding_the_log_open_reads_as_not_emptied(self):
        c = tt._connect(self.db)
        c.execute("PRAGMA busy_timeout=50")
        receipt = self.mw.purge_memory(c, 3)
        c.commit()
        reader = sqlite3.connect(str(self.db), isolation_level=None)
        reader.execute("BEGIN")
        reader.execute("SELECT 1 FROM memories LIMIT 1").fetchone()
        receipt.update(self.mw.finish_purge(c))
        reader.execute("ROLLBACK")
        reader.close()
        c.close()
        self.assertFalse(receipt["wal_emptied"])

    def test_a_copy_beside_the_brain_is_named_not_silently_left(self):
        bak = Path(str(self.db) + ".pre-hybrid-retrieval.bak")
        bak.write_bytes(b"x")
        self.assertEqual(self._purge()["other_copies_not_reached"], [str(bak)])

    def test_without_vec0_it_refuses_before_any_row_goes(self):
        c = sqlite3.connect(str(self.db))
        with self.assertRaises(self.mw.HardDeleteRefused) as cm:
            self.mw.purge_memory(c, 3)
        c.rollback()
        self.assertEqual(cm.exception.code, "HARD_DELETE_VECTOR_UNREACHABLE")
        self.assertIn("venv/bin/python3", cm.exception.message)
        self.assertEqual(c.execute("SELECT COUNT(*) FROM memories WHERE id = 3").fetchone()[0], 1)
        c.close()

    def test_control_a_keyword_memory_with_no_vector_still_deletes_without_vec0(self):
        c = tt._connect(self.db)
        c.execute("INSERT INTO memories (id, content) VALUES (99, 'no vector here')")
        c.commit(); c.close()
        c = sqlite3.connect(str(self.db))
        self.mw.purge_memory(c, 99)
        c.commit()
        self.assertEqual(c.execute("SELECT COUNT(*) FROM memories WHERE id = 99").fetchone()[0], 0)
        c.close()

    def test_replacing_a_vector_clears_the_old_bytes_too(self):
        c = tt._connect(self.db)
        self.mw.clear_vector(c, "memory_embeddings", 3)
        c.execute("INSERT INTO memory_embeddings (memory_id, embedding) VALUES (3, ?)", (bytes(4 * DIM),))
        c.commit()
        c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        c.close()
        self.assertFalse(PATTERN in on_disk(self.db))

    def test_the_seed_loader_refuses_the_same_way(self):
        import seed_loader
        c = tt._connect(self.db)
        c.execute("ALTER TABLE memories ADD COLUMN seed INTEGER DEFAULT 0")
        c.execute("ALTER TABLE memories ADD COLUMN origin TEXT")
        c.execute("UPDATE memories SET seed = 1, origin = 'seed_pack:1#x' WHERE id = 3")
        c.commit(); c.close()
        with mock.patch.object(seed_loader, "enable_vec", side_effect=seed_loader.SeedLoadError("X", "no vec0")), \
                mock.patch.object(seed_loader, "_connect", lambda db: sqlite3.connect(db)):
            with self.assertRaises(seed_loader.SeedLoadError) as cm:
                seed_loader.delete_one(str(self.db), "seed_pack:1#x")
        self.assertEqual(cm.exception.code, "HARD_DELETE_VECTOR_UNREACHABLE")
        c = tt._connect(self.db)
        self.assertEqual(c.execute("SELECT COUNT(*) FROM memories WHERE id = 3").fetchone()[0], 1)
        c.close()


@unittest.skipUnless(tt._vec_available(), "needs sqlite_vec")
class TierCleanupClearsItsBytes(unittest.TestCase):
    def test_the_old_vectors_leave_the_file(self):
        import tier_set
        with tempfile.TemporaryDirectory() as td, \
                mock.patch.object(tier_set, "_load", tt.fake_load), mock.patch.object(tier_set, "_encode", tt.fake_encode):
            d = Path(td).resolve()
            db = tt.make_brain(d, "nomic", n=30)
            c = tt._connect(db)
            c.execute("UPDATE memory_embeddings SET embedding = ? WHERE memory_id = 3", (PATTERN + bytes(4 * DIM - 16),))
            c.commit(); c.close()
            tier_set.set_tier(db, "qwen")
            self.assertTrue(PATTERN in on_disk(db))
            out = tier_set.cleanup(db, "memory_embeddings")
            self.assertTrue(out["receipt"]["compacted"], out)
            self.assertFalse(PATTERN in on_disk(db))
            c = tt._connect(db)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM memory_embeddings").fetchone()[0], 0)
            c.execute("INSERT INTO memory_embeddings (memory_id, embedding) VALUES (1, ?)", (bytes(4 * DIM),))
            c.close()


if __name__ == "__main__":
    unittest.main()
