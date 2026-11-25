# -*- coding: utf-8 -*-
"""R1 / R10 / R11 / R12: the marks are in the INSERT, named by the caller, read back after commit; never inferred."""
import argparse
import hashlib
import json
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _wall_brain as wb
from _wall_brain import Brain, data, unknown_flag

TEXT = "Glacier moraine notes copied from a page"
STORE = ("store", "--content", TEXT, "--category", "insight")


def sha(t):
    return hashlib.sha256(t.encode("utf-8")).hexdigest()


def ns(**k):
    d = dict(content=TEXT, category="insight", tags="", importance=0.5, project=None, context=None, summary=None,
             kind="episodic", auto_edges=False, source_user=None, answers=None)
    d.update(k)
    return argparse.Namespace(**d)


_Both = wb.BothEngines


class Control(_Both):
    def test_control_plain_store_is_unmarked_and_ok(self):
        def go(b, cli):
            rc, env, err = cli(b.path, *STORE)
            self.assertEqual((rc, env["status"]), (0, "ok"), err)
            self.assertFalse(b.marked(data(env)["memory_id"]))
        self.both(go)

    def test_control_text_never_infers_a_mark(self):
        def go(b, cli):
            rc, env, err = cli(b.path, "store", "--content", "[external / unverified source] http://x.example web research outside",
                               "--category", "insight", "--tags", "web,outside,research")
            self.assertEqual(rc, 0, err)
            self.assertFalse(b.marked(data(env)["memory_id"]))
        self.both(go)

    def test_control_plain_raw_insert_is_never_blocked_and_stays_clean(self):
        b = Brain(self.tmp)
        mid = b.add("a raw row by a script")
        self.assertFalse(b.marked(mid))
        b.c.execute("UPDATE memories SET content='edited' WHERE id=?", (mid,))
        self.assertFalse(b.marked(mid))


