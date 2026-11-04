"""A stamped centroid is finished work; replacing it with a text vector buries the memory.

A consolidated memory's vector is the average of its members, which is why a query about
those members finds it. `count_pending_embeddings` counted it as work, because its stamp
is not a text stamp, so any caller that drains the queue would swap it for a vector of the
distilled sentence. One under another checksum is still work: it is another model's space.
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


def _v(x):
    return struct.pack(f"{DIM}f", *([float(x)] + [0.0] * (DIM - 1)))


class Pending(unittest.TestCase):
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
        self.lock = el.write_lock(d, "cpu")
        el.register_loaded(model_id=self.lock["model_id"], revision=self.lock["revision"],
                           weights_hash=self.lock["weights_hash"], dim=DIM, vec_table=TABLE, tier="cpu")
        self.conn = c
        self.members = [self.write(f"member {i}") for i in (1, 2, 3)]
        self.centroid = c.execute("INSERT INTO memories (content) VALUES ('distilled')").lastrowid
        c.execute(f"INSERT INTO {TABLE} (memory_id, embedding) VALUES (?, ?)", (self.centroid, _v(1)))
        mw.stamp_centroid(c, self.centroid, TABLE, DIM, self.members, [_v(1)] * 3, "t")
        c.commit()

    def write(self, text):
        mid = self.conn.execute("INSERT INTO memories (content) VALUES (?)", (text,)).lastrowid
        mw.attach_embedding(self.conn, mid, text, embedding=_v(1), policy="require", organ="t")
        self.conn.commit()
        return mid

    def test_control_a_centroid_under_the_lock_is_not_work(self):
        self.assertEqual(mw.count_pending_embeddings(self.conn), 0)
        self.assertEqual(mw.pending_embeddings(self.conn, limit=10), [])

    def test_saboteur_a_centroid_under_another_checksum_is_work(self):
        self.conn.execute("UPDATE embedding_provenance SET weights_hash = ? WHERE memory_id = ?",
                          ("a" * 64, self.centroid))
        self.conn.commit()
        self.assertEqual(mw.count_pending_embeddings(self.conn), 1)

    def test_saboteur_an_edited_text_memory_is_still_work(self):
        self.conn.execute("UPDATE memories SET content = 'edited' WHERE id = ?", (self.members[0],))
        self.conn.commit()
        self.assertEqual(mw.count_pending_embeddings(self.conn), 1)


if __name__ == "__main__":
    unittest.main()
