# -*- coding: utf-8 -*-
"""What a caller is told: a value the engine changed, a mark it could not know, an input it could not take, a flag that used
to mean something else.

WHAT WOULD BREAK WITHOUT IT: the owner fixes a typo in a note worth 0.9, gets "ok", and the note is worth 0.2.
"""
import argparse
import contextlib
import io
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _wall_brain as wb
from _wall_brain import Brain, Case, ROOT, data

sys.path.insert(0, str(ROOT / "organs"))
import marks

STORE = ("store", "--content", "gradle daemon note", "--category", "insight")


class Update(Case):
    def update(self, b, mid, **ns):
        out = io.StringIO()
        args = dict(content=None, summary=None, importance=None, category=None, kind=None, tags=None, **{})
        args.update(ns)
        with wb.engine(b.path) as cm, contextlib.redirect_stdout(out):
            cm.cmd_update(argparse.Namespace(id=mid, **args))
        return wb.last_json(out.getvalue())

    def worth(self, b, mid):
        return b.one("SELECT importance, confidence FROM memories WHERE id = ?", (mid,))

    def derived(self, b, *, author=None, marked=False):
        parents = [b.add("parent one", importance=0.2, marked=marked), b.add("parent two", importance=0.2)]
        mid = b.add("the row", importance=0.9, confidence=0.5 if marked else 1.0, **({"author": author} if author else {}))
        b.c.executemany("INSERT INTO memory_parents (child_id, parent_id) VALUES (?, ?)", [(mid, p) for p in parents])
        b.c.executemany("INSERT INTO consolidation_sources (consolidated_memory_id, source_memory_id, cluster_id) VALUES (?, ?, 1)",
                        [(mid, p) for p in parents])
        b.c.commit()
        return mid

    def test_an_unmarked_row_keeps_what_its_owner_says(self):
        for n, author in enumerate((None, "user", "model")):
            b = Brain(self.tmp / str(n))
            mid = self.derived(b, author=author)
            out = self.update(b, mid, content="the row, typo fixed")
            self.assertEqual(self.worth(b, mid), (0.9, 1.0), author)
            self.assertNotIn("importance_capped", out)
            self.update(b, mid, content="rewritten", importance=0.95)
            self.assertEqual(self.worth(b, mid)[0], 0.95, author)
            self.update(b, mid, importance=0.97, kind="semantic")
            self.assertEqual(self.worth(b, mid), (0.97, 1.0), author)

    def test_a_marked_row_is_capped_and_the_answer_says_so(self):
        b = Brain(self.tmp)
        mid = self.derived(b, marked=True)
        out = self.update(b, mid, content="rewritten")
        self.assertEqual(out["importance_capped"], {"asked": 0.9, "stored": 0.2})
        self.assertEqual(self.worth(b, mid)[0], 0.2)
        out = self.update(b, mid, importance=0.8, kind="semantic")
        self.assertEqual((out["importance_capped"]["stored"], out["confidence_capped"]), (0.2, {"asked": 1.0, "stored": 0.5}))
        self.assertEqual(self.worth(b, mid), (0.2, 0.5))
        self.assertNotIn("importance_capped", self.update(b, mid, importance=0.1))

    def test_a_premark_brain_updates_as_it_did(self):
        b = wb.brain_over(wb.premark_brain(self.tmp / "pre"))
        mid = b.add("the row", importance=0.9)
        self.update(b, mid, content="fixed")
        self.assertEqual(self.worth(b, mid), (0.9, 1.0))


class Premark(Case):
    def test_a_premark_store_says_nothing_about_the_mark(self):
        for n, drop in enumerate((False, True)):
            b = wb.brain_over(wb.premark_brain(self.tmp / str(n), drop_mark=drop))
            for cli in (lambda *a: wb.full_cli(b.path, *a)[:2], lambda *a: wb.lite_cli(b.path, *a)[:2]):
                rc, env = cli(*STORE)
                self.assertEqual(rc, 0, env)
                self.assertIs(data(env)["premark"], True)
                self.assertIsNone(data(env)["marks"]["outside"], drop)
                self.assertIsNone(data(env)["marks"]["research"], drop)

    def test_a_ready_store_says_which(self):
        b = Brain(self.tmp)
        _rc, env, _err = wb.full_cli(b.path, *STORE)
        self.assertIs(data(env)["marks"]["outside"], False)


