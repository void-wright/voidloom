# -*- coding: utf-8 -*-
"""Putting things back: a trigger, a backfill, an archived row, an edge that was changed.

WHAT WOULD BREAK WITHOUT IT: an undo that hands a web page back as the owner's own words; a trigger that is there by name
and does nothing; an unarchive that brings back a row its owner deleted in between.
"""
import argparse
import contextlib
import io
import json
import sqlite3
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _wall_brain as wb
from _wall_brain import Brain, Case, ROOT, data

sys.path.insert(0, str(ROOT / "organs"))
import mark_admin
import marks
import schema_migrator

SPEC = {"k": 1}


def triggers(c):
    return dict(c.execute("SELECT name, sql FROM sqlite_master WHERE type = 'trigger'").fetchall())


class Edges:
    """A parent and what was written from it while no trigger looked."""

    def __init__(self, case, name="b", research=False):
        b = self.b = Brain(case.tmp / name)
        self.parent, self.child, self.grand = b.add("web page"), b.add("child row"), b.add("grandchild row")
        self.other = b.add("another web page")
        if research:
            marks.register(b.path, SPEC, "run1")
            marks.register(b.path, SPEC, "run2")
        kept = triggers(b.c)
        for n in kept:
            b.c.execute(f"DROP TRIGGER {n}")
        b.c.executemany("INSERT INTO memory_parents (child_id, parent_id) VALUES (?, ?)",
                        [(self.child, self.parent), (self.grand, self.child)])
        for q in kept.values():
            b.c.execute(q)
        b.c.commit()
        # the parent is marked with the late triggers out, which is how an older release left it
        for n in ("trg_marks_late_outside", "trg_marks_late_research", "trg_marks_run_linked"):
            b.c.execute(f"DROP TRIGGER {n}")
        if research:
            cfg = b.one("SELECT config_id FROM research_runs WHERE run_id = 'run1'")[0]
            b.c.execute("UPDATE memories SET research_mark = 1, untrusted_source = 1, research_run_id = 'run1', research_config_id = ? WHERE id = ?", (cfg, self.parent))
        else:
            b.c.execute("UPDATE memories SET untrusted_source = 1 WHERE id = ?", (self.parent,))
        for n in ("trg_marks_late_outside", "trg_marks_late_research", "trg_marks_run_linked"):
            b.c.execute(kept[n])
        b.c.commit()

    def outside(self, *ids):
        return [self.b.marked(i) for i in ids]


