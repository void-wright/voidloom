# -*- coding: utf-8 -*-
"""Where a row is shown, where a store is asked for, and where a call is pinned: the cases around the main ones.

Each names what would break: a hook that shows a row whose mark is neither 0 nor 1; a dream that quotes its other
endpoint plain; an engine that cannot mark storing an outside row as the owner's; a worker thread answering for the
default brain; a recall that orders an unmarked brain differently from the release before.
"""
import argparse
import contextlib
import io
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import threading
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _wall_brain as wb
from _wall_brain import Brain, Case, ROOT, data
from test_marks_schema import old_tree

sys.path.insert(0, str(ROOT / "organs"))
import engine_pin
import marks
import recall_channels
import voidloom_preamble
import warden


class Hooks(Case):
    """The session-start hook reads the brain through voidloom_preamble.read_priming."""

    def priming(self, b, selector):
        return voidloom_preamble.read_priming(b.path, lambda s: str(s or ""), selector=selector)["rows"]

    def test_a_marked_row_and_an_odd_one_are_never_shown_plain(self):
        for n, value in enumerate((1, 2, "", "odd")):
            b = Brain(self.tmp / str(n))
            mid = b.add("outside words", importance=1.0)
            b.add("owner words", importance=0.9)
            if value == 1:
                b.mark(mid)
            else:
                wb.plant_odd_mark(b.c, mid, value)
            for selector in ("trusted", "legacy"):
                by = {r["id"]: r for r in self.priming(b, selector)}
                if mid in by:
                    self.assertTrue(by[mid]["framed"], (value, selector, by[mid]))
                self.assertFalse(by[mid + 1]["framed"], (value, selector))

    def test_the_frame_is_spelled_once(self):
        self.assertEqual(marks.FRAME_PREFIX, voidloom_preamble.PROVENANCE_FRAME + "\n")
        self.assertEqual(marks.OUTSIDE_CONFIDENCE, warden.WEB_PROVENANCE_CONFIDENCE)
        self.assertEqual(marks.SENTINEL, warden.UNTRUSTED_DERIVED_FROM)


class Channels(Case):
    def test_a_side_row_and_the_endpoint_it_quotes_are_framed_when_outside(self):
        b = Brain(self.tmp)
        dream, web, clean = b.add("dream", kind="reflection", marked=True), b.add("web", marked=True), b.add("clean dream", kind="reflection")
        rows = [{"id": dream, "content": "[dream] x", "summary": "x", "provenance_frame_reason": ["dream"], "other": {"id": web, "summary": "web words"}},
                {"id": clean, "content": "[dream] y", "summary": "y", "provenance_frame_reason": ["dream"]}]
        recall_channels.present(b.c, rows)
        self.assertTrue(rows[0]["content"].startswith(wb.FRAME) and rows[0]["summary"].startswith(wb.FRAME))
        self.assertTrue(rows[0]["other"]["summary"].startswith(wb.FRAME))
        self.assertEqual(rows[0]["provenance_frame_reason"], ["dream", "untrusted_source"])
        self.assertEqual(rows[1]["content"], "[dream] y")           # a dream's own frame is enough for a clean dream

    def test_a_read_error_frames_a_side_row(self):
        class Broken:
            def execute(self, *a):
                raise sqlite3.OperationalError("database is locked")
        rows = [{"id": 1, "content": "[dream] x", "summary": "x"}]
        recall_channels.present(Broken(), rows)
        self.assertTrue(rows[0]["content"].startswith(wb.FRAME))
        self.assertEqual(rows[0]["degraded_reason"], "MARKS_UNREADABLE")


