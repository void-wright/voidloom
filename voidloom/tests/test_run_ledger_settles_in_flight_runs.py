# -*- coding: utf-8 -*-
"""The organ run ledger records each run's OWN result: a run seen in flight is `running`, and settled once it ends.

`observe` read the service's Result at observation time and wrote it with INSERT OR IGNORE keyed on the trigger. A
run still in progress showed the PREVIOUS run's Result -- so a run that then timed out was filed `success`, and the
IGNORE meant no later look could correct it. Measured on a live brain: thirty days of `success` for three units whose
journal showed timeouts and a failed backup. Doctor, the organ-yield sentinel and every health read take this table
at its word.

Now: in flight (ActiveState activating/active/deactivating, or an exit stamp older than the trigger) is written
`running`; a later look that sees the run ended moves that row to its own result, once (a final result is never
rewritten); a run superseded before anyone saw it end is said to be `ended-unobserved`, never `success`. Doctor and
the sentinel name failed runs.

The systemd reads are replaced by stand-ins (no user manager); the brain is a packaged scratch copy.
"""
from __future__ import annotations

import argparse
import contextlib
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

import organ_run_log as ORL  # noqa: E402
from voidloom import cli  # noqa: E402

UNIT = "wizard-library-pre-cognitive.timer"
SERVICE = "wizard-library-pre-cognitive.service"
T1 = datetime(2026, 11, 4, 12, 0, 55)          # systemd stamps are local, naive


def _sd(dt):
    return dt.strftime("Wed %Y-%m-%d %H:%M:%S EST") if dt else ""


class _Ledger(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="vl-runlog-", dir=os.environ.get("TMPDIR")))
        self.b = Brain(self.tmp)
        self.b.c.close()
        self.trigger = T1
        self.service = {}

    def _units(self, *a, **k):
        return [ORL.DeclaredUnit(unit_name=UNIT, unit_file="/x/" + UNIT, service_name=SERVICE, oncalendar=[],
                                 persistent=True, unit_file_state="enabled", active_state="active",
                                 last_trigger=self.trigger)]

    def _show(self, unit, props):
        return {k: [v] for k, v in self.service.items() if k in props}

    def look(self, **service):
        self.service = dict(service)
        with mock.patch.object(ORL, "discover_declared_units", self._units), \
                mock.patch.object(ORL, "_show", self._show):
            return ORL.observe(self.b.path, now=self.trigger + timedelta(minutes=5))

    def rows(self):
        c = sqlite3.connect(self.b.path)
        try:
            return c.execute("SELECT run_started_at, result, run_ended_at FROM organ_run_log WHERE unit_name = ? "
                             "ORDER BY run_started_at", (UNIT,)).fetchall()
        finally:
            c.close()


class ARunSeenInFlightIsSettled(_Ledger):

    def test_running_then_its_own_timeout(self):
        before = T1 - timedelta(minutes=30)
        # mid-run: the service is activating and still shows the PREVIOUS run's success
        self.look(Result="success", ExecMainStatus="0", ActiveState="activating",
                  ExecMainStartTimestamp=_sd(T1), ExecMainExitTimestamp=_sd(before + timedelta(minutes=1)))
        self.assertEqual([r[1] for r in self.rows()], ["running"])
        # the run timed out and was killed
        end = T1 + timedelta(minutes=7)
        self.look(Result="timeout", ExecMainStatus="0", ActiveState="failed",
                  ExecMainStartTimestamp=_sd(T1), ExecMainExitTimestamp=_sd(end))
        self.assertEqual(self.rows(), [(ORL._utc_stamp(T1), "timeout", ORL._utc_stamp(end))])
        # a final result is never rewritten by a later look
        self.look(Result="success", ExecMainStatus="0", ActiveState="inactive",
                  ExecMainStartTimestamp=_sd(T1), ExecMainExitTimestamp=_sd(end))
        self.assertEqual([r[1] for r in self.rows()], ["timeout"])

    def test_a_run_superseded_before_it_was_seen_to_end_is_not_a_success(self):
        self.look(Result="success", ExecMainStatus="0", ActiveState="activating",
                  ExecMainStartTimestamp=_sd(T1), ExecMainExitTimestamp=_sd(T1 - timedelta(minutes=29)))
        self.trigger = T1 + timedelta(minutes=30)
        self.look(Result="success", ExecMainStatus="0", ActiveState="inactive",
                  ExecMainStartTimestamp=_sd(self.trigger),
                  ExecMainExitTimestamp=_sd(self.trigger + timedelta(minutes=2)))
        self.assertEqual([r[1] for r in self.rows()], ["ended-unobserved", "success"])

    def test_control_a_finished_run_is_recorded_with_its_result(self):
        self.look(Result="exit-code", ExecMainStatus="3", ActiveState="failed",
                  ExecMainStartTimestamp=_sd(T1), ExecMainExitTimestamp=_sd(T1 + timedelta(minutes=1)))
        self.assertEqual([r[1] for r in self.rows()], ["exit-code"])


