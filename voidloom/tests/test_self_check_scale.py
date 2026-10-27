# -*- coding: utf-8 -*-
"""The installer's recall checks hold as the brain grows.

THE CLAIM UNDER TEST. `install/self_check.py` proves keyword recall works by asking for
seed memories by their own words, and for the brain's most important memory by its own
longest word. On a fresh install those rows are most of the brain. On an aged brain the
owner's own notes use the same words, and a check that demands a top-N rank over the
WHOLE brain fails a healthy brain as it grows (measured 2026-10-27: a 31k-memory brain
failed an upgrade this way). So:
    * seed probes rank only among the seed pack's rows, and must land in the top
      SEED_PROBE_WINDOW (3) there;
    * the own-content check asks whether recall RETURNS the row, not where.

Built here as a synthetic aged brain: the seed rows, plus 300 owner rows that repeat
every probe's words more often than the seeds do.

Shipped as control pairs:
    *_control_*   the shipped check on the aged brain, and it must read GREEN;
    *_saboteur_*  the whole-brain rank check put back, and it must read RED.
"""
import sqlite3
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "install"))
sys.path.insert(0, str(ROOT / "organs"))

import self_check  # noqa: E402

TAG = "seed-pack-agnostic"
SEEDS = [  # (summary that carries the probe's must-contain phrase, content)
    ("BATCH PARALLEL RECALL: run many recall queries in one call", "batch parallel recall queries"),
    ("WHISPERS: short observations captured in flight", "whisper observations in flight"),
    ("VALID CATEGORIES: the fixed enum of twelve", "valid categories fixed enum"),
    ("TWO-TIER EMBEDDING: nomic on CPU, qwen on GPU", "two tier embedding nomic"),
    ("SESSION-START PROTOCOL: prime context first", "session start protocol prime context"),
    ("STORE DISCIPLINE: store liberally, during work", "store discipline liberally"),
    ("FRESH-CLAUDE BOOTSTRAP: an inherited brain", "fresh claude bootstrap inherited brain"),
]


def aged_brain(n_owner=300):
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE memories (id INTEGER PRIMARY KEY, summary TEXT, content TEXT, importance REAL, "
              "created_at TEXT, is_archived INTEGER DEFAULT 0)")
    c.execute("CREATE TABLE memory_tags (memory_id INTEGER, tag TEXT)")
    c.execute("CREATE VIRTUAL TABLE memories_fts USING fts5(content, summary)")
    mid = 0
    for summ, content in SEEDS:
        mid += 1
        c.execute("INSERT INTO memories VALUES (?,?,?,?,?,0)", (mid, summ, content, 0.5, "2026-01-01"))
        c.execute("INSERT INTO memory_tags VALUES (?,?)", (mid, TAG))
        c.execute("INSERT INTO memories_fts (rowid, content, summary) VALUES (?,?,?)", (mid, content, summ))
    # The owner's own notes: short, dense in every probe's words, more important than any seed.
    for i in range(n_owner):
        mid += 1
        words = SEEDS[i % len(SEEDS)][1]
        content = f"{words} {words} note {i}"
        c.execute("INSERT INTO memories VALUES (?,?,?,?,?,0)", (mid, f"owner note {i}", content, 0.9, "2026-10-01"))
        c.execute("INSERT INTO memories_fts (rowid, content, summary) VALUES (?,?,?)", (mid, content, f"owner note {i}"))
    return c


def recall_results(checks):
    return [r for r in checks.results if r["name"].startswith("recall '")]


