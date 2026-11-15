"""The readers that age UTC rows, or cut windows over them, use the brain's own clock (brain_clock), never local time.

Every column the schema stamps itself (`datetime('now')`) is UTC, 'YYYY-MM-DD HH:MM:SS'. A reader that ages such a row
against datetime.now() is off by the local offset, and a cutoff written with .isoformat() carries a 'T' that sorts
after every same-date stamp. Each site below moved onto brain_clock.utc_now() / utc_cutoff().
  * saboteur (behaviour): with brain_clock pinned to a UTC instant and each module's local clock skewed ten hours
    away from it, a memory's age, a recency boost and the windows the dream digest, the assayer and the heatmap bind
    are the UTC answers;
  * saboteur (the class): none of the listed readers calls datetime.now() any more;
  * control: the deliberately local sites (an hour-of-day, a month label) are left alone.
"""
from __future__ import annotations

import ast
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

NOW = datetime(2026, 6, 3, 9, 0, 0)          # a fixed UTC instant
SKEW = timedelta(hours=10)                     # the local clock, ten hours away
FMT = "%Y-%m-%d %H:%M:%S"

LISTED = {                                     # module: the functions that age UTC rows or cut windows over them
    "cognitive_map": ["get_temporal_heatmap"],
    "persona_lenses": ["_compute_recency_boost"],
    "procedural_memory": ["compute_promotion_score", "get_review_candidates"],
    "skill_graduation": ["_get_usage_stats_for_memory", "_get_memory_age_days", "evaluate_memory"],
    "memory_dreaming": ["get_dream_digest"],
    "assayer": ["score_pending_dreams", "_augment_with_wild_pool", "run_bakeoff"],
    "claude_memory": ["cmd_maintain"],
    "website_memory": ["cmd_maintain"],
}


class _SkewedLocal(datetime):
    @classmethod
    def now(cls, tz=None):
        return (brain_clock.utc_now() + SKEW) if tz is None else datetime.now(tz)     # ten hours off the pinned UTC


class _Clock(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="vl-brainclock-", dir=os.environ.get("TMPDIR")))
        self.b = Brain(self.tmp, at=NOW)
        p = mock.patch.object(brain_clock, "utc_now", return_value=NOW)
        p.start()
        self.addCleanup(p.stop)

    def skew(self, mod):
        p = mock.patch.object(mod, "datetime", _SkewedLocal)
        p.start()
        self.addCleanup(p.stop)

    def bound(self, fn, pattern):
        seen = []
        self.b.c.set_trace_callback(seen.append)
        try:
            fn()
        finally:
            self.b.c.set_trace_callback(None)
        return [m.group(1) for s in seen for m in [re.search(pattern, s)] if m]


class TheReadersAgeOnTheBrainClock(_Clock):
    def test_saboteur_a_memorys_age_is_counted_in_utc(self):
        import skill_graduation as sg
        self.skew(sg)
        mid = self.b.add("a procedure", kind="procedural")
        self.b.c.execute("UPDATE memories SET created_at = ? WHERE id = ?", ((NOW - timedelta(hours=47)).strftime(FMT), mid))
        self.b.c.commit()
        self.assertEqual(sg._get_memory_age_days(self.b.c, mid), 1)        # 47 hours: one whole day, not two

    def test_saboteur_a_recency_boost_ages_in_utc(self):
        import persona_lenses as pl
        self.skew(pl)
        fresh = {"created_at": (NOW - timedelta(hours=20)).strftime(FMT)}
        self.assertEqual(pl._compute_recency_boost(fresh, 7), 1.5)          # zero whole days old: the full boost

    def test_saboteur_the_dream_digest_window_is_utc_in_the_stored_format(self):
        import memory_dreaming as md
        self.skew(md)
        cut = self.bound(lambda: md.get_dream_digest(self.b.c, days=7), r"'(\d{4}-\d\d-\d\d[ T]\d\d:\d\d:\d\d[^']*)'")
        self.assertIn((NOW - timedelta(days=7)).strftime(FMT), cut, cut)

    def test_saboteur_the_heatmap_window_is_the_utc_date(self):
        import cognitive_map as cm
        self.skew(cm)
        late = datetime(2026, 6, 3, 20, 0, 0)            # 20:00 UTC: ten hours later locally is the NEXT date
        with mock.patch.object(brain_clock, "utc_now", return_value=late):
            cut = self.bound(lambda: cm.get_temporal_heatmap(self.b.c, weeks=1), r"'(\d{4}-\d\d-\d\d)'")
        self.assertIn((late - timedelta(weeks=1)).strftime("%Y-%m-%d"), cut, cut)

    def test_saboteur_the_assayer_window_is_utc_in_the_stored_format(self):
        import assayer
        self.skew(assayer)
        with mock.patch.object(assayer, "is_enabled", return_value=True), \
                mock.patch.object(assayer, "_get_scorer", return_value=(object(), None)), \
                mock.patch.object(assayer, "_table_exists", return_value=True):              # no model is loaded
            cut = self.bound(lambda: assayer.score_pending_dreams(self.b.c, days=7),
                             r"'(\d{4}-\d\d-\d\d[ T]\d\d:\d\d:\d\d[^']*)'")
        self.assertTrue(cut, "the assayer bound no window")
        self.assertIn((NOW - timedelta(days=7)).strftime(FMT), cut, cut)

    # Local readings that age no row: a wall-clock duration, and one writer outside this change (a declared shortfall:
    # the assayer stamps scored_at in local ISO time).
    ALLOWED = ("t0 = datetime.now()", "(datetime.now() - t0)", "datetime.now().isoformat(), delta")

    def test_saboteur_no_listed_reader_calls_datetime_now(self):
        found = []
        lines = {}
        for mod, fns in LISTED.items():
            src = (_ROOT / "organs" / f"{mod}.py").read_text(encoding="utf-8")
            lines[mod] = src.splitlines()
            tree = ast.parse(src)
            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef) and node.name in fns:
                    for call in ast.walk(node):
                        if (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
                                and call.func.attr == "now" and isinstance(call.func.value, ast.Name)
                                and call.func.value.id == "datetime" and not call.args and not call.keywords
                                and not any(a in lines[mod][call.lineno - 1] for a in self.ALLOWED)):
                            found.append(f"{mod}.{node.name}:{call.lineno}")
        self.assertEqual(found, [])

    def test_control_the_deliberately_local_sites_stay_local(self):
        for mod, needle in (("monthly_self_eval", "datetime.now()"), ("pre_cognitive_recall", "datetime.now()")):
            self.assertIn(needle, (_ROOT / "organs" / f"{mod}.py").read_text(encoding="utf-8"), mod)


if __name__ == "__main__":
    unittest.main()