class Outside(_Both):
    def test_red_cli_store_outside_marks_the_row(self):
        def go(b, cli):
            rc, env, err = cli(b.path, *STORE, "--outside")
            self.needs(not unknown_flag(err), "store --outside")
            self.assertEqual(rc, 0, err)
            mid = data(env)["memory_id"]
            self.assertTrue(b.marked(mid), "stored outside text as a trusted row")
            self.assertLessEqual(b.one("SELECT confidence FROM memories WHERE id=?", (mid,))[0], 0.50)
            self.assertEqual(b.one("SELECT derived_from FROM memories WHERE id=?", (mid,))[0], "untrusted_web_source")
        self.both(go)

    def test_red_engine_namespace_outside_is_not_dropped(self):
        """Both engines' cmd_store: an `outside` the caller passed may not be silently ignored."""
        import contextlib, io
        from unittest import mock
        got = {}
        for name in ("claude_memory", "claude_memory_lite"):
            b = Brain(self.tmp / name)
            mod = __import__(name)
            with wb.engine(b.path), mock.patch.object(mod, "DB_PATH", Path(b.path)), \
                    contextlib.redirect_stdout(io.StringIO()):
                mod.cmd_store(ns(outside=True, research=None, author=None, session_id=None, parent_ref=None, source_ids=None))
            got[name] = b.marked(b.top())
        self.assertEqual(got, {"claude_memory": True, "claude_memory_lite": True}, "an outside row was stored unmarked")

    def test_red_novelty_never_lifts_a_marked_row(self):
        import contextlib, io
        b = Brain(self.tmp)
        with wb.engine(b.path) as cm, contextlib.redirect_stdout(io.StringIO()):
            b.add("unrelated neighbour about violin rosin")
            cm.cmd_store(ns(outside=True, importance=0.3))
        self.assertEqual((b.marked(b.top()), b.importance(b.top())), (True, 0.3))

    def test_red_batch_item_outside(self):
        def go(b, cli):
            items = json.dumps([dict(content=TEXT + " one", category="insight", outside=True),
                                dict(content="a clean twin row here", category="insight")])
            rc, env, err = cli(b.path, "batch-store", "--items", items)
            rs = data(env).get("results") or []
            self.assertEqual([r["status"] for r in rs], ["ok", "ok"], "INTERFACE ABSENT (contract not built): batch item key `outside`: %s" % rs)
            self.assertEqual([b.marked(r["memory_id"]) for r in rs], [True, False])
        self.both(go)

    def test_red_batch_cli_default_outside(self):
        def go(b, cli):
            rc, env, err = cli(b.path, "batch-store", "--items", json.dumps([dict(content=TEXT, category="insight")]), "--outside")
            self.needs(not unknown_flag(err), "batch-store --outside")
            self.assertTrue(b.marked(data(env)["results"][0]["memory_id"]))
        self.both(go)

    def test_red_facade_outside(self):
        import inspect
        from voidloom import _facade
        b = Brain(self.tmp)
        self.needs("outside" in inspect.signature(_facade.store).parameters, "_facade.store(outside=)")
        with wb.engine(b.path):
            r = _facade.store(TEXT, "insight", db_path=b.path, outside=True)
        self.assertTrue(b.marked(r["memory_id"]))

    @unittest.skipUnless(hasattr(os, "fork"), "fork")
    def test_red_killed_mid_write_leaves_no_unmarked_outside_row(self):
        """Die before the k-th SQL statement of an outside store, for every k: no surviving row is unmarked."""
        survived, absent, unmarked, k = 0, 0, [], 0
        while k < 400:
            k += 1
            b = Brain(self.tmp / ("k%d" % k))
            b.c.close()
            pid = os.fork()
            if pid == 0:
                try:
                    os.dup2(os.open(os.devnull, os.O_WRONLY), 1)
                    with wb.engine(b.path) as cm:
                        real, n = cm._get_conn, [0]

                        def tick(_sql):
                            n[0] += 1
                            if n[0] == k:
                                os._exit(9)

                        def get():
                            c = real()
                            c.set_trace_callback(tick)
                            return c
                        cm._get_conn = get
                        cm.cmd_store(ns(outside=True))
                    os._exit(0)
                except BaseException:
                    os._exit(7)
            rc = os.waitstatus_to_exitcode(os.waitpid(pid, 0)[1])
            self.assertIn(rc, (0, 9), "the child failed for another reason")
            c = wb.connect(b.path)
            rows = c.execute("SELECT untrusted_source FROM memories WHERE content=?", (TEXT,)).fetchall()
            c.close()
            import shutil
            shutil.rmtree(str(self.tmp / ("k%d" % k)))
            survived, absent = survived + bool(rows), absent + (not rows)
            if rows and rows[0][0] != 1:
                unmarked.append(k)
            if rc == 0:
                break
        self.assertTrue(survived and absent, "the sweep never saw both outcomes: it proves nothing")
        self.assertEqual(unmarked, [], "killed before statement k, an outside row survived UNMARKED")


