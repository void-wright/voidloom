# -*- coding: utf-8 -*-
"""Every writer that still stamped LOCAL time into a UTC table now stamps the brain's own clock (brain_clock).

The tables beside these columns hold datetime('now') stamps -- UTC, 'YYYY-MM-DD HH:MM:SS'. These writers used
datetime.now().isoformat(): local time with a 'T'. Measured on a live brain: decision_events.decided_at carried a
local 'T' row written by an auto decision beside seventeen UTC rows; calibration_log was local throughout;
approval_ping_state.snooze_until and belief_engine's last_belief_scan_at were local. Each now writes
brain_clock.utc_stamp(), and the comparisons that read those same columns back read them on the same clock -- so a
snooze set for one hour lasts one hour, and a calibration window holds the rows it should.

The many other readers that age UTC rows on a local clock are not changed here (declared).

A packaged scratch brain and a fixed clock; nothing touches a live brain.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parents[1]
for _p in (str(_ROOT), str(_ROOT / "organs"), str(_HERE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from test_dreamfix_tranche_a import Brain  # noqa: E402
from test_approvals_policy_d26 import _report  # noqa: E402

import approvals_policy as AP  # noqa: E402
import belief_engine as BE  # noqa: E402
import brain_clock  # noqa: E402
import calibration_memory as CM  # noqa: E402
import voidloom_approvals as VA  # noqa: E402

NOW = datetime(2026, 6, 3, 9, 0, 0)
STAMP = "2026-06-03 09:00:00"


class _Fixed(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="vl-utcw-", dir=os.environ.get("TMPDIR")))
        self.cfg = str(self.tmp / "wizard-config.json")
        Path(self.cfg).write_text(json.dumps({}) + "\n")
        env = mock.patch.dict(os.environ, {"WIZARD_CONFIG_PATH": self.cfg, "VOIDLOOM_ACTOR": "the-owner"})
        env.start()
        self.addCleanup(env.stop)
        self.brain = Brain(self.tmp)
        self.brain.c.close()
        clock = mock.patch.object(brain_clock, "utc_now", return_value=NOW)
        clock.start()
        self.addCleanup(clock.stop)

    def q(self, sql, args=()):
        c = sqlite3.connect(self.brain.path)
        try:
            return c.execute(sql, args).fetchall()
        finally:
            c.close()


class DecisionEventsAreUtc(_Fixed):
    def test_every_decided_at_an_auto_decision_writes(self):
        AP.set_mode("auto", set_by="owner")
        rid = _report(self.brain.path, "2026-09")
        self.assertEqual(AP.decide_new_self_eval_report(self.brain.path, rid)["status"], "ok")
        stamps = {r[0] for r in self.q("SELECT decided_at FROM decision_events e JOIN decision_units u "
                                      "ON u.id = e.unit_id WHERE u.report_id = ?", (rid,))}
        self.assertEqual(stamps, {STAMP})


class CalibrationIsUtc(_Fixed):
    def test_prediction_and_outcome_and_the_window_that_reads_them(self):
        c = sqlite3.connect(self.brain.path)
        try:
            if not CM.is_calibration_migration_applied(c):
                CM.apply_calibration_migration(c, str(_ROOT / "migrations" / "migration_014_calibration_memory.sql"))
            eid = CM.log_prediction(c, 10.0, "minutes", "time", "build")
            CM.log_outcome(c, eid, 12.0)
            row = c.execute("SELECT predicted_at, actual_at FROM calibration_log WHERE id = ?", (eid,)).fetchone()
            self.assertEqual(tuple(row), (STAMP, STAMP))
            # a resolved prediction made now sits inside a 30-day window read on the same clock
            for _ in range(4):
                e = CM.log_prediction(c, 10.0, "minutes", "time", "build")
                CM.log_outcome(c, e, 12.0)
            res = CM.compute_correction_factor(c, "build", "time", lookback_days=30, min_samples=3)
            self.assertEqual(res["sample_size"], 5, res)
        finally:
            c.close()


class TheSnoozeLastsWhatItSays(_Fixed):
    def test_one_hour_is_written_utc_and_read_on_the_same_clock(self):
        _report(self.brain.path, "2026-09")                   # an open unit, there before the snooze was set
        self.assertEqual(VA.set_snooze(1.0, db=self.brain.path).get("status"), "ok")
        until = self.q("SELECT snooze_until FROM approval_ping_state WHERE scope = 'global'")[0][0]
        self.assertEqual(until, "2026-06-03 10:00:00")
        # 59 minutes later the snooze quiets the unit; 61 minutes later it has ended -- on the brain's clock
        for minutes, quiet in ((59, True), (61, False)):
            with mock.patch.object(brain_clock, "utc_now", return_value=NOW + timedelta(minutes=minutes)):
                st = VA.should_ping(db=self.brain.path, persist=False)
            self.assertEqual(st.get("status"), "ok", st)
            self.assertEqual(bool(st.get("quiet_unit_keys")), quiet, (minutes, st))


    def test_the_banner_and_the_notifier_read_it_on_the_same_clock(self):
        # they ask decision_units.attention_signal() with no clock of their own
        import decision_units as DU
        _report(self.brain.path, "2026-09")
        self.assertEqual(VA.set_snooze(1.0, db=self.brain.path).get("status"), "ok")
        for minutes, quiet in ((59, True), (61, False)):
            with mock.patch.object(brain_clock, "utc_now", return_value=NOW + timedelta(minutes=minutes)):
                sig = DU.attention_signal(self.brain.path)
            self.assertEqual(bool(sig.quiet_keys), quiet, (minutes, sig))


class TheBeliefWatermarkIsUtc(_Fixed):
    def test_last_belief_scan_at(self):
        c = sqlite3.connect(self.brain.path)
        try:
            BE.nightly_scan(c, nli_model=lambda *a, **k: None)
            row = c.execute("SELECT value FROM belief_engine_state WHERE key = 'last_belief_scan_at'").fetchone()
        finally:
            c.close()
        self.assertEqual(row[0] if row else None, STAMP)


class OneClock(unittest.TestCase):
    def test_sleep_loop_reads_the_same_clock(self):
        import sleep_loop as SL
        with mock.patch.object(brain_clock, "utc_now", return_value=NOW):
            self.assertEqual(SL._utc_stamp(), STAMP)


if __name__ == "__main__":
    unittest.main()
