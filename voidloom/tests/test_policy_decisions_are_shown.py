# -*- coding: utf-8 -*-
"""What the brain decided without a person is shown where a person looks: `approvals --list`, the TUI and `--present`.

In auto mode nothing waits in the queue, so every face said "the void is quiet" while the policy had approved a whole
self-eval report; the only notice was a session-start line whose window was 7 days, after which the decision could be
seen only in SQL. Each face now carries a "decided by the policy" section for the last 30 days, and the session line
uses the same window. A decision older than the window is not shown.

A packaged scratch brain per test; the policy step runs for real; nothing touches a live brain.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import sqlite3
import sys
import tempfile
import unittest
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
import voidloom_preamble as PRE  # noqa: E402
from voidloom import cli  # noqa: E402
from voidloom import _approvals_faces as faces  # noqa: E402


class _Decided(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="vl-decided-", dir=os.environ.get("TMPDIR")))
        self.cfg = str(self.tmp / "wizard-config.json")
        Path(self.cfg).write_text(json.dumps({}) + "\n")
        self.env = mock.patch.dict(os.environ, {"WIZARD_CONFIG_PATH": self.cfg, "VOIDLOOM_ACTOR": "the-owner"})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.brain = Brain(self.tmp)
        self.brain.c.close()
        AP.set_mode("auto", set_by="owner")
        self.rid = _report(self.brain.path, "2026-09")
        out = AP.decide_new_self_eval_report(self.brain.path, self.rid)
        self.assertEqual(out["status"], "ok", out)
        self.ref = "self-eval:%d" % self.rid

    def age(self, days):
        c = sqlite3.connect(self.brain.path)
        c.execute("UPDATE approval_audit_log SET created_at = datetime('now', ?) WHERE ref = ?",
                  ("-%d days" % days, self.ref))
        c.commit()
        c.close()

    def approvals(self, **kw):
        return cli.verb_approvals(argparse.Namespace(db=self.brain.path, json=True, **kw))


class APolicyDecisionTenDaysOldIsShown(_Decided):
    def setUp(self):
        super().setUp()
        self.age(10)

    def test_the_window_is_thirty_days(self):
        self.assertEqual(AP.DECIDED_WINDOW_DAYS, 30.0)
        self.assertEqual([r["ref"] for r in AP.recent_policy_decisions(self.brain.path, AP.DECIDED_WINDOW_DAYS)],
                         [self.ref])

    def test_the_session_line_still_says_it(self):
        lines = PRE.read_policy_decisions(self.brain.path, str(_ROOT / "organs"))
        self.assertEqual(len(lines), 1, lines)

    def test_list(self):
        env = self.approvals()
        data = env["data"]
        # nothing is pending -- and that true sentence now stands beside what was decided
        self.assertEqual(data["unified_count"], 0, data)
        self.assertEqual([d["ref"] for d in data["decided_by_policy"]], [self.ref], data)
        self.assertTrue(data["decided_by_policy"][0]["line"].startswith("AUTO-DECIDED %s on " % self.ref), data)
        self.assertIn("2 approved", data["decided_by_policy"][0]["line"])

    def test_present(self):
        data = self.approvals(present=True)["data"]
        self.assertIn("AUTO-DECIDED %s" % self.ref, data["card"])
        self.assertEqual([d["ref"] for d in data["decided_by_policy"]], [self.ref])

    def test_tui(self):
        buf = io.StringIO()
        backend = cli._import_approvals_backend()
        with mock.patch.object(faces, "is_tty", return_value=True), \
                mock.patch("builtins.input", return_value="q"), contextlib.redirect_stdout(buf):
            cli._verb_tui(backend, self.brain.path, face="ansi")
        self.assertIn("AUTO-DECIDED %s" % self.ref, buf.getvalue())


class APolicyDecisionOlderThanTheWindowIsNot(_Decided):
    def test_forty_days(self):
        self.age(40)
        self.assertEqual(self.approvals()["data"]["decided_by_policy"], [])
        self.assertEqual(PRE.read_policy_decisions(self.brain.path, str(_ROOT / "organs")), [])


if __name__ == "__main__":
    unittest.main()