class Provenance(_Both):
    def test_red_author_session_parent_recorded(self):
        def go(b, cli):
            rc, env, err = cli(b.path, *STORE, "--author", "tool", "--session", "s-1", "--parent", "msg-9")
            self.needs(not unknown_flag(err), "store --author/--session/--parent")
            self.assertEqual(b.one("SELECT author, session_id, parent_ref FROM memories WHERE id=?", (data(env)["memory_id"],)),
                             ("tool", "s-1", "msg-9"))
        self.both(go)

    def test_red_unknown_author_is_refused(self):
        def go(b, cli):
            top = b.top()
            rc, env, err = cli(b.path, *STORE, "--author", "owner")
            self.needs("unrecognized" not in (err or ""), "store --author")
            self.assertNotEqual(rc, 0)
            self.assertEqual(b.top(), top)
        self.both(go)

    def test_red_source_ids_inherit_any_marked_source(self):
        def go(b, cli):
            clean, marked = b.add("clean parent row"), b.add("outside parent row", marked=True)
            got = {}
            for name, ids in (("marked", "%d,%d" % (clean, marked)), ("clean", "%d" % clean), ("missing", "999999")):
                rc, env, err = cli(b.path, "store", "--content", "child via " + name, "--category", "insight", "--source-ids", ids)
                self.needs(not unknown_flag(err), "store --source-ids")
                got[name] = b.marked(data(env).get("memory_id"))
            self.assertEqual(got, {"marked": True, "clean": False, "missing": True})
        self.both(go)

    def test_red_writer_refuses_to_drop_a_requested_mark(self):
        """memory_write drops a column the schema lacks with a warning: for a mark that is a laundering path."""
        import memory_write as mw
        b = wb.brain_over(wb.premark_brain(self.tmp / "pre", drop_mark=True))
        top = b.top()
        try:
            mw.write_memory(b.c, content="outside text", category="insight", encoder=wb.fake_vec, extra={"untrusted_source": 1})
        except Exception as e:
            self.assertEqual(getattr(e, "code", "") or str(e)[:21], "BRAIN_NEEDS_MIGRATION")
            return
        self.assertEqual(b.new_ids(top), [], "the mark was dropped and the row written unmarked")


class Research(_Both):
    def register(self, b, cli, spec=None, run="run-1"):
        f = Path(b.path).parent / "cfg.json"
        f.write_text(json.dumps(spec or {"model": "m", "prompt": "p1", "gate": "g1", "checker": "c1"}))
        rc, env, err = cli(b.path, "research-register", "--config", str(f), "--run", run)
        self.needs(not unknown_flag(err), "research-register --config FILE --run RUN_ID")
        self.assertEqual(rc, 0, env)
        return b.one("SELECT config_id FROM research_runs WHERE run_id=?", (run,))[0]

    def test_red_unregistered_run_refuses_and_writes_nothing(self):
        def go(b, cli):
            top = b.top()
            rc, env, err = cli(b.path, *STORE, "--research", "run-none")
            self.needs(not unknown_flag(err), "store --research")
            self.assertEqual((rc != 0, (env or {}).get("code"), b.top()), (True, "RESEARCH_NEEDS_RUN", top))
        self.both(go)

    def test_red_research_implies_outside_and_names_its_configuration(self):
        def go(b, cli):
            cid = self.register(b, cli)
            rc, env, err = cli(b.path, *STORE, "--research", "run-1")
            mid = data(env)["memory_id"]
            self.assertEqual(b.one("SELECT untrusted_source, research_mark, research_run_id, research_config_id, research_weight "
                                   "FROM memories WHERE id=?", (mid,)), (1, 1, "run-1", cid, None))
            self.assertEqual(b.all("SELECT run_id, config_id FROM memory_research_runs WHERE memory_id=?", (mid,)), [("run-1", cid)])
            m = data(env)["marks"]
            self.assertEqual((m["outside"], m["research"], m["research_run_id"], m["research_config_id"]), (True, True, "run-1", cid))
        self.both(go)

    def test_red_registrar_is_insert_only_and_idempotent(self):
        def go(b, cli):
            a = self.register(b, cli, {"model": "m", "prompt": "p"}, "r1")
            again = self.register(b, cli, {"prompt": "p", "model": "m"}, "r2")          # same object, another key order
            self.assertEqual(a, again)
            self.assertRegex(a, r"^[0-9a-f]{64}$")
            for sql in ("UPDATE research_configs SET spec_json='{}'", "DELETE FROM research_configs",
                        "UPDATE research_runs SET config_id='x'", "DELETE FROM research_runs"):
                with self.assertRaises(Exception, msg=sql):
                    b.c.execute(sql)
                b.c.rollback()
        self.both(go)

    def test_red_run_and_config_are_write_once(self):
        def go(b, cli):
            self.register(b, cli)
            rc, env, err = cli(b.path, *STORE, "--research", "run-1")
            mid = data(env)["memory_id"]
            for col, v in (("research_run_id", "other"), ("research_run_id", None), ("research_config_id", "x"),
                           ("research_mark", 0), ("untrusted_source", 0)):
                with self.assertRaises(Exception, msg=col):
                    b.c.execute("UPDATE memories SET %s=? WHERE id=?" % col, (v, mid))
                b.c.rollback()
        self.both(go)


