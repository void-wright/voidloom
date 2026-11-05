"""A seed memory the owner deleted stays deleted: the next load of the pack leaves it out, by whichever verb it went."""
from __future__ import annotations

import sqlite3
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "organs"))


def brain() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.executescript("""
        CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE memories (id INTEGER PRIMARY KEY, summary TEXT, content TEXT, is_archived INTEGER DEFAULT 0);
        CREATE TABLE memory_tags (memory_id INTEGER, tag TEXT);
    """)
    return c


ENTRY = {"content": "the tide table", "summary": "tides", "tags": "seed,anchor:seed_tides", "category": "insight"}


class SeedTombstone(unittest.TestCase):
    def setUp(self):
        import claude_memory_lite as lite
        import memory_write as mw
        self.lite, self.mw = lite, mw

    def plan(self, c):
        return self.lite.seed_plan(c, [ENTRY], "seed")["actions"]

    def test_control_an_entry_nobody_deleted_is_new(self):
        self.assertEqual(self.plan(brain())[0][0], "new")

    def test_a_deleted_entry_is_left_out_on_the_next_load(self):
        c = brain()
        c.execute("INSERT INTO memories (id, summary, content) VALUES (1, 'tides', 'the tide table')")
        c.executemany("INSERT INTO memory_tags VALUES (1, ?)", [("seed",), ("anchor:seed_tides",)])
        self.mw.tombstone_seed(c, 1)
        c.execute("DELETE FROM memories WHERE id = 1")
        c.execute("DELETE FROM memory_tags WHERE memory_id = 1")
        got = self.plan(c)[0]
        self.assertEqual((got[0], got[3]), ("left", None))

    def test_only_the_anchor_is_recorded_never_the_text(self):
        c = brain()
        c.execute("INSERT INTO memory_tags VALUES (1, 'anchor:seed_tides')")
        self.mw.tombstone_seed(c, 1)
        self.assertEqual(c.execute("SELECT key, value FROM metadata").fetchall(),
                         [("seed_tombstone:anchor:seed_tides", "anchor:seed_tides")])

    def test_a_memory_with_no_seed_anchor_leaves_no_record(self):
        c = brain()
        c.execute("INSERT INTO memory_tags VALUES (1, 'anchor:mine')")
        self.mw.tombstone_seed(c, 1)
        self.assertEqual(c.execute("SELECT COUNT(*) FROM metadata").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
