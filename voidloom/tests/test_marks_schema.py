# -*- coding: utf-8 -*-
"""migration_046 as a program that does not know about it meets it.

What would break without each rule: a second run of the migration that changes a brain; a trigger lost in a partial
restore that nobody notices; a full-text index corrupted by a mark set during an insert; an undo that leaves the
immutability trigger gone; a 1.1.3 process that can no longer store, or that still sends.
"""
import json
import os
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _wall_brain as wb
from _wall_brain import Brain, Case, ROOT, data

sys.path.insert(0, str(ROOT / "organs"))
import mark_admin
import marks
import schema_migrator

OLD = "c023b23"


def old_tree(dst):
    """The tree of the release before this one, from this repository's own history, or None in a checkout without it."""
    for rev in ("v1.1.3", OLD):
        if subprocess.run(["git", "-C", str(ROOT), "cat-file", "-e", rev + "^{commit}"], capture_output=True).returncode == 0:
            break
    else:
        return None
    tar = Path(dst) / "old.tar"
    with open(tar, "wb") as fh:
        if subprocess.run(["git", "-C", str(ROOT), "archive", rev], stdout=fh, stderr=subprocess.DEVNULL).returncode:
            return None
    out = Path(dst) / "old"
    with tarfile.open(tar) as t:
        t.extractall(out)
    tar.unlink()
    return out


def dump(db):
    c = sqlite3.connect(db)
    try:
        return ([tuple(r) for r in c.execute("SELECT type, name, sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY 1, 2")],
                c.execute("SELECT COUNT(*), TOTAL(untrusted_source), TOTAL(research_mark) FROM memories").fetchone())
    finally:
        c.close()


class Migration(Case):
    def test_a_second_run_changes_nothing(self):
        b = Brain(self.tmp)
        b.add("a row", marked=True)
        before = dump(b.path)
        out = schema_migrator.apply(b.path)
        self.assertEqual((out["statements_executed"], out["missing"], out["complete"]), (0, [], True), out)
        self.assertEqual(dump(b.path), before)

    def test_a_lost_trigger_is_reported_put_back_by_apply_and_changes_no_row(self):
        b = Brain(self.tmp)
        b.add("a row")
        b.c.execute("DROP TRIGGER trg_marks_late_outside")
        b.c.commit()
        audit = schema_migrator.audit(b.path)
        self.assertEqual((audit["missing_triggers"], audit["complete"], audit["missing"]), (["trg_marks_late_outside"], False, []))
        schema_migrator.apply(b.path)
        self.assertTrue(schema_migrator.audit(b.path)["complete"])

    def test_a_trigger_that_lost_its_body_is_reported(self):
        b = Brain(self.tmp)
        b.c.execute("DROP TRIGGER trg_marks_edge_parent")
        b.c.execute("CREATE TRIGGER trg_marks_edge_parent AFTER INSERT ON memory_parents BEGIN SELECT 1; END")
        b.c.commit()
        self.assertEqual(schema_migrator.audit(b.path)["altered_triggers"], ["trg_marks_edge_parent"])

    def test_a_lost_column_is_reported(self):
        db = wb.premark_brain(self.tmp / "pre")
        audit = schema_migrator.audit(str(db))
        self.assertIn("memories.research_mark", audit["missing_columns"])
        self.assertFalse(audit["complete"])
        self.assertTrue(schema_migrator.apply(str(db))["complete"])

    def test_already_applied_can_be_decided(self):
        deltas, _final = schema_migrator.migration_deltas(ROOT / "migrations")
        objs, cols = deltas["migration_046_marks.sql"]
        self.assertTrue(objs and cols)
        self.assertIn("trg_marks_dispatch_closed", objs)

    def test_compat_names_the_migration(self):
        doc = json.loads((ROOT / "COMPAT.json").read_text())
        self.assertIn("migration_046_marks.sql", doc["schema_produced"]["migrations"])

    def test_the_seed_pack_loads_on_a_migrated_brain_and_marks_nothing(self):
        b = Brain(self.tmp)
        p = subprocess.run([sys.executable, "-B", str(ROOT / "organs" / "claude_memory_lite.py"), "load-seed", "--file",
                            str(ROOT / "seed" / "seed_pack.json")], env=wb.child_env(b.path), capture_output=True, text=True, timeout=300)
        self.assertEqual(p.returncode, 0, p.stdout[-400:] + p.stderr[-400:])
        self.assertGreater(b.one("SELECT COUNT(*) FROM memories WHERE seed = 1")[0], 10)
        self.assertEqual(b.one("SELECT COUNT(*) FROM memories WHERE untrusted_source IS NOT 0 OR research_mark IS NOT 0")[0], 0)

    def test_a_read_never_adds_the_column(self):
        """A column added by a recall would say every existing row is clean, when nothing has ever said so."""
        db = wb.premark_brain(self.tmp / "pre", drop_mark=True)
        b = wb.brain_over(db)
        b.add("gradle cache daemon pressure")
        wb.full_cli(db, "recall", "--query", "gradle cache daemon pressure")
        wb.full_cli(db, "context")
        self.assertNotIn("untrusted_source", wb.columns(b.c))