class OtherSurfaces(Case):
    def brain(self):
        b = Brain(self.tmp)
        self.web = b.add("gradle cache daemon web words", summary="web words", marked=True, importance=0.9, kind="procedural")
        self.clean = b.add("gradle cache daemon my words", summary="my words", importance=0.9, kind="procedural")
        return b

    def run_verb(self, b, fn, **ns):
        out = io.StringIO()
        with wb.engine(b.path) as cm, contextlib.redirect_stdout(out):
            getattr(cm, fn)(argparse.Namespace(**ns))
        return wb.last_json(out.getvalue())

    def test_stats_edges_and_promotions(self):
        b = self.brain()
        b.c.execute("INSERT INTO memory_edges (source_id, target_id, predicate, confidence) VALUES (?, ?, 'references', 0.9)", (self.clean, self.web))
        b.c.commit()
        stats = self.run_verb(b, "cmd_stats")
        by = {r["id"]: r["summary"] for r in stats["recent_memories"]}
        self.assertTrue(by[self.web].startswith(wb.FRAME))
        self.assertEqual(by[self.clean], "my words")
        edge = self.run_verb(b, "cmd_edges", id=self.clean, direction="both", predicate=None, current_only=True)["edges"][0]
        self.assertTrue(edge["connected_summary"].startswith(wb.FRAME))
        promo = self.run_verb(b, "cmd_promotions", promote=False, review=True)["review_candidates"]
        by = {r["memory_id"]: r["summary"] for r in promo}
        if self.web in by:
            self.assertTrue(by[self.web].startswith(wb.FRAME))

    def test_a_row_merged_from_another_brain_is_framed_whatever_this_brain_holds_under_its_id(self):
        b = self.brain()
        rows = [{"id": self.clean, "content": "from elsewhere", "summary": "s", "tier": "another-brain"}]
        marks.present_rows(b.c, rows)
        self.assertEqual(rows[0]["provenance_frame_reason"], ["other_brain"])
        self.assertTrue(rows[0]["content"].startswith(wb.FRAME))

    def test_presenting_twice_frames_once(self):
        b = self.brain()
        rows = [{"id": self.web, "content": "c", "summary": "s"}]
        for _ in range(2):
            marks.present_rows(b.c, rows)
        self.assertEqual(rows[0]["content"].count(wb.FRAME), 1)

    def test_update_kind_and_the_kind_backfill_never_raise_an_outside_row(self):
        import kind_taxonomy
        b = self.brain()
        b.c.execute("UPDATE memories SET confidence = 0.4, derived_from = ? WHERE id = ?", (marks.SENTINEL, self.web))
        b.c.execute("UPDATE memories SET kind = NULL WHERE 0")
        b.c.commit()
        self.run_verb(b, "cmd_update", id=self.web, content=None, summary=None, importance=None, category=None, kind="semantic", tags=None)
        self.run_verb(b, "cmd_update", id=self.clean, content=None, summary=None, importance=None, category=None, kind="semantic", tags=None)
        self.assertEqual(b.one("SELECT confidence, derived_from FROM memories WHERE id = ?", (self.web,)), (0.5, marks.SENTINEL))
        self.assertEqual(b.one("SELECT confidence FROM memories WHERE id = ?", (self.clean,))[0], 1.0)
        b.c.execute("UPDATE memories SET kind = 'episodic' WHERE id = ?", (self.web,))
        b.c.commit()
        kind_taxonomy.backfill_kinds(b.path, dry_run=False)
        self.assertLessEqual(b.one("SELECT confidence FROM memories WHERE id = ?", (self.web,))[0], 0.5)

    def test_rolling_back_a_restamp_does_not_make_an_outside_dream_trusted(self):
        import dream_ledger as dl
        b = Brain(self.tmp / "dl")
        a, c = b.add("gradle cache", marked=True), b.add("daemon pressure")
        d = b.add("[Dream synthesis — divergent]\nSource memories: #%d and #%d\n\nThe cache and the daemon share one cause." % (a, c),
                  summary="memories", kind="reflection", project="memory-dreaming", tags=("dream",), importance=0.6)
        with wb.engine(b.path):
            dl.apply(b.c, dl.plan(b.c), ["restamp"], str(self.tmp / "pre.json"), encoder=wb.fake_vec, stamp_date="20261125")
            self.assertTrue(b.marked(d))
            dl.rollback(b.c, json.loads((self.tmp / "pre.json").read_text()))
        derived, conf = b.one("SELECT derived_from, confidence FROM memories WHERE id = ?", (d,))
        self.assertTrue(derived and b.marked(d))
        self.assertLessEqual(conf, 0.5)