class SelfCheckScale(unittest.TestCase):

    def test_control_seed_probes_pass_on_an_aged_brain(self):
        checks = self_check.Checks()
        self_check.check_recall_path(checks, aged_brain(), [{"summary": s} for s, _ in SEEDS], common_tag=TAG)
        res = recall_results(checks)
        self.assertEqual(len(res), len(SEEDS))
        self.assertTrue(all(r["ok"] for r in res), [r["name"] for r in res if not r["ok"]])

    def test_saboteur_a_whole_brain_rank_window_fails_the_aged_brain(self):
        real = self_check._recall
        self_check._recall = lambda conn, q, limit=5, seed_tag=None: real(conn, q, limit=limit)  # scope dropped
        try:
            checks = self_check.Checks()
            self_check.check_recall_path(checks, aged_brain(), [{"summary": s} for s, _ in SEEDS], common_tag=TAG)
        finally:
            self_check._recall = real
        self.assertFalse(all(r["ok"] for r in recall_results(checks)))

    def test_control_seed_probes_pass_on_a_fresh_brain(self):
        checks = self_check.Checks()
        self_check.check_recall_path(checks, aged_brain(0), [{"summary": s} for s, _ in SEEDS], common_tag=TAG)
        self.assertTrue(all(r["ok"] for r in recall_results(checks)))

    def test_control_own_content_is_membership_and_holds_at_scale(self):
        c = aged_brain()
        # Make one row the most important, sharing its longest word with 300 others.
        c.execute("UPDATE memories SET importance = 1.0, summary = 'Lighthouse maintenance schedule' WHERE id = 1")
        c.execute("UPDATE memories_fts SET summary = 'Lighthouse maintenance schedule' WHERE rowid = 1")
        for i in range(300):
            c.execute("INSERT INTO memories_fts (rowid, content, summary) VALUES (?,?,?)",
                      (10_000 + i, "maintenance maintenance maintenance", "x"))
            c.execute("INSERT INTO memories VALUES (?,?,?,?,?,0)",
                      (10_000 + i, "x", "maintenance maintenance maintenance", 0.1, "2026-10-02"))
        checks = self_check.Checks()
        self_check.check_own_content_recall(checks, c)
        own = [r for r in checks.results if r["name"].startswith("recall over this brain's own content")]
        self.assertEqual(len(own), 1)
        self.assertTrue(own[0]["ok"], own[0])
        # Saboteur on the same brain: the old top-10 rank window would not find it.
        top10 = [i for i, _ in self_check._recall(c, "maintenance", limit=10)]
        self.assertNotIn(1, top10, "the planted brain no longer discriminates a rank window from membership")

    def test_control_an_impostor_note_is_not_the_seed(self):
        """An owner note that carries the seed's phrase and outranks it is not the seed. With the seed
        row taken out of the keyword index, the probe must FAIL rather than pass on the impostor."""
        c = aged_brain()
        c.execute("INSERT INTO memories VALUES (900, 'TWO-TIER EMBEDDING copied into my notes', "
                  "'two tier embedding nomic two tier embedding nomic', 1.0, '2026-10-03', 0)")
        c.execute("INSERT INTO memory_tags VALUES (900, 'anchor:seed_owner_copy')")
        c.execute("INSERT INTO memories_fts (rowid, content, summary) VALUES (900, "
                  "'two tier embedding nomic two tier embedding nomic', 'TWO-TIER EMBEDDING copied into my notes')")
        c.execute("DELETE FROM memories_fts WHERE rowid = 4")  # the real seed can no longer be recalled
        checks = self_check.Checks()
        self_check.check_recall_path(checks, c, [{"summary": s} for s, _ in SEEDS], common_tag=TAG)
        probe = [r for r in recall_results(checks) if "two tier embedding" in r["name"]]
        self.assertEqual(len(probe), 1)
        self.assertFalse(probe[0]["ok"], "the probe passed on an owner note instead of the seed")

    def test_saboteur_own_content_fails_when_recall_cannot_return_the_row(self):
        c = aged_brain()
        c.execute("UPDATE memories SET importance = 1.0, summary = 'Lighthouse maintenance schedule' WHERE id = 1")
        c.execute("DELETE FROM memories_fts WHERE rowid = 1")  # out of the keyword index
        checks = self_check.Checks()
        self_check.check_own_content_recall(checks, c)
        own = [r for r in checks.results if r["name"].startswith("recall over this brain's own content")]
        self.assertEqual(len(own), 1)
        self.assertFalse(own[0]["ok"], "the own-content check passed for a row keyword recall cannot return")

    def test_control_the_window_is_tight(self):
        self.assertLessEqual(self_check.SEED_PROBE_WINDOW, 3)


if __name__ == "__main__":
    unittest.main()