class Inputs(Case):
    def test_an_input_that_cannot_be_stored_is_a_typed_refusal_and_writes_nothing(self):
        b = Brain(self.tmp)
        top = b.top()
        for flag, value, code in (("--source-ids", "9223372036854775808", "SOURCE_IDS_INVALID"),
                                  ("--source-ids", "9" * 5000, "SOURCE_IDS_INVALID"),
                                  ("--source-ids", "²", "SOURCE_IDS_INVALID"),
                                  ("--source-ids", "[" * 100000, "SOURCE_IDS_INVALID"),
                                  ("--source-ids", "[1e400]", "SOURCE_IDS_INVALID"),
                                  ("--session", "a\ud800b", "MARK_INVALID"),
                                  ("--parent", "a\ud800b", "MARK_INVALID"),
                                  ("--research", "a\ud800b", "RESEARCH_NEEDS_RUN")):
            rc, env, _err = wb.full_cli(b.path, *STORE, flag, value)
            self.assertEqual((rc != 0, (env or {}).get("code")), (True, code), (flag, value[:20], env))
        self.assertEqual(b.top(), top)

    def test_a_configuration_that_cannot_be_stored(self):
        b = Brain(self.tmp)
        deep = cur = {}
        for _ in range(100000):
            cur["a"] = {}
            cur = cur["a"]
        try:
            for spec in ({"k": "a\ud800b"}, {"\udfff": 1}, deep, {"k": float("nan")}):
                with self.assertRaises(marks.MarksError) as e:
                    marks.register(b.path, spec, "run1")
                self.assertEqual(e.exception.code, "CONFIG_INVALID")
        finally:
            while deep:                                 # taken apart by hand: freeing it whole would recurse as deep
                deep = deep.pop("a")
        with self.assertRaises(marks.MarksError) as e:
            marks.register(b.path, {"k": 1}, "run\ud800")
        self.assertEqual(e.exception.code, "RUN_INVALID")
        cfg = self.tmp / "cfg.json"
        cfg.write_text('{"k": "\\ud800"}', encoding="utf-8")
        rc, env, _err = wb.full_cli(b.path, "research-register", "--config", str(cfg), "--run", "r")
        self.assertEqual((rc != 0, env["code"]), (True, "CONFIG_INVALID"))
        cfg.write_text("[" * 200000, encoding="utf-8")
        rc, env, _err = wb.full_cli(b.path, "research-register", "--config", str(cfg), "--run", "r")
        self.assertEqual((rc != 0, env["code"]), (True, "CONFIG_INVALID"))

    def test_a_target_that_is_not_one(self):
        b = Brain(self.tmp)
        for argv in (("forget", "--id", "9" * 30), ("forget", "--run", "a\ud800b"), ("unarchive", "--id", "9" * 30)):
            rc, env, _err = wb.full_cli(b.path, *argv)
            self.assertNotEqual(rc, 0, argv)
            self.assertIn(env["code"], ("TARGET_INVALID", "MEMORY_NOT_FOUND", "RUN_UNKNOWN"), argv)


class Batch(Case):
    def test_source_ids_on_the_command_line_are_every_items_unless_it_names_its_own(self):
        b = Brain(self.tmp)
        web, mine = b.add("web", marked=True), b.add("mine")
        items = [dict(content="takes the default", category="insight"),
                 dict(content="names its own", category="insight", source_ids=[mine]),
                 dict(content="says it has none", category="insight", source_ids=[])]
        for cli in (lambda *a: wb.full_cli(b.path, *a)[:2], lambda *a: wb.lite_cli(b.path, *a)[:2]):
            rc, env = cli("batch-store", "--items", json.dumps(items), "--source-ids", str(web))
            self.assertEqual(rc, 0, env)
            ids = [r["memory_id"] for r in data(env)["results"]]
            self.assertEqual([b.marked(i) for i in ids], [True, False, False])
            self.assertEqual([b.all("SELECT parent_id FROM memory_parents WHERE child_id = ?", (i,)) for i in ids], [[(web,)], [(mine,)], []])
            rc, env = cli("batch-store", "--items", json.dumps(items[:1]), "--source-ids", "x")
            self.assertNotEqual(rc, 0)