class Refusals(Case):
    def test_an_engine_that_cannot_mark_refuses_an_outside_store(self):
        from voidloom import _facade
        b = Brain(self.tmp)
        top = b.top()
        with wb.engine(b.path) as cm, mock.patch.object(cm, "SUPPORTS_MARKS", False):
            r = _facade.store("outside words", "insight", db_path=b.path, outside=True)
            plain = _facade.store("owner words", "insight", db_path=b.path)
        self.assertEqual((r["error"], plain["status"]), ("OUTSIDE_REQUIRES_MARK", "ok"))
        self.assertEqual(len(b.new_ids(top)), 1)

    def test_a_bare_copy_of_the_lite_engine_refuses_outside_and_frames_everything(self):
        b = Brain(self.tmp / "b")
        b.add("gradle cache daemon words")
        bare = self.tmp / "bare" / "claude_memory_lite.py"
        bare.parent.mkdir()
        shutil.copy2(ROOT / "organs" / "claude_memory_lite.py", bare)
        env = wb.child_env(b.path)
        env.pop("PYTHONPATH")
        run = lambda *a: subprocess.run([sys.executable, "-B", str(bare), *a], env=env, capture_output=True, text=True, timeout=60)
        top = b.top()
        p = run("store", "--content", "outside words", "--category", "insight", "--outside")
        self.assertEqual((p.returncode, json.loads(p.stdout)["code"], b.top()), (1, "OUTSIDE_REQUIRES_MARK", top))
        p = run("recall", "--query", "gradle cache")
        rows = json.loads(p.stdout)["results"]
        self.assertTrue(rows and all(r["content"].startswith(wb.FRAME) for r in rows), p.stdout[-300:])

    def test_the_website_store_refuses_outside(self):
        import website_memory as w
        out = io.StringIO()
        with contextlib.redirect_stdout(out), self.assertRaises(SystemExit), mock.patch.object(w, "_get_conn", lambda: None), \
                mock.patch.object(w, "_encode", side_effect=AssertionError("a model ran")):
            w.cmd_store(argparse.Namespace(content="x", category="insight", summary="s", outside=True, research=None))
        self.assertEqual(wb.last_json(out.getvalue())["code"], "OUTSIDE_REQUIRES_MARK")

    def test_an_unreadable_brain_is_named(self):
        with self.assertRaises(marks.MarksError) as e:
            marks.register(self.tmp / "absent.db", {"model": "m"}, "r")
        self.assertEqual(e.exception.code, "BRAIN_UNREADABLE")

        class Broken:
            def execute(self, *a):
                raise sqlite3.OperationalError("database is locked")
        with self.assertRaises(marks.MarksError) as e:
            marks.store_marks(Broken(), outside=True)
        self.assertEqual(e.exception.code, "BRAIN_UNREADABLE")
        rc, env, _err = wb.full_cli(self.tmp / "absent.db", "forget", "--id", "1")
        self.assertNotEqual(rc, 0)

    def test_a_batch_item_that_spells_false_is_refused_not_read_as_clean(self):
        b = Brain(self.tmp)
        top = b.top()
        rc, env, _err = wb.full_cli(b.path, "batch-store", "--items", json.dumps(
            [dict(content="x words", category="insight", outside="false"), dict(content="y words", category="insight", outside=1)]))
        self.assertEqual([r["status"] for r in data(env)["results"]], ["error", "error"])
        self.assertEqual(b.top(), top)

    def test_a_batch_default_of_outside_is_not_undone_by_an_item(self):
        b = Brain(self.tmp)
        rc, env, _err = wb.full_cli(b.path, "batch-store", "--outside", "--items", json.dumps(
            [dict(content="x words", category="insight", outside=False)]))
        self.assertTrue(b.marked(data(env)["results"][0]["memory_id"]))

    def test_a_run_under_two_configurations_is_refused_and_bad_source_ids_write_nothing(self):
        b = Brain(self.tmp)
        marks.register(b.path, {"model": "m"}, "r")
        with self.assertRaises(marks.MarksError) as e:
            marks.register(b.path, {"model": "other"}, "r")
        self.assertEqual(e.exception.code, "RUN_CONFIG_MISMATCH")
        top = b.top()
        rc, env, _err = wb.full_cli(b.path, "store", "--content", "x", "--category", "insight", "--source-ids", "1,two")
        self.assertEqual((rc != 0, env["code"], b.top()), (True, "SOURCE_IDS_INVALID", top))

    def test_a_derived_write_on_a_brain_before_the_migration_refuses(self):
        import sleep_loop as sl
        b = wb.brain_over(wb.premark_brain(self.tmp / "pre"))
        for ch in "abcd":
            b.add("gradle cache daemon memory pressure kills the build lane variant " + ch * 4)
        top = b.top()
        with wb.engine(b.path), self.assertRaises(marks.MarksError) as e:
            sl.consolidate_episodics(b.c, min_cluster_size=3, similarity_threshold=0.5)
        self.assertEqual((e.exception.code, b.new_ids(top)), ("BRAIN_NEEDS_MIGRATION", []))

    def test_parents_that_cannot_be_recorded_take_the_row_with_them(self):
        import memory_write as mw
        b = Brain(self.tmp)
        p = b.add("parent")
        b.c.execute("CREATE TRIGGER planted BEFORE INSERT ON memory_parents BEGIN SELECT RAISE(ABORT, 'planted'); END")
        b.c.commit()
        top = b.top()
        with self.assertRaises(sqlite3.Error):
            mw.write_memory(b.c, content="derived words", category="insight", encoder=wb.fake_vec, sources=[p], transformation="distillation")
        self.assertEqual(b.new_ids(top), [])

    def test_a_derived_row_and_its_parents_land_together_and_end_no_transaction(self):
        b = Brain(self.tmp)
        p = b.add("parent")
        b.c.execute("CREATE TRIGGER planted BEFORE INSERT ON memory_parents BEGIN SELECT RAISE(ABORT, 'planted'); END")
        b.c.commit()
        top = b.top()
        for level, opened in ((b.c.isolation_level, False), (b.c.isolation_level, True), (None, False)):
            b.c.isolation_level = level
            if opened:
                b.c.execute("UPDATE memories SET access_count = 7 WHERE id = ?", (p,))
            with self.assertRaises(sqlite3.Error):
                marks.derived_insert(b.c, {"content": "x", "category": "insight"}, [p])
            self.assertEqual((b.new_ids(top), b.c.in_transaction), ([], opened), (level, opened))
            b.c.commit()
        self.assertEqual(b.one("SELECT access_count FROM memories WHERE id = ?", (p,))[0], 7)      # the caller's own write stood
        b.c.execute("DROP TRIGGER planted")
        b.c.isolation_level = ""
        b.c.commit()
        marks.derived_insert(b.c, {"content": "x", "category": "insight"}, [p])
        self.assertTrue(b.c.in_transaction)
        b.c.rollback()                                  # the caller can still take the whole thing back
        self.assertEqual(b.new_ids(top), [])

    def test_doctor_names_a_lost_trigger_and_a_brain_before_the_migration(self):
        b = Brain(self.tmp / "b")
        self.assertEqual(marks.health(b.path)["verdict"], "ok")
        b.c.execute("DROP TRIGGER trg_marks_late_outside")
        b.c.commit()
        rc, env, _err = wb.full_cli(b.path, "doctor")
        self.assertEqual(data(env)["checks"]["marks"]["verdict"], "MARK_TRIGGERS_MISSING")
        self.assertEqual(marks.health(wb.premark_brain(self.tmp / "pre"))["verdict"], "BRAIN_NEEDS_MIGRATION")


