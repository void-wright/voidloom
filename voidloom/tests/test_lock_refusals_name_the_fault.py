"""On a locked brain the refusal says what is actually wrong.

Two cases read as something else: a stamp for a table the lock does not name refused as
"no tier maps to this table", and a brain whose every stamp carries the lock's checksum
at the wrong width refused as "no rows", which points the user at a re-embed when the
stamps themselves cannot be trusted.
"""
from __future__ import annotations

import sqlite3
import struct
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "organs"))

import embedder_lock as el  # noqa: E402
import memory_write as mw  # noqa: E402

TABLE, DIM = "memory_embeddings", 768


class Locked(unittest.TestCase):
    def setUp(self):
        import sqlite_vec
        el.clear_loaded()
        self.addCleanup(el.clear_loaded)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        d = Path(self.tmp.name)
        c = sqlite3.connect(str(d / "claude_memory.db"))
        self.addCleanup(c.close)
        c.enable_load_extension(True)
        sqlite_vec.load(c)
        c.executescript(f"""
            CREATE TABLE memories (id INTEGER PRIMARY KEY AUTOINCREMENT, content TEXT NOT NULL,
                is_archived INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE memory_tags (memory_id INTEGER, tag TEXT);
            CREATE VIRTUAL TABLE {TABLE} USING vec0(memory_id INTEGER PRIMARY KEY, embedding float[{DIM}]);
        """)
        mw.ensure_provenance_table(c)
        lock = el.write_lock(d, "cpu")
        el.register_loaded(model_id=lock["model_id"], revision=lock["revision"], weights_hash=lock["weights_hash"],
                           dim=DIM, vec_table=TABLE, tier="cpu")
        mid = c.execute("INSERT INTO memories (content) VALUES ('m')").lastrowid
        mw.attach_embedding(c, mid, "m", embedding=struct.pack(f"{DIM}f", *([1.0] + [0.0] * (DIM - 1))),
                            policy="require", organ="t")
        c.commit()
        self.conn, self.mid = c, mid

    def test_saboteur_a_table_the_lock_does_not_name_is_stamp_inconsistent(self):
        with self.assertRaises(el.EmbedderLockError) as cm:
            mw._stamp_provenance(self.conn, self.mid, "memory_embeddings_v9", DIM, "x", "t")
        self.assertEqual(cm.exception.code, "STAMP_INCONSISTENT")

    def test_saboteur_every_stamp_at_the_wrong_width_is_stamp_inconsistent_not_no_rows(self):
        self.conn.execute("UPDATE embedding_provenance SET dim = 7")
        self.conn.commit()
        with self.assertRaises(mw.ProvenanceInconsistent) as cm:
            mw.provenance_coverage(self.conn)
        self.assertEqual(cm.exception.code, "STAMP_INCONSISTENT")
        self.assertEqual(cm.exception.coverage.inconsistent, [{"vec_table": TABLE, "dim": 7, "rows": 1}])

    def test_control_a_stamped_brain_still_reads(self):
        self.assertEqual(mw.provenance_coverage(self.conn).matched, 1)


if __name__ == "__main__":
    unittest.main()
