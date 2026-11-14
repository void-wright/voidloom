# -*- coding: utf-8 -*-
"""An approved self-eval proposal that needs engineering lands on an open-work list, and is not enacted again.

"Enacting" a self-eval proposal writes one procedural memory and nothing else -- no task, no ticket. Under auto mode
the policy approved every title on every report, so the same engineering proposals were approved and "enacted" month
after month (five times for one title on a measured brain), each time one more memory and no work. Now:
  * `approvals --list` carries an "approved, awaiting work" section: every enacted title, its status `awaiting-work`
    (nothing in the brain records that the work was done -- closure states do not exist yet), and every report it was
    enacted on;
  * the policy folds a title already enacted on an earlier report into that open item instead of enacting it again:
    no second memory, and the decision says which report holds it.

A packaged scratch brain per test; the policy step and the enactment run for real.
"""
from __future__ import annotations

import argparse
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
from test_approvals_policy_d26 import _report, ENH  # noqa: E402

import approvals_policy as AP  # noqa: E402
from voidloom import cli  # noqa: E402

NEW = {"title": "Rotate the dream ledger", "rationale": "r", "evidence": "e", "priority": "low",
       "estimated_effort": "1h", "piece_affected": "L4-P6-gap-mapping"}


class _Auto(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="vl-await-", dir=os.environ.get("TMPDIR")))
        self.cfg = str(self.tmp / "wizard-config.json")
        Path(self.cfg).write_text(json.dumps({}) + "\n")
        self.env = mock.patch.dict(os.environ, {"WIZARD_CONFIG_PATH": self.cfg, "VOIDLOOM_ACTOR": "the-owner"})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.brain = Brain(self.tmp)
        self.brain.c.close()
        AP.set_mode("auto", set_by="owner")

    def tasks(self):
        c = sqlite3.connect(self.brain.path)
        try:
            return c.execute("SELECT count(*) FROM memory_tags WHERE tag = 'self-eval-approved'").fetchone()[0]
        finally:
            c.close()

    def listing(self):
        return cli.verb_approvals(argparse.Namespace(db=self.brain.path, json=True))["data"]


class ARepeatedTitleIsFoldedNotEnactedAgain(_Auto):

    def test_the_second_report_enacts_only_the_new_title(self):
        first = _report(self.brain.path, "2026-08")
        self.assertEqual(AP.decide_new_self_eval_report(self.brain.path, first)["status"], "ok")
        self.assertEqual(self.tasks(), 2)
        second = _report(self.brain.path, "2026-09", enh=ENH + [NEW])
        out = AP.decide_new_self_eval_report(self.brain.path, second)
        self.assertEqual(out["status"], "ok", out)
        self.assertEqual(out["approved"], [NEW["title"]], out)
        self.assertEqual(sorted(out["folded"]), sorted(p["title"] for p in ENH), out)
        # Independent of the decision's own words: one memory more, not three.
        self.assertEqual(self.tasks(), 3)

    def test_the_list_shows_every_enacted_title_awaiting_work(self):
        first = _report(self.brain.path, "2026-08")
        AP.decide_new_self_eval_report(self.brain.path, first)
        second = _report(self.brain.path, "2026-09", enh=ENH + [NEW])
        AP.decide_new_self_eval_report(self.brain.path, second)
        data = self.listing()
        open_work = {w["title"]: w for w in data["approved_awaiting_work"]}
        self.assertEqual(set(open_work), {p["title"] for p in ENH} | {NEW["title"]}, data)
        self.assertTrue(all(w["status"] == "awaiting-work" for w in open_work.values()), open_work)
        self.assertEqual(open_work[ENH[0]["title"]]["reports"], [first], open_work)
        self.assertEqual(open_work[NEW["title"]]["reports"], [second], open_work)

    def test_a_title_enacted_twice_before_this_change_lists_both_reports_once(self):
        # Brains already carry the repetition (a measured brain: one title enacted on five reports).
        first = _report(self.brain.path, "2026-07")
        second = _report(self.brain.path, "2026-08")
        for rid in (first, second):
            with mock.patch.object(AP, "_titles_awaiting_work", return_value=({}, True)):
                AP.decide_new_self_eval_report(self.brain.path, rid)
        self.assertEqual(self.tasks(), 4)
        rows = [w for w in self.listing()["approved_awaiting_work"] if w["title"] == ENH[0]["title"]]
        self.assertEqual(len(rows), 1, rows)
        self.assertEqual(rows[0]["reports"], [first, second], rows)


if __name__ == "__main__":
    unittest.main()