class DispatchExactness(Case):
    TOPIC = "Zanzibar quokka freezing"

    def sent(self, name, approved, warden=True):
        import gap_mapping as gm
        b = Brain(self.tmp / name)
        b.c.execute("INSERT INTO gap_inventory (entity, gap_type, score, auto_research_eligible) VALUES (?, 'thin_entity', 0.9, 1)", (self.TOPIC,))
        b.c.commit()
        (Path(b.path).parent / gm.APPROVED_TOPICS_FILE).write_text(json.dumps(approved))
        got = []
        with wb.engine(b.path), mock.patch.object(gm, "_dispatch_research", lambda p, t=0: got.append(p) or {
                "status": "success", "output": "A measured rate, doi 10.1/x.", "wall_clock_seconds": 0.1}), \
                contextlib.ExitStack() as st:
            if not warden:
                st.enter_context(mock.patch.object(gm, "_warden", None))
            r = gm.dispatch_auto_research(b.path, gap_ids=[1])
        return len(got), r["status"]

    def test_each_near_spelling_alone_opens_nothing_and_the_exact_one_does(self):
        near = [self.TOPIC + " ", " " + self.TOPIC, self.TOPIC.lower(), self.TOPIC.upper(), self.TOPIC[:-1], self.TOPIC + "\n",
                self.TOPIC.replace(" ", "\u00a0"), "*", "quokka"]
        self.assertEqual({t: self.sent("n%d" % i, [t]) for i, t in enumerate(near)}, {t: (0, "refused") for t in near})
        self.assertEqual(self.sent("yes", [self.TOPIC]), (1, "ok"))
        self.assertEqual(self.sent("yes2", {"topics": [self.TOPIC]}), (1, "ok"))

    def test_with_no_screen_nothing_is_sent(self):
        self.assertEqual(self.sent("nw", [self.TOPIC], warden=False), (0, "refused"))

    def test_a_json_key_given_twice_grants_nothing(self):
        import gap_mapping as gm
        (self.tmp / gm.APPROVED_TOPICS_FILE).write_text('{"topics": [], "topics": ["x"]}')
        self.assertEqual(gm.approved_topics(str(self.tmp / "b.db"))[0], None)


