"""On a brain with no lock, `matched` is what a search can reach.

The lockless search rule is the whole table, so the answer's coverage must count active
memories with a vector there, not stamped rows. Measured on a migrated copy of a jina
brain: 11,048 stamps against 25,009 reachable active memories, so every answer on that
brain understated its reach by more than half, and `stats` disagreed with it. `covered`
keeps the stamp count: it is the figure a re-embed moves.
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

TABLE, DIM = "memory_embeddings_v2", 1024


def _vec():
    return struct.pack(f"{DIM}f", *([1.0] + [0.0] * (DIM - 1)))


class Lockless(unittest.TestCase):
    def setUp(self):
        import sqlite_vec
        el.clear_loaded()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        c = sqlite3.connect(str(Path(self.tmp.name) / "claude_memory.db"))
        self.addCleanup(c.close)
        c.enable_load_extension(True)
        sqlite_vec.load(c)
        c.executescript(f"""
            CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT);
            INSERT INTO metadata VALUES ('active_embedding_table', '{TABLE}');
            CREATE TABLE memories (id INTEGER PRIMARY KEY AUTOINCREMENT, content TEXT NOT NULL,
                is_archived INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE memory_tags (memory_id INTEGER, tag TEXT);
            CREATE VIRTUAL TABLE {TABLE} USING vec0(memory_id INTEGER PRIMARY KEY, embedding float[{DIM}]);
        """)
        mw.ensure_provenance_table(c)
        self.conn = c

    def add(self, *, stamped=False, vector=True, archived=0):
        mid = self.conn.execute("INSERT INTO memories (content, is_archived) VALUES ('m', ?)", (archived,)).lastrowid
        if stamped:
            mw.attach_embedding(self.conn, mid, "m", embedding=_vec(), policy="require", organ="t")
        elif vector:
            self.conn.execute(f"INSERT INTO {TABLE} (memory_id, embedding) VALUES (?, ?)", (mid, _vec()))
        self.conn.commit()
        return mid

    def test_control_an_unstamped_vector_is_reachable_and_counted(self):
        self.add(stamped=True)
        self.add()
        self.add()
        cov = mw.provenance_coverage(self.conn)
        self.assertEqual((cov.matched, cov.total, cov.covered), (3, 3, 1))
        self.assertEqual(cov.coverage["matched"], 3)
        self.assertEqual(cov.as_dict()["matched"], 3)

    def test_saboteur_an_archived_memory_is_outside_what_a_search_reaches(self):
        self.add(stamped=True)
        self.add(stamped=True, archived=1)
        self.add(archived=1)
        cov = mw.provenance_coverage(self.conn)
        self.assertEqual((cov.matched, cov.total), (1, 1))

    def test_saboteur_a_memory_with_no_vector_is_uncovered(self):
        self.add(stamped=True)
        self.add(vector=False)
        cov = mw.provenance_coverage(self.conn)
        self.assertEqual((cov.matched, cov.total), (1, 2))

    def test_control_the_defects_still_show_what_is_unstamped(self):
        self.add(stamped=True)
        self.add()
        cov = mw.provenance_coverage(self.conn)
        self.assertEqual((cov.vectors_without_stamp, cov.clean), (1, False))


if __name__ == "__main__":
    unittest.main()
