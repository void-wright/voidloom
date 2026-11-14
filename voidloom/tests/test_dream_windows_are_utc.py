# -*- coding: utf-8 -*-
"""The dream pipeline ages rows and cuts its windows on the brain's own clock: UTC, 'YYYY-MM-DD HH:MM:SS'.

access_log.accessed_at and memories.created_at are datetime('now') stamps -- UTC, a space between date and time. The
activity profile aged them against datetime.now() (LOCAL), and both it and the NREM co-recall window cut with
`.isoformat()` -- a 'T' that sorts after every same-date stamp, so the cutoff's whole date fell out of the window.
Measured on a copy of a live brain (UTC-5): the two-day NREM window selected 650 rows under the bug, 593 under the
correct UTC cutoff, and 400 under the tempting `datetime.utcnow().isoformat()`.

The window is pinned here by the cutoff the phase actually binds (read from the connection's statement trace), on a
fixed clock. A packaged scratch brain; no model.
"""
from __future__ import annotations

import math
import os
import re
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

import brain_clock  # noqa: E402
import dream_pipeline as dp  # noqa: E402

NOW = datetime(2026, 6, 3, 9, 0, 0)          # a fixed UTC instant, far from the wall clock
FMT = "%Y-%m-%d %H:%M:%S"


class _Clock(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="vl-utcwin-", dir=os.environ.get("TMPDIR")))
        self.b = Brain(self.tmp)
        p = mock.patch.object(brain_clock, "utc_now", return_value=NOW)
        p.start()
        self.addCleanup(p.stop)
        self.cfg = dict(dp.DEFAULT_CONFIG)

    def access(self, mid, hours_before):
        self.b.c.execute("INSERT INTO access_log (memory_id, access_type, accessed_at) VALUES (?, 'recall', ?)",
                         (mid, (NOW - timedelta(hours=hours_before)).strftime(FMT)))
        self.b.c.commit()

    def bound_cutoffs(self, fn):
        seen = []
        self.b.c.set_trace_callback(seen.append)
        try:
            fn()
        finally:
            self.b.c.set_trace_callback(None)
        return [m.group(1) for s in seen if "FROM access_log" in s
                for m in [re.search(r"accessed_at > '([^']+)'", s)] if m]


class TheNremWindow(_Clock):

    def test_the_cutoff_is_utc_in_the_stored_format(self):
        days = int(self.cfg["nrem_corecall_lookback_days"])
        cut = self.bound_cutoffs(lambda: dp.phase_nrem_consolidation(self.b.c, self.cfg, None))
        self.assertEqual(cut, [(NOW - timedelta(days=days)).strftime(FMT)], cut)

    def test_a_row_on_the_cutoffs_own_date_inside_the_window_is_selected(self):
        days = int(self.cfg["nrem_corecall_lookback_days"])
        mid = self.b.add("a recalled note", project="utc-window")
        self.access(mid, days * 24 - 1)          # inside the window, on the cutoff's own date
        self.access(mid, days * 24 + 1)          # outside it
        cut = self.bound_cutoffs(lambda: dp.phase_nrem_consolidation(self.b.c, self.cfg, None))[0]
        rows = self.b.all("SELECT accessed_at FROM access_log WHERE memory_id = ? AND accessed_at > ?", (mid, cut))
        self.assertEqual(len(rows), 1, (cut, rows))


class TheActivityProfile(_Clock):

    def test_a_recall_one_day_old_weighs_one_day(self):
        mid = self.b.add("a recalled note", project="utc-age-probe")
        self.access(mid, 24)
        dp.rebuild_activity_profile(self.b.c, self.cfg)
        row = self.b.one("SELECT recall_mass FROM dream_activity_profile WHERE dimension = 'source_project' "
                         "AND key = 'utc-age-probe'")
        half = max(0.5, float(self.cfg["activity_half_life_days"]))
        expected = math.exp(-math.log(2.0) / half * 1.0)      # recall_mass is the decayed sum, before weighting
        self.assertIsNotNone(row)
        self.assertAlmostEqual(row[0], expected, places=4)

    def test_the_profile_window_is_utc_in_the_stored_format(self):
        cut = self.bound_cutoffs(lambda: dp.rebuild_activity_profile(self.b.c, self.cfg))
        days = int(self.cfg["activity_lookback_days"])
        self.assertEqual(cut, [(NOW - timedelta(days=days)).strftime(FMT)], cut)


if __name__ == "__main__":
    unittest.main()