class DiscardedRow(Case):
    """A trigger that ignores an INSERT leaves `lastrowid` naming the row before it."""

    def brain(self):
        b = Brain(self.tmp)
        b.c.execute("CREATE TRIGGER planted BEFORE INSERT ON memories WHEN NEW.content LIKE '%VANISH%' BEGIN SELECT RAISE(IGNORE); END")
        b.c.commit()
        return b

    def test_every_writer_notices_on_a_connection_that_has_written_before(self):
        import claude_memory_lite as lite
        import memory_write as mw
        b = self.brain()
        kw = dict(summary="s", category="insight", kind="episodic", importance=0.5, project=None, context=None, tags="")
        lite._insert_memory(b.c, content="kept", **kw)
        with self.assertRaises(lite.RowNotKept):
            lite._insert_memory(b.c, content="VANISH", **kw)
        with self.assertRaises(sqlite3.Error):
            marks.derived_insert(b.c, {"content": "VANISH", "category": "insight"}, [])
        with self.assertRaises(mw.MemoryWriteError):
            mw.write_memory(b.c, content="VANISH", category="insight", encoder=wb.fake_vec)

    def test_the_lexical_log_keeps_no_words_whatever_it_is_handed(self):
        import recall_diversity
        b = Brain(self.tmp)
        self.assertTrue(recall_diversity.record_lexical_miss(b.c, "a private question", fts_hits=0))
        self.assertRegex(b.one("SELECT query FROM lexical_miss_log")[0], r"^[0-9a-f]{32}$")


class Pin(Case):
    def test_a_worker_of_a_pinned_call_carries_no_pin_and_is_refused(self):
        got = {}

        def worker():
            try:
                got["path"] = engine_pin.db_path("default.db")
            except engine_pin.PinUnset as e:
                got["code"] = e.code
        with engine_pin.pinned(self.tmp / "a.db"):
            t = threading.Thread(target=worker)
            t.start()
            t.join()
            self.assertEqual(engine_pin.db_path("default.db"), self.tmp / "a.db")
        self.assertEqual(got, {"code": "ENGINE_PIN_UNSET"})
        worker()                                        # no call running: the default is the answer
        self.assertEqual(str(got["path"]), "default.db")

    def test_a_worker_given_the_context_answers_for_the_pinned_brain(self):
        import contextvars
        got = []
        with engine_pin.pinned(self.tmp / "a.db"):
            ctx = contextvars.copy_context()
            t = threading.Thread(target=lambda: ctx.run(lambda: got.append(engine_pin.db_path("default.db"))))
            t.start()
            t.join()
        self.assertEqual(got, [self.tmp / "a.db"])

    def test_pins_nest_and_restore_and_output_goes_to_the_innermost_sink(self):
        outer, inner, real = io.StringIO(), io.StringIO(), sys.stdout
        with engine_pin.pinned("a.db", outer):
            print("one")
            with engine_pin.pinned("b.db", inner):
                print("two")
                self.assertEqual(str(engine_pin.db_path("d")), "b.db")
            print("three")
            self.assertEqual(str(engine_pin.db_path("d")), "a.db")
        self.assertIs(sys.stdout, real)
        self.assertIsNone(engine_pin.current())
        self.assertEqual((outer.getvalue(), inner.getvalue()), ("one\nthree\n", "two\n"))

    def test_another_threads_print_is_not_captured(self):
        sink, seen = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(seen), engine_pin.pinned("a.db", sink):
            t = threading.Thread(target=lambda: print("mine"))
            t.start()
            t.join()
        self.assertEqual((sink.getvalue(), seen.getvalue()), ("", "mine\n"))

    def test_the_tier_of_one_brain_is_not_answered_from_another(self):
        a, b = Brain(self.tmp / "a"), Brain(self.tmp / "b")
        b.c.execute("UPDATE metadata SET value = 'memory_embeddings' WHERE key = 'active_embedding_table'")
        b.c.commit()
        import claude_memory as cm
        got = []
        with mock.patch.dict(os.environ), mock.patch.dict(cm._active_model_cache, {}, clear=True):
            os.environ.pop("WIZARD_EMBED_MODEL", None)
            for db in (a.path, b.path, a.path):
                with engine_pin.pinned(db):
                    got.append(cm._active_embed_table())
        self.assertEqual(got[0], got[2])
        self.assertNotEqual(got[0], got[1])

    def test_the_default_path_is_untouched_by_a_refusal_probe(self):
        import claude_memory as cm
        before = cm.DB_PATH
        with contextlib.suppress(Exception):
            cm.query_encode_refusal(self.tmp / "absent.db")
        self.assertEqual(cm.DB_PATH, before)


