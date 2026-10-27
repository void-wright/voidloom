# -*- coding: utf-8 -*-
"""2026-10-26 diagnostics: the monthly self-eval called itself dormant.

Report #8 (run 2026-10-01, evaluating 2026-09) proposed "Activate
L4-P9-self-eval -- zero activity". It had run twice in September. It counted its
own fires as reports whose `month` equals the month under evaluation, and that
report is the one being written at scoring time, so the count was always 0.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "organs") not in sys.path:
    sys.path.insert(0, str(ROOT / "organs"))

import monthly_self_eval as se  # noqa: E402

def _piece_def():
    for attr in dir(se):
        v = getattr(se, attr)
        if isinstance(v, dict):
            for sub in v.values():
                if isinstance(sub, dict) and "L4-P9-self-eval" in sub:
                    return sub["L4-P9-self-eval"]
    raise AssertionError("L4-P9-self-eval piece definition not found")


def _brain(rows):
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE self_eval_reports (id INTEGER PRIMARY KEY, month TEXT, "
              "created_at TEXT, status TEXT)")
    c.executemany("INSERT INTO self_eval_reports (month, created_at, status) VALUES (?, ?, 'enacted')",
                  rows)
    return c


def test_a_month_in_which_the_self_eval_ran_is_not_scored_dormant():
    pd = _piece_def()
    # the owner's history: two runs on 2026-09-01 (about August); scoring September
    # happens on 2026-10-01 BEFORE that month's report row exists
    c = _brain([("2026-08", "2026-09-01 12:02:23"), ("2026-08", "2026-09-01 22:21:28"),
                ("2026-09", "2026-10-01")])   # the next month's first instant, date-only as month_end is (review 2, N10)
    r = se._score_cron_piece(c, "self_eval_reports", "2026-09", pd["description"], pd)
    assert r["usage"] == 2, r
    assert r["score"] > 0.3, r


def test_a_month_in_which_it_did_not_run_still_scores_zero():
    pd = _piece_def()
    c = _brain([("2026-07", "2026-08-01 12:02:00")])
    r = se._score_cron_piece(c, "self_eval_reports", "2026-09", pd["description"], pd)
    assert r["usage"] == 0