class Statements(Case):
    def test_a_mark_set_during_an_insert_leaves_the_text_index_whole(self):
        b = Brain(self.tmp)
        cur = b.c.execute("INSERT INTO memories (content, summary, category, source_project) VALUES ('zebra crossing', 'zebra', 'insight', 'gap-research')")
        self.assertEqual(b.one("SELECT last_insert_rowid()")[0], cur.lastrowid)
        self.assertTrue(b.marked(cur.lastrowid))
        b.c.execute("INSERT INTO memories_fts(memories_fts, rank) VALUES ('integrity-check', 1)")      # raises if it is not
        self.assertEqual(b.all("SELECT rowid FROM memories_fts WHERE memories_fts MATCH 'zebra'"), [(cur.lastrowid,)])

    def test_an_edit_of_the_text_still_reaches_the_index(self):
        b = Brain(self.tmp)
        mid = b.add("zebra crossing")
        b.c.execute("UPDATE memories SET content = 'otter holt', summary = 'otter' WHERE id = ?", (mid,))
        b.c.execute("INSERT INTO memories_fts(memories_fts, rank) VALUES ('integrity-check', 1)")
        self.assertEqual((b.all("SELECT rowid FROM memories_fts WHERE memories_fts MATCH 'zebra'"),
                          b.all("SELECT rowid FROM memories_fts WHERE memories_fts MATCH 'otter'")), ([], [(mid,)]))

    def test_an_odd_mark_cannot_be_cleared_to_clean(self):
        b = Brain(self.tmp)
        mid = b.add("row")
        wb.plant_odd_mark(b.c, mid, 2)
        with self.assertRaises(sqlite3.Error):
            b.c.execute("UPDATE memories SET untrusted_source = 0 WHERE id = ?", (mid,))
        b.c.rollback()
        b.c.execute("UPDATE memories SET untrusted_source = 1 WHERE id = ?", (mid,))        # and may be made plain

    def test_a_research_run_reaches_descendants_through_every_edge_with_the_first_run_named(self):
        b = Brain(self.tmp)
        for run in ("r1", "r2"):
            marks.register(b.path, {"model": "m"}, run)
        cid = b.one("SELECT config_id FROM research_runs LIMIT 1")[0]
        rows = {r: b.add("by " + r, research_run_id=r, research_config_id=cid) for r in ("r1", "r2")}
        child, grand = b.add("child"), b.add("grandchild")
        b.c.execute("INSERT INTO memory_parents VALUES (?, ?)", (grand, child))
        for r in ("r1", "r2"):
            b.c.execute("INSERT INTO memory_parents VALUES (?, ?)", (child, rows[r]))
        for i in (child, grand):
            self.assertEqual(b.one("SELECT untrusted_source, research_mark, research_run_id FROM memories WHERE id = ?", (i,)), (1, 1, "r1"))
            self.assertEqual(b.all("SELECT run_id, inherited FROM memory_research_runs WHERE memory_id = ? ORDER BY 1", (i,)),
                             [("r1", 1), ("r2", 1)])

    def test_a_hard_delete_takes_the_new_rows_that_name_the_memory(self):
        import memory_write as mw
        b = Brain(self.tmp)
        b.c.execute("PRAGMA foreign_keys = ON")
        p, c = b.add("a parent with a long enough text to be a copy of something"), b.add("child")
        b.c.execute("INSERT INTO memory_parents VALUES (?, ?)", (c, p))
        mark_admin_row = b.c.execute("INSERT INTO forget_receipts (receipt_id, memory_id, before_archived) VALUES ('x', ?, 0)", (p,))
        b.c.commit()
        mw.purge_memory(b.c, p)
        b.c.commit()
        self.assertEqual((b.one("SELECT COUNT(*) FROM memory_parents")[0], b.one("SELECT COUNT(*) FROM forget_receipts")[0]), (0, 0))


