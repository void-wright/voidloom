# -*- coding: utf-8 -*-
"""R5: an unreadable mark means outside, for every predicate; maintain never archives a person's row on a similarity."""
import argparse
import contextlib
import io
import re
import sqlite3
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _wall_brain as wb
from _wall_brain import Brain, Case, data

from unittest import mock

SAME = "identical twin text about the build lane and its cache"


class Dedup(Case):
    def maintain(self, b):
        out = io.StringIO()
        with wb.engine(b.path) as cm, contextlib.redirect_stdout(out):
            cm.cmd_maintain(argparse.Namespace(skip_dedup=False))
        return wb.last_json(out.getvalue())

    def archived(self, b, *ids):
        return [b.one("SELECT is_archived FROM memories WHERE id=?", (i,))[0] for i in ids]

    def author(self, b, mid, who):
        if "author" in wb.columns(b.c):
            b.c.execute("UPDATE memories SET author=? WHERE id=?", (who, mid))
            b.c.commit()

    def machine(self, b, text=SAME, **kw):
        return b.add(text, kind="reflection", derived_from="whisper_synthesis", confidence=0.5, **kw)

    def test_control_two_machine_twins_still_deduplicate(self):
        b = Brain(self.tmp)
        ids = self.machine(b, importance=0.6), self.machine(b, importance=0.4)
        self.maintain(b)
        self.assertEqual(self.archived(b, *ids), [0, 1])

    def test_control_different_texts_are_untouched(self):
        b = Brain(self.tmp)
        ids = b.add("violin rosin bow"), b.add("orchid watering bark")
        self.maintain(b)
        self.assertEqual(self.archived(b, *ids), [0, 0])

    def test_red_a_user_row_is_never_archived_by_an_outside_twin(self):
        b = Brain(self.tmp)
        u = b.add(SAME, importance=0.2, source_user="human")
        self.author(b, u, "user")
        o = b.add(SAME, importance=0.9, marked=True)
        self.maintain(b)
        self.assertEqual(self.archived(b, u, o), [0, 0], "an outside row archived the owner's own row")

    def test_red_a_row_with_no_author_is_protected(self):
        b = Brain(self.tmp)
        ids = b.add(SAME, importance=0.2), b.add(SAME, importance=0.9)
        self.maintain(b)
        self.assertEqual(self.archived(b, *ids), [0, 0], "author NULL and no machine evidence: archived on similarity")

    def test_red_machine_rows_never_merge_across_marks(self):
        b = Brain(self.tmp)
        clean, web = self.machine(b, importance=0.4, tags=("mine",)), self.machine(b, importance=0.9, tags=("theirs",))
        b.mark(web)
        self.maintain(b)
        self.assertEqual(self.archived(b, clean, web), [0, 0])
        self.assertEqual(b.one("SELECT COUNT(*) FROM memory_tags WHERE memory_id=? AND tag='mine'", (web,))[0], 0)

    def test_red_tags_never_cross_the_mark_line(self):
        b = Brain(self.tmp)
        clean, web = self.machine(b, importance=0.9, tags=("mine",)), self.machine(b, importance=0.4, tags=("theirs",))
        b.mark(web)
        self.maintain(b)
        self.assertEqual([t for t, in b.all("SELECT tag FROM memory_tags WHERE memory_id=?", (clean,))], ["mine"])

    def test_red_an_already_archived_row_archives_nothing(self):
        """A~B and B~C but not A~C: B goes to A, and the dead B must not then take C with it."""
        b = Brain(self.tmp)
        w = ["wordnumber%02d" % i for i in range(12)]
        ids = [self.machine(b, " ".join(w[i:i + 10]), importance=imp) for i, imp in ((0, 0.6), (1, 0.5), (2, 0.4))]
        self.maintain(b)
        self.assertEqual(self.archived(b, *ids), [0, 1, 0])

    def test_red_protected_rows_are_counted(self):
        b = Brain(self.tmp)
        b.add(SAME, importance=0.2)
        b.add(SAME, importance=0.9)
        env = self.maintain(b)
        self.assertIn("dedup_protected", str(env), "maintain reports nothing about the rows it spared")


class Unreadable(Case):
    """A present-but-odd value (2, a word) is not a clean row to any predicate that decides trust or inheritance."""

    def odd(self, value):
        b = Brain(self.tmp / ("odd%s" % value))
        mid = b.add("row with an odd mark about the cache daemon build lane")
        wb.plant_odd_mark(b.c, mid, value)
        return b, mid

    def test_control_zero_is_clean_and_one_is_outside(self):
        import warden
        b = Brain(self.tmp)
        self.assertEqual([warden.is_untrusted(b.c, b.add("c")), warden.is_untrusted(b.c, b.add("m", marked=True))], [False, True])

    def test_red_warden_reads_an_odd_value_as_outside(self):
        import warden
        self.assertEqual([warden.is_untrusted(*(lambda t: (t[0].c, t[1]))(self.odd(v))) for v in (2, "odd")], [True, True])

    def test_red_trust_is_capped_on_a_falsy_odd_value(self):
        import trust_tier as tt
        b, mid = self.odd("")                                  # '' is present, not 0, and falsy
        self.assertLessEqual(tt.compute_trust(b.c, mid), tt.UNTRUSTED_SOURCE_CEILING)

    def test_red_trust_is_capped_when_the_mark_cannot_be_read(self):
        """`database is locked` is not `the column is absent`: the legacy fallback drops the ceiling on any error."""
        import trust_tier as tt
        b = Brain(self.tmp)
        mid = b.add("a marked row", marked=True)

        class Flaky:
            def execute(self, sql, *a):
                if "untrusted_source" in sql:
                    raise sqlite3.OperationalError("database is locked")
                return b.c.execute(sql, *a)
        try:
            score = tt.compute_trust(Flaky(), mid)
        except sqlite3.OperationalError:
            return                                             # an error is not a trusted score
        self.assertLessEqual(score, tt.UNTRUSTED_SOURCE_CEILING)

    def test_red_a_child_of_an_odd_row_is_marked(self):
        import dream_pipeline as dp
        import sleep_loop as sl
        got = {}
        for v in (2, "odd"):
            b, mid = self.odd(v)
            for i in range(3):
                b.add("row with an odd mark about the cache daemon build lane %s" % ("abc"[i] * 4))
            with wb.engine(b.path):
                r = sl.consolidate_episodics(b.c, min_cluster_size=3, similarity_threshold=0.5)
                got["consolidate", v] = b.marked(r[0]["consolidated_memory_id"])
                got["cluster", v] = dp._cluster_has_untrusted_member(b.c, [mid])
        self.assertEqual(set(got.values()), {True}, got)

    def test_red_cluster_taint_fails_closed_on_a_query_error(self):
        import dream_pipeline as dp

        class Broken:
            def execute(self, *a):
                raise sqlite3.OperationalError("database is locked")
        self.assertTrue(dp._cluster_has_untrusted_member(Broken(), [1, 2]), "a query error read as `no marked member`")


if __name__ == "__main__":
    unittest.main()