class DoctorAndTheSentinelSeeTheFailure(_Ledger):

    def setUp(self):
        super().setUp()
        self.look(Result="timeout", ExecMainStatus="0", ActiveState="failed",
                  ExecMainStartTimestamp=_sd(T1), ExecMainExitTimestamp=_sd(T1 + timedelta(minutes=7)))
        # make the planted run recent, so a 7-day window holds it
        c = sqlite3.connect(self.b.path)
        c.execute("UPDATE organ_run_log SET run_started_at = datetime('now', '-1 hours') WHERE unit_name = ?", (UNIT,))
        c.commit()
        c.close()

    def test_the_doctor_check(self):
        out = cli._doctor_organ_runs(self.b.path)
        self.assertEqual([f["result"] for f in out["failed"]], ["timeout"], out)
        self.assertIn("FAILED", out["line"])
        self.assertIn(SERVICE, out["line"])

    def test_the_doctor_report_warns(self):
        plats = {"claude": {"state": "NOT_INSTALLED", "ours": [], "config_warning": None}}
        with mock.patch.object(cli, "doctor_hook_join", return_value={"platforms": plats}), \
                mock.patch.object(cli, "_absent_model", return_value=None):
            env = cli.verb_doctor(argparse.Namespace(db=self.b.path, cwd=None, platform=None, probe_platform=False))
        warnings = env["data"]["warnings"]
        self.assertTrue([w for w in warnings if "FAILED" in w and SERVICE in w], warnings)

    def test_the_sentinel_note(self):
        import organ_yield_sentinel as OYS
        c = sqlite3.connect(self.b.path)
        c.row_factory = sqlite3.Row
        try:
            note = OYS._skip_note(c, SERVICE, 7)
        finally:
            c.close()
        self.assertIn("FAILED", note)


class TheLedgerObservingItsOwnUnit(_Ledger):
    """The ledger's own unit is in flight whenever it looks at itself: its run reads `running`, and the next look used to
    supersede that row as ended-unobserved, so the ledger's own runs never read success. From inside its run (the
    service's InvocationID is this process's $INVOCATION_ID) it settles its previous run only when the manager still
    shows that run's exit stamp, before this trigger. systemd clears it when a run starts, so there the previous
    run stays ended-unobserved: an unknown result is never written as a success."""

    def own(self, inv, trigger, **service):
        self.trigger = trigger
        with mock.patch.dict(os.environ, {"INVOCATION_ID": inv}):
            return self.look(InvocationID=inv, ActiveState="active", ExecMainStartTimestamp=_sd(trigger), **service)

    def test_saboteur_its_own_previous_run_is_settled_with_its_own_result(self):
        T2 = T1 + timedelta(hours=1)
        self.own("aaa1", T1, Result="success", ExecMainStatus="0", ExecMainExitTimestamp=_sd(T1 - timedelta(minutes=58)))
        self.assertEqual([r[1] for r in self.rows()], [ORL.RUNNING])
        self.own("bbb2", T2, Result="success", ExecMainStatus="0", ExecMainExitTimestamp=_sd(T1 + timedelta(seconds=40)))
        rows = self.rows()
        self.assertEqual([r[1] for r in rows], ["success", ORL.RUNNING], rows)
        self.assertIsNotNone(rows[0][2], "the settled run carries its exit stamp")

    def test_saboteur_a_failed_previous_run_is_settled_failed(self):
        self.own("aaa1", T1, Result="success", ExecMainStatus="0")
        self.own("bbb2", T1 + timedelta(hours=1), Result="exit-code", ExecMainStatus="1",
                 ExecMainExitTimestamp=_sd(T1 + timedelta(seconds=40)))
        self.assertEqual([r[1] for r in self.rows()][0], "exit-code")

    def test_saboteur_systemd_resets_the_result_when_the_run_starts(self):
        # systemd resets Result to success and clears the exit status and stamp when a service starts, so from inside
        # its own run the ledger sees nothing of the previous run. It must not settle that run as a success: with no
        # exit stamp before this trigger, the previous run's result is unknown and reads ended-unobserved.
        self.own("aaa1", T1, Result="success", ExecMainStatus="0")
        self.own("bbb2", T1 + timedelta(hours=1), Result="success", ExecMainStatus="0")
        self.assertEqual([r[1] for r in self.rows()], [ORL.ENDED_UNOBSERVED, ORL.RUNNING])

    def test_control_another_units_superseded_run_is_still_unobserved(self):
        self.own("aaa1", T1, Result="success", ExecMainStatus="0")
        self.trigger = T1 + timedelta(hours=1)
        with mock.patch.dict(os.environ, {"INVOCATION_ID": "not-this-unit"}):
            self.look(InvocationID="bbb2", ActiveState="active", Result="success", ExecMainStatus="0",
                      ExecMainStartTimestamp=_sd(self.trigger))
        self.assertEqual([r[1] for r in self.rows()], [ORL.ENDED_UNOBSERVED, ORL.RUNNING])

    def test_control_no_invocation_id_matches_nothing(self):
        with mock.patch.dict(os.environ, {"INVOCATION_ID": ""}):
            self.assertFalse(ORL._is_this_run({"InvocationID": [""]}))
        with mock.patch.dict(os.environ, {"INVOCATION_ID": "x"}):
            self.assertFalse(ORL._is_this_run({}))
            self.assertTrue(ORL._is_this_run({"InvocationID": ["x"]}))


if __name__ == "__main__":
    unittest.main()
