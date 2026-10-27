# -*- coding: utf-8 -*-
"""`recall --top-k K` returns the K MOST RELEVANT memories, whatever order it prints them in.

Found by the public-release clean room (2026-10-27): on a fresh brain, a memory stored a moment earlier ranked 4th
by vector distance for a paraphrased question and was ABSENT from `--top-k 3/5/10/20`. The diversity organ folds its
pool into Lost-in-the-Middle order (ranks 1,3,5,... first, ...,6,4,2 last) and cmd_recall then cut that FOLDED list
from the front -- so every `--top-k` answer held the odd ranks and dropped the even ones, the second-best result
included. The same class was fixed once before in the dreams channel (test_dreamfix_r2w2a,
TestTheKnobReadsTheTopFiveByRank: "took positions [:5] of the Lost-in-the-Middle-placed list"); this is the main
recall lane.

The contract now: cut by relevance FIRST, then place. Each case drives the real cmd_recall over a planted brain with
a deterministic bag-of-words encoder, so the relevance order is known in advance.
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

os.environ["CUDA_VISIBLE_DEVICES"] = ""          # forced, before anything can import torch

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

try:
    import sqlite_vec  # noqa: F401
    from test_dreamfix_tranche_a import Brain, _TmpCase, run_recall
    _SKIP = None
except Exception as e:  # noqa: BLE001
    _SKIP = f"needs sqlite_vec and the tranche-A brain helpers: {e}"
    _TmpCase = unittest.TestCase                  # type: ignore

QUERY = "alpha bravo charlie delta echo foxtrot golf hotel india juliet"
WORDS = QUERY.split()


def _plant(b):
    """Memory i shares (10 - i) query words: a strictly falling relevance, rank 0 strongest. Each row also carries
    its own distinct filler, so no two rows are near-duplicates the de-dup step could merge."""
    ids = []
    for i in range(10):
        filler = " ".join(f"filler{i}x{j}" for j in range(4))
        ids.append(b.add(" ".join(WORDS[: 10 - i]) + " " + filler, project="p", category="insight"))
    return ids


@unittest.skipIf(_SKIP, _SKIP or "")
class TestTopKIsTheMostRelevant(_TmpCase):
    def _ids(self, env):
        data = env.get("data", env)
        return [r["id"] for r in data["results"]]

    def test_every_top_k_holds_the_strongest(self):
        """The two strongest always survive the cut, and at most one slot goes to a weaker row (the de-dup step
        may trade a near-duplicate for a more distinct memory -- the planted rows share most of their words). The
        folded-then-cut defect returned ranks 0,2,4,6,8 for --top-k 5: rank 1 absent, three of the top six."""
        b = Brain(self.tmp)
        ids = _plant(b)
        b.c.close()
        for k in (2, 3, 5, 7):
            env, _spy = run_recall(b.path, QUERY, top_k=k)
            got = self._ids(env)
            ranks = sorted(ids.index(g) for g in got if g in ids)
            self.assertIn(0, ranks, f"--top-k {k}: the strongest memory is missing ({ranks})")
            self.assertIn(1, ranks, f"--top-k {k}: the second strongest is missing ({ranks})")
            self.assertGreaterEqual(sum(1 for r in ranks if r < k), k - 1,
                                    f"--top-k {k} returned ranks {ranks}: more than one slot below the top {k}")

    def test_placement_still_puts_the_two_strongest_at_the_ends(self):
        b = Brain(self.tmp)
        ids = _plant(b)
        b.c.close()
        env, _spy = run_recall(b.path, QUERY, top_k=5)
        got = self._ids(env)
        self.assertEqual(got[0], ids[0], "the strongest memory opens the list")
        self.assertEqual(got[-1], ids[1], "the second strongest closes it (Lost-in-the-Middle placement)")


if __name__ == "__main__":
    unittest.main()
