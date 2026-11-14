# -*- coding: utf-8 -*-
"""Community detection keeps a window of epochs, not every night forever, and what its readers see is unchanged.

Every nightly run wrote a new epoch of memory_communities and memory_community_members and nothing ever deleted an
older one; summaries of communities long gone stayed too. Measured on a live brain: 128 epochs, 2,972 current
communities of 126,347 rows, 62,569 of 65,541 summaries for communities not in the current epoch -- 81.6 MB of a
501 MB file. Every reader reads only the newest epoch (the carry-forward, the summaries), plus the highest community id
ever minted (so a retired id is never reused).

Now the stage prunes, inside its own transaction, every epoch older than the newest `communities_keep_epochs` (7),
and the summaries of communities no kept epoch holds; it reports epochs_pruned. What the readers return is the same
before and after, the id high-water mark survives the prune, and VACUUM stays the owner's call.

A packaged scratch brain with a planted history of epochs; no model.
"""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parents[1]
for _p in (str(_ROOT), str(_ROOT / "organs"), str(_HERE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from test_dreamfix_tranche_a import Brain  # noqa: E402

import communities as CO  # noqa: E402


class _History(unittest.TestCase):
    """Twelve planted epochs. Community 900 lived only in epoch 2 (the highest id ever minted); the newest epoch
    holds communities 1 and 2. Every community has a summary."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="vl-comm-", dir=os.environ.get("TMPDIR")))
        self.b = Brain(self.tmp)
        self.c = self.b.c
        self.assertTrue(CO.ensure_communities_migration(self.c))
        ids = [self.b.add("community member %d" % i, project="comm") for i in range(6)]
        for epoch in range(1, 13):
            parts = {1: ids[:3], 2: ids[3:]} if epoch != 2 else {1: ids[:2], 900: ids[2:]}
            for cid, members in parts.items():
                self.c.execute("INSERT INTO memory_communities (community_id, epoch, method, size, label, "
                               "member_edge_hash, seed) VALUES (?, ?, 'connected_components', ?, ?, 'h', 0)",
                               (cid, epoch, len(members), "label-%d" % cid))
                for m in members:
                    self.c.execute("INSERT INTO memory_community_members (community_id, epoch, memory_id) "
                                   "VALUES (?, ?, ?)", (cid, epoch, m))
        for cid in (1, 2, 900):
            self.c.execute("INSERT INTO community_summaries (community_id, summary, member_edge_hash, member_count) "
                           "VALUES (?, ?, 'h', 3)", (cid, "summary of %d" % cid))
        self.c.commit()

    def readers(self):
        latest = self.c.execute("SELECT MAX(epoch) FROM memory_communities").fetchone()[0]
        current = self.c.execute("SELECT community_id, size, label FROM memory_communities WHERE epoch = ? "
                                 "ORDER BY community_id", (latest,)).fetchall()
        summaries = self.c.execute("SELECT community_id, summary FROM community_summaries WHERE community_id IN "
                                   "(SELECT community_id FROM memory_communities WHERE epoch = ?) "
                                   "ORDER BY community_id", (latest,)).fetchall()
        return {"prior": CO._load_prior_partition(self.c), "current": current, "summaries": summaries,
                "max_id": CO._max_community_id(self.c)}

    def epochs(self):
        return self.c.execute("SELECT COUNT(DISTINCT epoch) FROM memory_communities").fetchone()[0]


class ThePrune(_History):

    def test_the_readers_see_the_same_before_and_after(self):
        before = self.readers()
        out = CO.prune_epochs(self.c, keep=7)
        self.c.commit()
        self.assertEqual(out["epochs_pruned"], 5, out)
        self.assertEqual(self.epochs(), 7)
        self.assertEqual(self.readers(), before)

    def test_the_highest_id_ever_minted_survives(self):
        CO.prune_epochs(self.c, keep=7)
        self.c.commit()
        self.assertEqual(CO._max_community_id(self.c), 900)

    def test_a_retired_communitys_summary_goes_with_it(self):
        CO.prune_epochs(self.c, keep=7)
        self.c.commit()
        ids = {r[0] for r in self.c.execute("SELECT community_id FROM community_summaries")}
        self.assertEqual(ids, {1, 2})

    def test_the_stage_prunes_and_says_so(self):
        res = CO.run_communities_stage(self.c, {"communities_keep_epochs": 7})
        self.assertIn(res["status"], ("ok", "partial"), res)
        self.assertGreaterEqual(res.get("epochs_pruned", 0), 5, res)
        self.assertLessEqual(self.epochs(), 7)


if __name__ == "__main__":
    unittest.main()