# The INSERT is silently skipped (RAISE(IGNORE) leaves a STALE lastrowid: a read-back by id alone finds somebody else's
# row), or the text is replaced after the FTS index took it (a memory_tags trigger; a trigger on memories itself would
# race the FTS triggers and corrupt the fixture instead of testing the verb).
VANISH = ("CREATE TRIGGER planted_vanish BEFORE INSERT ON memories WHEN NEW.content LIKE '%VANISH%' "
          "BEGIN SELECT RAISE(IGNORE); END")
ALTER = ("CREATE TRIGGER planted_alter AFTER INSERT ON memory_tags WHEN NEW.tag = 'planted' "
         "BEGIN UPDATE memories SET content = 'something else entirely' WHERE id = NEW.memory_id; END")


class Readback(_Both):
    def test_red_success_envelope_is_read_back(self):
        def go(b, cli):
            rc, env, err = cli(b.path, "store", "--content", "Ünïcode stored text — exactly", "--category", "insight")
            self.assertEqual(data(env).get("stored_sha256"), sha("Ünïcode stored text — exactly"))
            self.assertEqual((data(env).get("marks") or {}).get("outside"), False)
            rc, env, err = cli(b.path, "batch-store", "--items", json.dumps([dict(content="b one text", category="insight")]))
            r = data(env)["results"][0]
            self.assertEqual(r.get("stored_sha256") or r["result"].get("stored_sha256"), sha("b one text"))
        self.both(go)

    def test_red_a_store_that_did_not_land_is_never_ok(self):
        for why, trg in (("the row vanished", VANISH), ("the row holds other text", ALTER)):
            def go(b, cli):
                b.add("a row that was already here")
                b.c.execute(trg)
                b.c.commit()
                rc, env, err = cli(b.path, "store", "--content", "VANISH this text" if trg is VANISH else "this text",
                                   "--category", "insight", *(() if trg is VANISH else ("--tags", "planted")))
                self.assertNotEqual(rc, 0, "%s and the verb exited 0 with status %s" % (why, env["status"]))
                self.assertNotEqual(env["status"], "ok")
                self.assertEqual(env["code"], "STORE_READBACK_FAILED")
                self.assertTrue(data(env).get("row_may_exist"))
                self.assertIn("memory_id", data(env))
            self.both(go, sub=why.replace(" ", "_"))

    def test_red_batch_names_the_item_that_did_not_land(self):
        def go(b, cli):
            b.add("a row that was already here")
            b.c.execute(VANISH)
            b.c.commit()
            rc, env, err = cli(b.path, "batch-store", "--items", json.dumps(
                [dict(content="good text one", category="insight"), dict(content="VANISH two", category="insight"),
                 dict(content="good text three", category="insight")]))
            self.assertEqual([r["status"] for r in data(env)["results"]], ["ok", "error", "ok"])
            self.assertNotEqual(rc, 0)
            self.assertEqual(b.one("SELECT COUNT(*) FROM memories WHERE content LIKE 'good text%'")[0], 2)
        self.both(go)


class Premark(_Both):
    def test_red_marked_store_refuses_and_plain_store_proceeds_flagged(self):
        def go(b, cli):
            rc, env, err = cli(b.path, *STORE)
            self.assertEqual(rc, 0, err)                                   # the control half: green on both trees
            self.assertIs(data(env).get("premark"), True, "a pre-046 brain's plain store does not say premark")
            top = b.top()
            rc, env, err = cli(b.path, *STORE, "--outside")
            self.assertEqual((rc != 0, (env or {}).get("code"), b.top()), (True, "BRAIN_NEEDS_MIGRATION", top))
        self.both(go, make=lambda d: wb.brain_over(wb.premark_brain(d)))


if __name__ == "__main__":
    unittest.main()