RANK = '''
import contextlib, io, json, os, sys
sys.path[:0] = [os.environ["TREE"], os.environ["TREE"] + "/organs", os.environ["TREE"] + "/voidloom/tests"]
import _wall_brain as wb
from voidloom import _facade
out = []
for q in ("gradle cache daemon", "memory pressure build lane", "orchid watering", "gradle", "lane cache pressure bark"):
    for flag in ("hybrid", "vector_only", "bm25_only", "no_rerank"):
        ns = _facade._recall_namespace(q, 6)
        setattr(ns, flag, True)
        ns.hybrid = flag == "hybrid"
        ns.leads = 0
        o = io.StringIO()
        with wb.engine(os.environ["DB"]) as cm, contextlib.redirect_stdout(o):
            cm.cmd_recall(ns)
        r = wb.last_json(o.getvalue())
        out.append([q, flag, r["mode"], [[x["id"], x.get("score"), x.get("distance"), x["content"]] for x in r["results"]]])
open(os.environ["OUT"], "w").write(json.dumps(out))
'''


class Ranking(Case):
    def test_order_and_scores_of_an_unmarked_brain_are_the_older_releases(self):
        old = old_tree(self.tmp)
        if old is None:
            self.skipTest("this checkout has no history to take the older release from")
        shutil.copy2(ROOT / "voidloom" / "tests" / "_wall_brain.py", old / "voidloom" / "tests" / "_wall_brain.py")
        b = Brain(self.tmp / "b")
        words = "gradle cache daemon memory pressure kills the build lane orchid watering bark violin rosin".split()
        for i in range(60):
            b.add(" ".join(words[(i * j) % len(words)] for j in range(1, 6)) + " row %d" % i, importance=0.2 + (i % 7) / 10.0,
                  kind=("semantic", "procedural", "episodic")[i % 3], days_ago=i % 11, project="p%d" % (i % 4))
        b.c.close()
        got = {}
        for name, tree in (("new", ROOT), ("old", old)):
            db = self.tmp / ("db_" + name) / "claude_memory.db"
            db.parent.mkdir()
            shutil.copy2(b.path, db)
            out = self.tmp / (name + ".json")
            env = wb.child_env(db, TREE=str(tree), DB=str(db), OUT=str(out), VOIDLOOM_TIER0_ONLY="0")
            env["PYTHONPATH"] = str(tree)
            p = subprocess.run([sys.executable, "-B", "-c", RANK], env=env, cwd=str(self.tmp), capture_output=True, text=True, timeout=240)
            self.assertEqual(p.returncode, 0, p.stderr[-600:])
            got[name] = json.loads(out.read_text())
        self.assertTrue(any(len(r[3]) >= 5 for r in got["old"]), "the fixture ranks nothing, so it proves nothing")
        self.assertEqual(json.dumps(got["new"]), json.dumps(got["old"]))


if __name__ == "__main__":
    unittest.main()