class Undo(Case):
    def brain(self, n=1):
        b = Brain(self.tmp)
        p = b.add("web row")
        kids = [b.add("child %d" % i) for i in range(n)]
        trg = b.all("SELECT name, sql FROM sqlite_master WHERE type = 'trigger'")
        for name, _sql in trg:                  # what an older release left: edges written before any of these triggers
            b.c.execute("DROP TRIGGER %s" % name)
        b.c.executemany("INSERT INTO consolidation_sources (consolidated_memory_id, source_memory_id, cluster_id) VALUES (?, ?, 1)",
                        [(k, p) for k in kids])
        b.mark(p)
        for _name, sql in trg:
            b.c.execute(sql)
        b.c.commit()
        return b, p, kids

    def test_undo_restores_the_rows_and_the_triggers_and_a_mark_is_immutable_again(self):
        b, p, kids = self.brain(3)
        before = dump(b.path)
        self.assertEqual(mark_admin.backfill(b.path)["rows_changed"], 3)
        self.assertEqual(mark_admin.backfill_undo(b.path)["rows_changed"], 3)
        self.assertEqual(dump(b.path), before)
        with self.assertRaises(sqlite3.Error):
            b.c.execute("UPDATE memories SET untrusted_source = 0 WHERE id = ?", (p,))

    def test_an_undo_that_fails_part_way_leaves_every_mark_and_every_trigger(self):
        b, _p, kids = self.brain(2)
        mark_admin.backfill(b.path)
        b.c.execute("CREATE TRIGGER planted BEFORE UPDATE OF undone_at ON marks_backfill_preimage BEGIN SELECT RAISE(ABORT, 'planted'); END")
        b.c.commit()
        before = dump(b.path)
        with self.assertRaises(marks.MarksError):
            mark_admin.backfill_undo(b.path)
        self.assertEqual(dump(b.path), before)

    def test_a_backfill_run_twice_changes_nothing_the_second_time(self):
        b, _p, _kids = self.brain(2)
        self.assertEqual([mark_admin.backfill(b.path)["rows_changed"] for _ in range(2)], [2, 0])

    def test_over_the_limit_it_halts_and_yes_many_goes_on(self):
        b, _p, kids = self.brain(mark_admin.BACKFILL_LIMIT + 1)
        rc, env, _err = wb.full_cli(b.path, "marks", "backfill")
        self.assertEqual((rc != 0, env["code"], sum(b.marked(k) for k in kids)), (True, "BACKFILL_HALTED", 0))
        rc, env, _err = wb.full_cli(b.path, "marks", "backfill", "--yes-many")
        self.assertEqual((rc, data(env)["rows_changed"], all(b.marked(k) for k in kids)), (0, len(kids), True))


    def test_the_migration_can_be_taken_out_and_put_back_and_the_older_shape_is_what_is_left(self):
        """The rollback of the file itself, on a copy: nothing of it is left, every row stands, and it applies again."""
        old = wb.premark_brain(self.tmp / "old")
        db = wb.premark_brain(self.tmp / "db")
        c = sqlite3.connect(db)
        c.execute("INSERT INTO memories (content, category) VALUES ('kept', 'insight')")
        c.commit()
        self.assertTrue(schema_migrator.apply(str(db))["complete"])
        c.execute("UPDATE memories SET untrusted_source = 1")
        c.commit()
        for (name,) in c.execute("SELECT name FROM sqlite_master WHERE type = 'trigger' AND name LIKE 'trg_marks_%'").fetchall():
            c.execute("DROP TRIGGER %s" % name)
        for table in (*marks.NEW_TABLES, "forget_receipts", "marks_backfill_preimage"):
            c.execute("PRAGMA foreign_keys = OFF")
            c.execute("DROP TABLE %s" % table)
        for col in marks.NEW_COLUMNS:
            c.execute("ALTER TABLE memories DROP COLUMN %s" % col)
        for table, col in (("access_log", "event_id"), ("access_log", "machine"), ("gap_research_log", "approval_hash"),
                           ("gap_research_log", "outcome")):
            c.execute("ALTER TABLE %s DROP COLUMN %s" % (table, col))
        c.commit()

        def shape(conn):
            return {(t, n): sorted(r[1] for r in conn.execute('PRAGMA table_info("%s")' % n)) if t == "table" else None
                    for t, n in conn.execute("SELECT type, name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'")
                    if not n.startswith(("memories_fts_", "memory_embeddings"))}
        self.assertEqual(shape(c), shape(sqlite3.connect(old)))
        self.assertEqual(c.execute("SELECT content, untrusted_source FROM memories").fetchall(), [("kept", 1)])
        with self.assertRaises(sqlite3.Error):
            c.execute("UPDATE memories SET untrusted_source = 0")
        c.rollback()
        c.close()
        self.assertTrue(schema_migrator.apply(str(db))["complete"])