class Capabilities(Case):
    def test_the_features_are_listed_with_no_organ_registry(self):
        b = Brain(self.tmp)
        (self.tmp / "voidloom_manifest.json").write_text('{"version": "x"}', encoding="utf-8")
        rc, env, _err = wb.full_cli(b.path, "capabilities")
        self.assertEqual((rc, env["status"], env["code"]), (0, "ok", "no_organs"))
        from voidloom import _agent_context
        for held in (env, data(env)):
            self.assertEqual(held["features"], _agent_context.FEATURES)
        rc, env, _err = wb.full_cli(b.path, "agent-context")
        self.assertEqual((rc, data(env)["features"]), (0, _agent_context.FEATURES))


class Doctor(Case):
    def test_a_lost_trigger_is_promised_back_by_a_store_alone(self):
        b = Brain(self.tmp)
        b.c.execute("DROP TRIGGER trg_marks_late_outside")
        b.c.commit()
        _rc, env, _err = wb.full_cli(b.path, "doctor")
        text = json.dumps(env)
        self.assertIn("the next store puts them back", text)
        self.assertNotIn("store or recall", text)


class Shortened(Case):
    """A flag shortened to what named it before still names it."""

    def test_the_engines_store(self):
        b = Brain(self.tmp)
        out = io.StringIO()
        env = wb.child_env(b.path)
        for argv, col, want in ((("--source", "me"), "source_user", "me"), (("--so", "me"), "source_user", "me"),
                                (("--p", "proj9"), "source_project", "proj9")):
            got = self.parse("claude_memory", "store", "--content", "x", "--category", "insight", *argv)
            self.assertEqual(getattr(got, {"source_project": "project"}.get(col, col)), want, argv)
        self.assertTrue(self.parse("claude_memory", "store", "--content", "x", "--category", "insight", "--aut").auto_edges)
        self.assertTrue(self.parse("claude_memory", "recall", "--query", "x", "--no-lo").no_log_usage)
        got = self.parse("claude_memory", "store", "--content", "x", "--category", "insight")
        self.assertEqual((got.source_user, got.project, got.auto_edges), (None, None, False))

    def test_the_lite_engines_store(self):
        got = self.parse("claude_memory_lite", "store", "--content", "x", "--category", "insight", "--p", "proj9", "--s", "sum")
        self.assertEqual((got.project, got.summary), ("proj9", "sum"))
        self.assertEqual(self.parse("claude_memory_lite", "store", "--content", "x", "--category", "insight").project, "default")

    def test_the_packages_verbs(self):
        self.assertEqual(self.parse("cli", "store", "--content", "x", "--category", "insight", "--a", "7").answers, "7")
        got = self.parse("cli", "batch-recall", "--items", "[]", "--no")
        self.assertEqual((got.no_rerank, got.no_log), (True, False))

    def parse(self, module, *argv):
        """What the module's own parser makes of argv."""
        code = ("import argparse, json, sys\n"
                "class Stop(BaseException): pass\n"
                "real = argparse.ArgumentParser.parse_known_args\n"
                "def stop(self, args=None, namespace=None):\n"
                "    ns, extra = real(self, args, namespace)\n"
                "    assert not extra, extra\n"
                "    print('NS' + json.dumps({k: v for k, v in vars(ns).items() if isinstance(v, (str, int, bool, type(None)))}))\n"
                "    raise Stop()\n"
                "argparse.ArgumentParser.parse_args = stop\n"
                "argparse.ArgumentParser.parse_known_args = lambda self, args=None, namespace=None: "
                "real(self, args, namespace) if ' ' in self.prog else stop(self, args, namespace)\n"
                f"sys.path[:0] = [{str(ROOT)!r}, {str(ROOT / 'organs')!r}]\n"
                "sys.argv = ['x'] + json.loads(sys.stdin.read())\n"
                "try:\n"
                + ("    from voidloom import cli; cli.main(sys.argv[1:])\n" if module == "cli" else
                   f"    import {module} as m; m.main()\n") +
                "except Stop: pass\n")
        p = subprocess.run([sys.executable, "-B", "-c", code], input=json.dumps(list(argv)), text=True, capture_output=True,
                           cwd=str(self.tmp), env={k: v for k, v in wb.child_env(str(self.tmp / "x.db")).items() if k != "WIZARD_LIBRARY_DB"}, timeout=120)
        lines = [l for l in p.stdout.splitlines() if l.startswith("NS")]
        self.assertTrue(lines, (argv, p.stdout[-300:], p.stderr[-300:]))
        return argparse.Namespace(**json.loads(lines[-1][2:]))


