# -*- coding: utf-8 -*-
"""R4: a reversible soft archive with a receipt, by id or by run; undo restores the state before, never more."""
import argparse
import contextlib
import io
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _wall_brain as wb
from _wall_brain import Brain, Case, data, unknown_flag

class Forget(Case):
    def cli(self, b, *a):
        rc, env, err = wb.full_cli(b.path, *a)
        self.needs(not unknown_flag(err), "verb `%s`" % " ".join(a[:2]))
        return rc, data(env)

    def arch(self, b, *ids):
        return [b.one("SELECT is_archived FROM memories WHERE id=?", (i,))[0] for i in ids]

    def test_control_delete_still_soft_archives(self):
        b = Brain(self.tmp)
        i = b.add("row")
        with wb.engine(b.path) as cm, contextlib.redirect_stdout(io.StringIO()):
            cm.cmd_delete(argparse.Namespace(id=i, hard=False))
        self.assertEqual(self.arch(b, i), [1])

    def test_red_forget_and_undo_by_id(self):
        b = Brain(self.tmp)
        i, other = b.add("row"), b.add("other")
        rc, r = self.cli(b, "forget", "--id", str(i))
        rec = r.get("receipt", r)
        self.assertEqual((rc, rec["ids"], rec["rows_changed"], rec["reversible"]), (0, [i], 1, True))
        self.assertIn("unarchive", rec["undo"])
        self.assertEqual(self.arch(b, i, other), [1, 0])
        self.assertEqual(self.cli(b, "unarchive", "--id", str(i))[0], 0)
        self.assertEqual(self.arch(b, i, other), [0, 0])

    def test_red_dry_run_changes_nothing(self):
        b = Brain(self.tmp)
        i = b.add("row")
        rc, r = self.cli(b, "forget", "--id", str(i), "--dry-run")
        self.assertEqual((rc, self.arch(b, i), r.get("receipt", r)["rows_changed"]), (0, [0], 0))

    def test_red_unarchive_never_revives_what_forget_did_not_archive(self):
        b = Brain(self.tmp)
        i = b.add("archived by its owner long ago", is_archived=1)
        self.cli(b, "unarchive", "--id", str(i))
        self.assertEqual(self.arch(b, i), [1])

    def test_red_forget_by_run_takes_the_run_and_lists_the_mixed(self):
        import json
        b = Brain(self.tmp)
        f = self.tmp / "c.json"
        f.write_text(json.dumps({"model": "m"}))
        for run in ("r1", "r2"):
            self.cli(b, "research-register", "--config", str(f), "--run", run)
        ids = {}
        for run in ("r1", "r2"):
            ids[run] = self.cli(b, "store", "--content", "text of " + run, "--category", "insight", "--research", run)[1]["memory_id"]
        mixed, owner = b.add("child of both runs"), b.add("owner row")
        for p in ids.values():
            b.c.execute("INSERT INTO memory_parents (child_id, parent_id) VALUES (?,?)", (mixed, p))
        pre = b.add("already archived", is_archived=1)
        b.c.commit()
        rc, r = self.cli(b, "forget", "--run", "r1")
        self.assertEqual((rc, self.arch(b, ids["r1"], ids["r2"], mixed, owner)), (0, [1, 0, 0, 0]))
        self.assertIn(str(mixed), re.findall(r"\d+", str(r.get("receipt", r))), "the mixed child is not listed")
        self.cli(b, "unarchive", "--run", "r1")
        self.assertEqual(self.arch(b, ids["r1"], pre), [0, 1])


class Backfill(Case):
    def test_red_marks_backfill_is_reported_reversible_and_never_follows_edge_inference(self):
        b = Brain(self.tmp)
        p, child, grand, owner = b.add("web"), b.add("child"), b.add("grandchild"), b.add("owner row")
        trg = b.all("SELECT name, sql FROM sqlite_master WHERE type='trigger'")
        for n, _s in trg:                                       # edges written by 1.1.3, before any wall trigger
            b.c.execute("DROP TRIGGER %s" % n)
        e = "INSERT INTO memory_provenance (derived_memory_id, source_memory_id, transformation_type) VALUES (?,?,?)"
        b.c.executemany(e, [(child, p, "consolidation"), (grand, child, "reflection"), (owner, p, "edge_inference")])
        b.mark(p)
        for _n, s in trg:
            b.c.execute(s)
        b.c.commit()

        def go(*a):
            rc, env, err = wb.full_cli(b.path, "marks", "backfill", *a)
            self.needs(not unknown_flag(err), "marks backfill " + " ".join(a))
            return [b.marked(i) for i in (child, grand, owner)]
        self.assertEqual(go("--dry-run"), [False, False, False])
        self.assertEqual(go(), [True, True, False])
        self.assertEqual(go("--undo"), [False, False, False])


if __name__ == "__main__":
    unittest.main()