class Backfill(Case):
    def test_a_mark_acquired_after_the_backfill_survives_its_undo(self):
        e = Edges(self)
        self.assertEqual(sorted(mark_admin.backfill(e.b.path)["ids"]), [e.child, e.grand])
        e.b.mark(e.other)
        e.b.c.execute("INSERT INTO memory_parents (child_id, parent_id) VALUES (?, ?)", (e.child, e.other))
        e.b.c.commit()
        out = mark_admin.backfill_undo(e.b.path)
        self.assertEqual(e.outside(e.child, e.grand), [True, True])     # the second parent, and what descends from its child
        self.assertEqual((out["rows_changed"], [k["id"] for k in out["kept"]]), (0, [e.child, e.grand]))
        self.assertTrue(marks.shown(e.b.c, e.child, content="child row")["content"].startswith(wb.FRAME))

    def test_only_the_rows_with_another_reason_are_kept(self):
        e = Edges(self)
        mark_admin.backfill(e.b.path)
        e.b.mark(e.other)
        e.b.c.execute("INSERT INTO memory_parents (child_id, parent_id) VALUES (?, ?)", (e.grand, e.other))
        e.b.c.commit()
        out = mark_admin.backfill_undo(e.b.path)
        self.assertEqual(e.outside(e.child, e.grand), [False, True])
        self.assertEqual((out["ids"], [k["id"] for k in out["kept"]]), ([e.child], [e.grand]))

    def test_a_parent_marked_late_is_another_reason_and_a_gone_one_is(self):
        for n, late in enumerate((True, False)):
            e = Edges(self, f"late{n}")
            e.b.c.execute("INSERT INTO memory_parents (child_id, parent_id) VALUES (?, ?)", (e.child, e.other))
            e.b.c.commit()
            mark_admin.backfill(e.b.path)
            if late:
                e.b.mark(e.other)
            else:
                e.b.c.execute("DELETE FROM memories WHERE id = ?", (e.other,))
                e.b.c.commit()
            mark_admin.backfill_undo(e.b.path)
            self.assertEqual(e.outside(e.child, e.grand), [True, True], late)

    def test_an_undo_with_nothing_new_puts_every_row_back(self):
        e = Edges(self, research=True)
        before = e.b.all("SELECT id, untrusted_source, research_mark, research_run_id, research_config_id FROM memories ORDER BY id")
        links = e.b.all("SELECT * FROM memory_research_runs ORDER BY 1, 2")
        trg = triggers(e.b.c)
        mark_admin.backfill(e.b.path)
        out = mark_admin.backfill_undo(e.b.path)
        self.assertEqual((out["rows_changed"], out["kept"]), (2, []))
        self.assertEqual(e.b.all("SELECT id, untrusted_source, research_mark, research_run_id, research_config_id FROM memories ORDER BY id"), before)
        self.assertEqual(e.b.all("SELECT * FROM memory_research_runs ORDER BY 1, 2"), links)
        self.assertEqual(triggers(e.b.c), trg)

    def test_research_descendants_are_linked_to_their_run_and_forgotten_with_it(self):
        e = Edges(self, research=True)
        mark_admin.backfill(e.b.path)
        for m in (e.child, e.grand):
            self.assertEqual(e.b.one("SELECT research_mark, research_run_id, untrusted_source FROM memories WHERE id = ?", (m,)), (1, "run1", 1))
            self.assertEqual(e.b.all("SELECT run_id, inherited FROM memory_research_runs WHERE memory_id = ?", (m,)), [("run1", 1)])
        got = mark_admin.forget(e.b.path, run_id="run1", include_mixed=True)["receipt"]["ids"]
        self.assertEqual(sorted(got), [e.parent, e.child, e.grand])
        self.assertEqual(mark_admin.backfill(e.b.path)["rows_changed"], 0)

    def test_a_run_gained_after_the_backfill_keeps_its_row_research(self):
        e = Edges(self, research=True)
        mark_admin.backfill(e.b.path)
        cfg = e.b.one("SELECT config_id FROM research_runs WHERE run_id = 'run2'")[0]
        new = e.b.add("run2 row", research_mark=1, untrusted_source=1, research_run_id="run2", research_config_id=cfg)
        e.b.c.execute("INSERT INTO memory_parents (child_id, parent_id) VALUES (?, ?)", (e.child, new))
        e.b.c.commit()
        mark_admin.backfill_undo(e.b.path)
        self.assertEqual(e.b.one("SELECT research_mark, untrusted_source FROM memories WHERE id = ?", (e.child,)), (1, 1))
        self.assertIn(("run2",), e.b.all("SELECT run_id FROM memory_research_runs WHERE memory_id = ?", (e.grand,)))
        self.assertEqual(e.outside(e.grand), [True])


