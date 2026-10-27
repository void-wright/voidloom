"""The two dream units share one lock; the nightly waits, and a lock-skip is its own verdict.

Measured 2026-09-18 22:28:36 (memory #20269): the machine slept through 14:00 and 22:00,
Persistent=true fired the daytime and nightly dream units in the same second at the wake,
the daytime won `dream_pipeline.py`'s lock, the nightly returned {"status": "skipped"} with
exit 1, systemd recorded Result=exit-code, and the ledger filed the lost night as a failure.

Three properties, each a minimal pair against the previous tree:
  1. the packaged nightly unit's After= names the daytime service (so a same-second start
     job waits for the oneshot to finish instead of racing it), and the roster renders the
     same line (the body-binding test holds the two equal; this names the intent);
  2. dream_pipeline.main() returns EX_TEMPFAIL (75), not 1, when the lock is held --
     without touching the database;
  3. the ledger classifies Result=exit-code + ExecMainStatus=75 as "skipped-lock" and
     leaves every other pair alone.
"""
import json
import os
import re
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(os.environ.get("VOIDLOOM_ROOT") or Path(__file__).resolve().parents[2]).resolve()
sys.path.insert(0, str(ROOT / "organs"))

import dream_pipeline  # noqa: E402
import organ_run_log  # noqa: E402


class TestNightlyWaitsForDaytime(unittest.TestCase):
    def test_packaged_unit_after_names_the_daytime_service(self):
        text = (ROOT / "cron" / "wizard-library-dreaming.service").read_text(encoding="utf-8")
        after = [l for l in text.splitlines() if l.startswith("After=")]
        self.assertEqual(len(after), 1, after)
        self.assertIn("wizard-library-dream-daytime.service", after[0].split("=", 1)[1].split(), after)
        # one direction only: the daytime unit must NOT name the nightly (a cycle systemd breaks silently)
        day = (ROOT / "cron" / "wizard-library-dream-daytime.service").read_text(encoding="utf-8")
        self.assertNotRegex(day, r"^After=.*wizard-library-dreaming\.service", "ordering must be one-directional")

    def test_roster_renders_the_same_after_line(self):
        roster = json.loads((ROOT / "install" / "cron_roster.json").read_text(encoding="utf-8"))["units"]
        extra = roster["dreaming"].get("service_extra", [])
        afters = [l for l in extra if l.startswith("After=")]
        self.assertEqual(afters, ["After=network.target wizard-library-dream-daytime.service"], extra)
        day_extra = roster["dream-daytime"].get("service_extra", [])
        self.assertEqual([l for l in day_extra if l.startswith(("After=", "Before=", "Requires=", "Wants="))], [],
                         "the daytime unit carries no ordering back")   # SuccessExitStatus=79 (starved) is not ordering


class TestLockSkipIsItsOwnExit(unittest.TestCase):
    def test_main_returns_ex_tempfail_when_the_lock_is_held_and_never_opens_the_db(self):
        self.assertEqual(dream_pipeline.EXIT_SKIPPED_LOCK, 75)
        opened = []
        with mock.patch.object(dream_pipeline.memory_dreaming, "_acquire_lock", return_value=False), \
             mock.patch.object(dream_pipeline, "_get_conn", side_effect=lambda p: opened.append(p) or (_ for _ in ()).throw(AssertionError("db opened"))), \
             mock.patch("builtins.print"):
            rc = dream_pipeline.main(["run", "/nonexistent/claude_memory.db"])
        self.assertEqual(rc, 75, "a held lock is EX_TEMPFAIL, not a failure")
        self.assertEqual(opened, [], "the skip must happen before the database is touched")

    def test_a_real_error_is_still_exit_1(self):
        with mock.patch.object(dream_pipeline, "run_dream_cycle", return_value={"status": "error", "message": "x"}), \
             mock.patch("builtins.print"):
            self.assertEqual(dream_pipeline.main(["run", "/nonexistent/claude_memory.db"]), 1)


class TestLedgerClassifiesTheSkip(unittest.TestCase):
    def test_exit_code_75_is_skipped_lock(self):
        self.assertEqual(organ_run_log.classify_result("exit-code", "75"), "skipped-lock")

    def test_every_other_pair_is_untouched(self):
        for result, status in (("exit-code", "1"), ("success", "0"), ("timeout", None), ("exit-code", None), (None, "75"), ("success", "75")):
            self.assertEqual(organ_run_log.classify_result(result, status), result, (result, status))


if __name__ == "__main__":
    unittest.main()