class LoadedByPath(Case):
    def test_an_organ_loads_by_its_file_path(self):
        for name in ("trust_tier", "memory_write"):
            code = ("import importlib.util, sys\n"
                    f"s = importlib.util.spec_from_file_location('m', {str(ROOT / 'organs' / (name + '.py'))!r})\n"
                    "m = importlib.util.module_from_spec(s); sys.modules['m'] = m; s.loader.exec_module(m)\n")
            env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
            p = subprocess.run([sys.executable, "-B", "-c", code], text=True, capture_output=True, cwd=str(self.tmp), env=env, timeout=120)
            self.assertEqual(p.returncode, 0, p.stderr[-400:])


class ReadOnly(Case):
    def test_a_dry_run_only_reads(self):
        b = Brain(self.tmp / "d")
        mid = b.add("row")
        b.c.execute("PRAGMA journal_mode=WAL")
        b.c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        b.c.close()
        for side in ("-wal", "-shm"):
            Path(b.path + side).unlink(missing_ok=True)
        wb.full_cli(b.path, "forget", "--id", str(mid), "--dry-run")         # whatever the verb makes beside the brain, made
        for side in ("-wal", "-shm"):
            Path(b.path + side).unlink(missing_ok=True)
        before = Path(b.path).read_bytes()
        os.chmod(b.path, 0o444)
        os.chmod(self.tmp / "d", 0o555)
        try:
            for argv in (("forget", "--id", str(mid), "--dry-run"), ("marks", "backfill", "--dry-run")):
                rc, env, err = wb.full_cli(b.path, *argv)
                self.assertEqual((rc, env["status"]), (0, "ok"), (argv, env, err[-300:]))
            for argv in (("forget", "--id", str(mid)), ("marks", "backfill"), ("marks", "backfill", "--undo")):
                rc, env, _err = wb.full_cli(b.path, *argv)
                self.assertEqual((rc, env["code"]), (3, "readonly_db"), argv)
        finally:
            os.chmod(self.tmp / "d", 0o755)
            os.chmod(b.path, 0o644)
        self.assertEqual(Path(b.path).read_bytes(), before)


class Enacted(Case):
    def test_an_enacted_proposal_says_a_model_wrote_it(self):
        import decision_units
        import monthly_self_eval as mse
        b = Brain(self.tmp)
        what = [{"title": "A thing", "rationale": "r", "evidence": "e", "priority": "low", "piece_affected": "x"}]
        rid = b.c.execute("INSERT INTO self_eval_reports (month, health_scorecard, proposed_enhancements, proposed_deprecations, "
                          "threshold_assessment, persona_evolution, status) VALUES ('2026-01', '{}', ?, '[]', '{}', '{}', 'pending')",
                          (json.dumps(what),)).lastrowid
        decision_units.create_units_for_report(b.c, rid, what, [])
        b.c.execute("INSERT INTO self_eval_enacted_proposals (report_id, proposal_title, proposal_json, status) VALUES (?, 'A thing', ?, 'pending')",
                    (rid, json.dumps(what[0])))
        b.c.commit()
        out = mse.enact_approved(b.path, rid, confirm=True)
        row = b.one("SELECT author, importance FROM memories WHERE content LIKE 'APPROVED ENHANCEMENT: A thing%'")
        self.assertEqual(row, ("model", 0.8), out)


if __name__ == "__main__":
    unittest.main()