class Triggers(Case):
    def gut(self, b, name="trg_marks_edge_parent"):
        b.c.execute(f"DROP TRIGGER {name}")
        b.c.execute(f"CREATE TRIGGER {name} AFTER INSERT ON memory_parents BEGIN SELECT 1; END")
        b.c.commit()

    def test_a_gutted_trigger_is_reported_and_the_next_store_puts_it_back(self):
        b = Brain(self.tmp)
        shipped = triggers(b.c)
        self.gut(b)
        self.assertEqual((marks.health(b.path)["verdict"], marks.health(b.path)["altered_triggers"]),
                         ("MARK_TRIGGERS_ALTERED", ["trg_marks_edge_parent"]))
        _rc, env, _err = wb.full_cli(b.path, "doctor")
        self.assertIn("MARK_TRIGGERS_ALTERED", json.dumps(env))
        self.assertNotEqual(env["code"], "doctor_ok")
        wb.full_cli(b.path, "store", "--content", "an owner note", "--category", "insight")
        self.assertEqual(triggers(b.c), shipped)
        self.assertEqual(marks.health(b.path)["verdict"], "ok")
        p, c = b.add("web page", marked=True), b.add("child")
        b.c.execute("INSERT INTO memory_parents (child_id, parent_id) VALUES (?, ?)", (c, p))
        b.c.commit()
        self.assertTrue(b.marked(c))

    def test_every_shipped_trigger_is_compared(self):
        b = Brain(self.tmp)
        self.assertEqual(marks.wrong_triggers(b.c), {})
        names = set(marks.shipped_triggers())
        self.assertIn("trg_untrusted_source_immutable", names)
        self.assertIn(marks.FTS_TRIGGER, names)
        held = {n for n in triggers(b.c) if n.startswith("trg_marks_")}
        self.assertEqual(held, {n for n in names if n.startswith("trg_marks_")})
        for n in sorted(names):
            b.c.execute(f"DROP TRIGGER {n}")
            self.assertEqual(marks.wrong_triggers(b.c), {} if n == marks.FTS_TRIGGER else {n: "missing"}, n)
            b.c.execute(marks.shipped_triggers()[n])
        self.assertEqual(marks.wrong_triggers(b.c), {})

    def test_an_apply_after_a_full_text_trigger_was_lost_leaves_the_narrow_one(self):
        for name in ("memories_fts_after_insert", "memories_fts_after_delete", "memories_fts_after_update"):
            b = Brain(self.tmp / name)
            shipped = triggers(b.c)
            if name not in shipped:
                name = next(n for n in shipped if n.startswith("memories_fts_") and name.rsplit("_", 1)[1] in n)
            b.c.execute(f"DROP TRIGGER {name}")
            b.c.commit()
            out = schema_migrator.apply(b.path)
            self.assertTrue(out["complete"], out)
            self.assertIn("UPDATE OF id, summary, content", triggers(b.c)["memories_fts_after_update"])
            m = b.add("gradle daemon", untrusted_source=1)
            b.c.execute("INSERT INTO memories_fts(memories_fts) VALUES ('integrity-check')")
            self.assertTrue(b.marked(m))

    def test_a_wide_full_text_trigger_is_reported_by_audit_and_doctor(self):
        b = Brain(self.tmp)
        b.c.execute("DROP TRIGGER memories_fts_after_update")
        b.c.execute("CREATE TRIGGER memories_fts_after_update AFTER UPDATE ON memories BEGIN SELECT 1; END")
        b.c.commit()
        self.assertEqual(schema_migrator.audit(b.path)["altered_triggers"], ["memories_fts_after_update"])
        self.assertEqual(marks.health(b.path)["altered_triggers"], ["memories_fts_after_update"])
        self.assertEqual(marks.ensure_triggers(b.c), ["memories_fts_after_update"])
        self.assertTrue(schema_migrator.audit(b.path)["complete"])

    def test_a_newer_releases_trigger_is_left_alone_and_a_lost_one_is_not(self):
        b = Brain(self.tmp)
        self.gut(b)
        b.c.execute("DROP TRIGGER trg_marks_late_outside")
        b.c.execute("INSERT INTO schema_migrations (name) VALUES ('migration_999_later.sql')")
        b.c.commit()
        self.assertEqual(marks.ensure_triggers(b.c), ["trg_marks_late_outside"])
        self.assertEqual(marks.wrong_triggers(b.c), {"trg_marks_edge_parent": "altered"})

    def test_a_package_with_no_migrations_to_compare_is_not_called_well(self):
        b = Brain(self.tmp)
        kept, marks._TRIGGERS = marks._TRIGGERS, {}
        try:
            self.assertEqual(marks.health(b.path)["verdict"], "UNMEASURED")
        finally:
            marks._TRIGGERS = kept


class Forget(Case):
    def test_a_later_decision_is_not_undone(self):
        b = Brain(self.tmp)
        marks.register(b.path, SPEC, "run1")
        cfg = b.one("SELECT config_id FROM research_runs")[0]
        ids = [b.add(f"row {i}", research_mark=1, untrusted_source=1, research_run_id="run1", research_config_id=cfg) for i in range(5)]
        mark_admin.forget(b.path, run_id="run1")
        kept, deleted, revived, edited, gone = ids
        with wb.engine(b.path) as cm, contextlib.redirect_stdout(io.StringIO()):       # in the same second as the forget
            cm.cmd_delete(argparse.Namespace(id=deleted, hard=False))
        b.c.execute("UPDATE memories SET is_archived = 0 WHERE id = ?", (revived,))      # by hand, and archived again
        b.c.execute("UPDATE memories SET is_archived = 1 WHERE id = ?", (revived,))
        b.c.execute("UPDATE memories SET content = 'edited', updated_at = '2030-01-01 00:00:00' WHERE id = ?", (edited,))
        b.c.execute("DELETE FROM memories WHERE id = ?", (gone,))
        b.c.commit()
        receipt = mark_admin.unarchive(b.path, run_id="run1")["receipt"]
        self.assertEqual((receipt["ids"], receipt["rows_changed"]), ([kept], 1))
        self.assertEqual({s["id"] for s in receipt["skipped"]}, {deleted, revived, edited, gone})
        self.assertTrue(all(s["reason"] for s in receipt["skipped"]))
        self.assertEqual([b.one("SELECT is_archived FROM memories WHERE id = ?", (i,))[0] for i in (kept, deleted, revived, edited)],
                         [0, 1, 1, 1])

    def test_one_memory_the_same_way(self):
        b = Brain(self.tmp)
        m = b.add("row")
        mark_admin.forget(b.path, memory_id=m)
        b.c.execute("UPDATE memories SET is_archived = 0 WHERE id = ?", (m,))
        b.c.execute("UPDATE memories SET is_archived = 1 WHERE id = ?", (m,))
        b.c.commit()
        receipt = mark_admin.unarchive(b.path, memory_id=m)["receipt"]
        self.assertEqual((receipt["rows_changed"], [s["id"] for s in receipt["skipped"]]), (0, [m]))
        self.assertEqual(b.one("SELECT is_archived FROM memories WHERE id = ?", (m,))[0], 1)