class OlderRelease(Case):
    """A real 1.1.3 process on a migrated database."""

    def setUp(self):
        super().setUp()
        self.old = old_tree(self.tmp)
        if self.old is None:
            self.skipTest("this checkout has no history to take the older release from")

    def run_old(self, b, code, **env):
        e = wb.child_env(b.path, OLD=str(self.old), DB=b.path, **env)
        e["PYTHONPATH"] = str(self.old)
        pre = "import os, sys; sys.path[:0] = [os.environ['OLD'] + '/organs', os.environ['OLD']]\n"
        return subprocess.run([sys.executable, "-B", "-c", pre + code], env=e, cwd=str(self.tmp), capture_output=True, text=True, timeout=240)

    def test_its_plain_store_works_and_is_an_owner_write(self):
        b = Brain(self.tmp / "b")
        p = subprocess.run([sys.executable, "-B", str(self.old / "organs" / "claude_memory_lite.py"), "store", "--content",
                            "an owner row by the older release", "--category", "insight"],
                           env=wb.child_env(b.path), capture_output=True, text=True, timeout=120)
        self.assertEqual(p.returncode, 0, p.stderr[-300:])
        mid = json.loads(p.stdout)["memory_id"]
        self.assertEqual(b.one("SELECT content, untrusted_source FROM memories WHERE id = ?", (mid,)), ("an owner row by the older release", 0))

    def test_its_research_row_is_marked_by_the_database(self):
        b = Brain(self.tmp / "b")
        p = self.run_old(b, "import sqlite3\nc = sqlite3.connect(os.environ['DB'])\n"
                            "c.execute(\"INSERT INTO memories (content, summary, category, importance, source_project, source_context, kind, created_at) "
                            "VALUES ('web text', 's', 'domain_knowledge', 0.5, 'gap-research', 'x', 'semantic', datetime('now'))\")\n"
                            "c.commit(); import warden; print(warden.is_untrusted(c, 1))")
        self.assertEqual((p.returncode, p.stdout.strip()), (0, "True"), p.stderr[-400:])

    def test_its_dispatch_stops_before_it_sends(self):
        b = Brain(self.tmp / "b")
        b.c.execute("INSERT INTO gap_inventory_batches (started_at, config_snapshot) VALUES (datetime('now'), '{}')")
        b.c.execute("INSERT INTO gap_inventory (entity, gap_type, score, auto_research_eligible, batch_id) VALUES ('a topic', 'thin_entity', 0.9, 1, 1)")
        b.c.commit()
        p = self.run_old(b, "import gap_mapping as gm\nsent = []\n"
                            "gm._dispatch_research = lambda p, t=0: sent.append(p) or {'status': 'success', 'output': 'x', 'wall_clock_seconds': 0}\n"
                            "try:\n    gm.dispatch_auto_research(os.environ['DB'])\nexcept Exception as e:\n    print(type(e).__name__, e)\nprint('SENT', len(sent))")
        self.assertEqual(p.returncode, 0, p.stderr[-400:])
        self.assertIn("RESEARCH_DISPATCH_CLOSED", p.stdout)
        self.assertIn("SENT 0", p.stdout)

    def test_its_sleep_writer_leaves_no_unmarked_child(self):
        b = Brain(self.tmp / "b")
        words = "gradle cache daemon memory pressure kills the build lane variant "
        ids = [b.add(words + ch * 4) for ch in "abcd"]
        b.mark(ids[0])
        p = self.run_old(b, "sys.path.insert(0, %r)\nimport _wall_brain as wb\nimport sleep_loop\nc = wb.connect(os.environ['DB'])\n"
                            "with wb.engine(os.environ['DB']):\n"
                            "    r = sleep_loop.consolidate_episodics(c, min_cluster_size=3, similarity_threshold=0.5)\n"
                            "import memory_write; print(os.path.dirname(os.path.abspath(sleep_loop.__file__)))\nprint(r[0]['consolidated_memory_id'])"
                         % str(self.old / "voidloom" / "tests"))
        if p.returncode != 0:
            # the fixture helper is newer than the older tree: lend it
            (self.old / "voidloom" / "tests" / "_wall_brain.py").write_bytes((ROOT / "voidloom" / "tests" / "_wall_brain.py").read_bytes())
            p = self.run_old(b, "sys.path.insert(0, %r)\nimport _wall_brain as wb\nimport sleep_loop\nc = wb.connect(os.environ['DB'])\n"
                                "with wb.engine(os.environ['DB']):\n"
                                "    r = sleep_loop.consolidate_episodics(c, min_cluster_size=3, similarity_threshold=0.5)\n"
                                "print(os.path.dirname(os.path.abspath(sleep_loop.__file__)))\nprint(r[0]['consolidated_memory_id'])"
                             % str(self.old / "voidloom" / "tests"))
        self.assertEqual(p.returncode, 0, p.stderr[-600:])
        where, child = p.stdout.strip().splitlines()[-2:]
        self.assertEqual(Path(where).resolve(), (self.old / "organs").resolve(), "the new organ ran, not the older one")
        self.assertTrue(b.marked(int(child)))


if __name__ == "__main__":
    unittest.main()
