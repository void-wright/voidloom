"""The tier verbs on the paths the happy case never takes: an edit before a rollback, a run that died, a keyword install,
a real brain's long memories."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "organs"))

from voidloom.tests import test_tier_set as tt  # noqa: E402


@unittest.skipUnless(tt._vec_available(), "needs sqlite_vec")
class TierVerbs(unittest.TestCase):
    def setUp(self):
        import tier_set
        self.ts = tier_set
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        self.d = Path(self.td.name).resolve()
        self.db = tt.make_brain(self.d, "nomic", n=9, archived=())
        for p in (mock.patch.object(tier_set, "_load", tt.fake_load), mock.patch.object(tier_set, "_encode", tt.fake_encode)):
            p.start()
            self.addCleanup(p.stop)

    def _log_table(self):
        c = tt._connect(self.db)
        c.execute("""CREATE TABLE IF NOT EXISTS embedding_upgrade_log (id INTEGER PRIMARY KEY, started_at TEXT, completed_at TEXT,
                     model_name TEXT, embedding_dim INTEGER, memories_total INTEGER, memories_processed INTEGER, status TEXT,
                     error_message TEXT, weights_hash TEXT, notes TEXT)""")
        c.commit(); c.close()

    def test_rollback_counts_a_memory_edited_after_the_switch(self):
        self.ts.set_tier(self.db, "qwen")
        c = tt._connect(self.db)
        c.execute("UPDATE memories SET content = 'edited under qwen' WHERE id = 4")
        c.commit(); c.close()
        out = self.ts.rollback(self.db)
        self.assertEqual(out["receipt"]["memories_with_a_stale_restored_vector"], 1)
        self.assertIn("edited after the switch", out["note"])
        self.assertTrue(out["gap_fill"])

    def test_a_lock_lost_after_a_rollback_names_the_tier_it_rolled_back_to(self):
        import embedder_lock as el
        self.ts.set_tier(self.db, "qwen")
        self.ts.rollback(self.db)
        (self.d / "embedder.lock.json").unlink()
        self.assertEqual(el.lost_lock(self.d), {"tier": "cpu"})

    def test_control_a_clean_rollback_says_nothing(self):
        self.ts.set_tier(self.db, "qwen")
        out = self.ts.rollback(self.db)
        self.assertEqual(out["receipt"]["memories_with_a_stale_restored_vector"], 0)
        self.assertIsNone(out["note"])

    def test_a_killed_run_is_closed_by_the_next(self):
        self._log_table()
        c = tt._connect(self.db)
        c.execute("INSERT INTO embedding_upgrade_log (started_at, status, notes) VALUES ('t', 'in_progress', 'tier set')")
        c.commit(); c.close()
        self.ts.set_tier(self.db, "qwen")
        c = tt._connect(self.db)
        rows = c.execute("SELECT status, completed_at IS NOT NULL FROM embedding_upgrade_log ORDER BY id").fetchall()
        c.close()
        self.assertEqual(rows, [("aborted", 1), ("completed", 1)])

    def test_a_refused_flip_says_so_in_the_history(self):
        self._log_table()
        real = self.ts._coverage
        with mock.patch.object(self.ts, "_coverage", lambda c, l: {**real(c, l), "matched": 0}):
            with self.assertRaises(self.ts.TierRefused):
                self.ts.set_tier(self.db, "qwen")
        c = tt._connect(self.db)
        self.assertEqual(c.execute("SELECT status FROM embedding_upgrade_log").fetchall(), [("failed",)])
        c.close()

    def test_batches_hold_short_texts_together_and_a_long_one_alone(self):
        c = tt._connect(self.db)
        c.execute("UPDATE memories SET content = ? WHERE id = 3", ("long " * 3000,))
        c.commit()
        batches = self.ts._batches(c, list(range(1, 10)), 16)
        c.close()
        self.assertEqual(batches[-1], [3])
        self.assertEqual(sorted(i for b in batches for i in b), list(range(1, 10)))
        self.assertEqual(len(batches), 2)

    def test_no_batch_passes_the_budget_or_the_count(self):
        c = tt._connect(self.db)
        for i in range(1, 10):
            c.execute("UPDATE memories SET content = ? WHERE id = ?", ("x" * (i * 300), i))
        c.commit()
        with mock.patch.dict(self.ts._LIMITS, {"char_budget": 1500}):
            batches = self.ts._batches(c, list(range(1, 10)), 4)
        lengths = dict(c.execute("SELECT id, LENGTH(content) FROM memories").fetchall())
        c.close()
        for b in batches:
            self.assertLessEqual(len(b), 4)
            self.assertTrue(len(b) == 1 or max(lengths[i] for i in b) * len(b) <= 1500, b)

    def test_a_long_text_is_cut_up_front_on_an_accelerator_and_counted(self):
        out = self.ts._Outcome()
        seen = []
        with mock.patch.object(self.ts, "_on_accelerator", return_value=True), \
                mock.patch.object(self.ts, "_encode", lambda ts, lock: seen.extend(ts) or [b"v"] * len(ts)):
            done = self.ts._encode_rows([(7, "")], ["y" * 40000], {}, out)
        self.assertEqual(len(seen[0]), self.ts.SHORTEN_START_CHARS)
        self.assertEqual(out.shortened, {7: self.ts.SHORTEN_START_CHARS})
        self.assertEqual(done[0][1], "y" * 40000, "the stamp names the whole text")

    def test_control_on_a_cpu_nothing_is_cut(self):
        out = self.ts._Outcome()
        seen = []
        with mock.patch.object(self.ts, "_on_accelerator", return_value=False), \
                mock.patch.object(self.ts, "_encode", lambda ts, lock: seen.extend(ts) or [b"v"] * len(ts)):
            self.ts._encode_rows([(7, "")], ["y" * 40000], {}, out)
        self.assertEqual(len(seen[0]), 40000)
        self.assertEqual(out.shortened, {})

    def test_progress_is_printed_every_so_many_rows(self):
        pace = self.ts._Pace(1200)
        with mock.patch("sys.stderr") as err:
            pace.add(499)
            self.assertFalse(err.write.called)
            pace.add(2)
            self.assertIn("501 of 1200 embedded", "".join(c.args[0] for c in err.write.call_args_list))


class TheStallGuard(unittest.TestCase):
    def test_a_batch_past_the_bound_stops_typed_and_names_the_flag(self):
        code = ("import sys, time; sys.path.insert(0, sys.argv[1]); import tier_set\n"
                "with tier_set._StallGuard(0.2, 16, 9000):\n    time.sleep(5)\nprint('came back')\n")
        p = subprocess.run([sys.executable, "-c", code, str(ROOT / "organs")], capture_output=True, text=True, timeout=30)
        self.assertEqual(p.returncode, 1, p.stderr)
        env = json.loads(p.stdout)
        self.assertEqual(env["error"], "TIER_SET_STALLED")
        self.assertIn("--batch-size", env["message"])

    def test_control_a_batch_inside_the_bound_is_left_alone(self):
        sys.path.insert(0, str(ROOT / "organs"))
        import tier_set
        with tier_set._StallGuard(5, 1, 1) as g:
            pass
        self.assertTrue(g.timer.finished.is_set())


class TheKeywordInstall(unittest.TestCase):
    def test_tier_verbs_refuse_typed_without_the_stack(self):
        code = ("import sys, json; sys.modules['sqlite_vec'] = None; sys.path.insert(0, sys.argv[1]); import tier_set\n"
                "print(json.dumps(tier_set.run(['--db', sys.argv[2], 'status'])))\n")
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "claude_memory.db"
            db.write_bytes(b"")
            p = subprocess.run([sys.executable, "-c", code, str(ROOT / "organs"), str(db)], capture_output=True, text=True)
        env = json.loads(p.stdout)
        self.assertEqual(env["error"], "TIER1_REQUIRED", p.stderr)
        self.assertIn("bash install.sh", env["message"])


if __name__ == "__main__":
    unittest.main()