class LateEdges(Case):
    def test_an_edge_that_is_changed_carries_its_new_parents_marks(self):
        cases = (("memory_parents", "child_id", "parent_id", {}),
                 ("consolidation_sources", "consolidated_memory_id", "source_memory_id", {"cluster_id": 1}),
                 ("memory_provenance", "derived_memory_id", "source_memory_id", {"transformation_type": "reflection"}))
        for table, child, parent, more in cases:
            for moved in (parent, child):
                b = Brain(self.tmp / f"{table}-{moved}")
                marks.register(b.path, SPEC, "run1")
                cfg = b.one("SELECT config_id FROM research_runs")[0]
                clean, kid, kid2 = b.add("clean"), b.add("kid"), b.add("kid two")
                web = b.add("web", research_mark=1, untrusted_source=1, research_run_id="run1", research_config_id=cfg)
                first = {child: kid, parent: clean, **more} if moved == parent else {child: web, parent: web, **more}
                if moved == child:
                    first[child] = clean
                    first[parent] = web
                cols = list(first)
                if moved == child:                      # the edge web -> clean marks `clean`; then it is moved onto kid2
                    clean2 = b.add("clean two")
                    first[child] = clean2
                b.c.execute(f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", [first[c] for c in cols])
                if moved == parent:
                    self.assertFalse(b.marked(kid))
                    b.c.execute(f"UPDATE {table} SET {parent} = ?", (web,))
                    got = kid
                else:
                    b.c.execute(f"UPDATE {table} SET {child} = ?", (kid2,))
                    got = kid2
                b.c.commit()
                self.assertEqual(b.one("SELECT untrusted_source, research_mark, research_run_id FROM memories WHERE id = ?", (got,)),
                                 (1, 1, "run1"), (table, moved))

    def test_a_comparison_that_becomes_a_derivation_marks(self):
        b = Brain(self.tmp)
        web, kid = b.add("web", marked=True), b.add("kid")
        b.c.execute("INSERT INTO memory_provenance (derived_memory_id, source_memory_id, transformation_type) VALUES (?, ?, 'edge_inference')", (kid, web))
        self.assertFalse(b.marked(kid))
        b.c.execute("UPDATE memory_provenance SET transformation_type = 'reflection'")
        b.c.commit()
        self.assertTrue(b.marked(kid))

    def test_a_marked_row_is_not_replaced_by_one_without_its_mark(self):
        b = Brain(self.tmp)
        web, mine = b.add("web", marked=True), b.add("mine")
        for verb in ("INSERT OR REPLACE", "REPLACE"):
            with self.assertRaisesRegex(sqlite3.Error, "MARKED_ROW_REPLACED"):
                b.c.execute(f"{verb} INTO memories (id, content, category) VALUES (?, 'clean now', 'insight')", (web,))
        b.c.execute("INSERT OR REPLACE INTO memories (id, content, category, untrusted_source) VALUES (?, 'still web', 'insight', 1)", (web,))
        b.c.execute("INSERT OR REPLACE INTO memories (id, content, category) VALUES (?, 'mine again', 'insight')", (mine,))
        b.c.execute("INSERT INTO memories (content, category) VALUES ('a plain store', 'insight')")
        b.c.execute("INSERT INTO memories (id, content, category) VALUES (90001, 'an id of its own', 'insight')")
        b.c.commit()
        self.assertEqual([b.marked(web), b.marked(mine)], [True, False])


if __name__ == "__main__":
    unittest.main()
