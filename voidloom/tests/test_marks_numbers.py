# -*- coding: utf-8 -*-
"""A stored importance is a number from 0 to 1, and a recall over an old brain that cannot be changed answers.

WHAT WOULD BREAK WITHOUT IT: `store(importance=-1.0)` stores -0.99, NaN stores 1.0, the lite engine stores 5.0, and every
ranking that reads importance is bent by one row. A recall of a brain on read-only media that predates a column raises out
of the facade instead of answering.
"""
import argparse
import contextlib
import io
import json
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _wall_brain as wb
from _wall_brain import Brain, Case, data

BAD = ("-1.0", "1.0001", "5.0", "nan", "inf", "-inf")


class Importance(Case):
    def test_store_refuses_a_number_outside_0_to_1_on_every_way_in(self):
        b = Brain(self.tmp)
        top = b.top()
        for value in BAD:
            for lite in (False, True):
                rc, env, _err = wb.full_cli(b.path, "store", "--content", "gradle note", "--category", "insight",
                                            f"--importance={value}", env={"VOIDLOOM_TIER0_ONLY": "1"} if lite else None)
                # the package's own verb has refused it under this code since the release before this one
                self.assertEqual((rc != 0, env["code"]), (True, "invalid_importance"), (value, lite, env))
            rc, env, _err = wb.lite_cli(b.path, "store", "--content", "gradle note", "--category", "insight",
                                        f"--importance={value}")[:3]
            self.assertNotEqual(rc, 0, value)
            out = io.StringIO()
            with wb.engine(b.path) as cm, contextlib.redirect_stdout(out):
                from voidloom import _facade
                got = _facade.store("gradle note", "insight", importance=float(value), db_path=b.path)
            self.assertEqual((got["status"], got.get("code") or got.get("error")), ("error", "IMPORTANCE_OUT_OF_RANGE"), value)
        p = __import__("subprocess").run([sys.executable, "-B", str(wb.ROOT / "organs" / "claude_memory_lite.py"), "store",
                                          "--content", "gradle note", "--category", "insight", "--importance", "5.0"],
                                         env=wb.child_env(b.path), capture_output=True, text=True, timeout=120)
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("IMPORTANCE_OUT_OF_RANGE", p.stdout)
        self.assertEqual(b.top(), top, "something was written")
        for value in ("0", "1", "0.5"):                       # the edges and the middle are numbers from 0 to 1
            rc, env, _err = wb.full_cli(b.path, "store", "--content", f"gradle note {value}", "--category", "insight",
                                        "--importance", value)
            self.assertEqual(rc, 0, env)

    def test_update_refuses_it_too(self):
        b = Brain(self.tmp)
        mid = b.add("a note", importance=0.4)
        for value in (-1.0, 2.0, float("nan")):
            out = io.StringIO()
            with wb.engine(b.path) as cm, contextlib.redirect_stdout(out), self.assertRaises(SystemExit) as e:
                cm.cmd_update(argparse.Namespace(id=mid, content=None, summary=None, importance=value, category=None,
                                                 kind=None, tags=None))
            self.assertNotEqual(e.exception.code, 0)
            self.assertEqual(wb.last_json(out.getvalue())["code"], "IMPORTANCE_OUT_OF_RANGE")
        self.assertEqual(b.importance(mid), 0.4)

    def test_batch_says_the_same_code(self):
        b = Brain(self.tmp)
        items = [dict(content="ok item", category="insight", importance=0.5)] + \
                [dict(content=f"bad {v}", category="insight", importance=v) for v in (-1.0, 5.0, "nan", True)]
        rc, env, _err = wb.full_cli(b.path, "batch-store", "--items", json.dumps(items))
        results = data(env)["results"]
        self.assertEqual([r["status"] for r in results], ["ok"] + ["error"] * 4)
        self.assertEqual({r.get("code") for r in results[1:]}, {"IMPORTANCE_OUT_OF_RANGE"})
        self.assertNotEqual(rc, 0)


class OldReadOnlyBrain(Case):
    def test_a_recall_answers_instead_of_raising(self):
        b = Brain(self.tmp)
        b.add("gradle cache daemon note")
        b.c.execute("ALTER TABLE memories DROP COLUMN confidence")
        b.c.commit()
        b.c.close()
        os.chmod(b.path, 0o444)
        try:
            with wb.engine(b.path):
                from voidloom import _facade
                got = _facade.recall("gradle cache", top_k=3, db_path=b.path)
            self.assertEqual(got["status"], "ok", got)               # it is read as it is: no confidence to show
            self.assertTrue(got["results"])
            self.assertIsNone(got["results"][0].get("confidence"))
            rc, env, err = wb.full_cli(b.path, "recall", "--query", "gradle cache")
            self.assertIsNotNone(env, err[-600:])
            self.assertIn(env["status"], ("ok", "degraded", "error"))
        finally:
            os.chmod(b.path, 0o644)

    def test_a_database_error_is_an_answer(self):
        b = Brain(self.tmp)
        import sqlite3
        from unittest import mock
        with wb.engine(b.path) as cm, mock.patch.object(cm, "cmd_recall", side_effect=sqlite3.OperationalError("no such column: x")):
            from voidloom import _facade
            with mock.patch.dict(_facade.__dict__):
                got = _facade.recall("x", db_path=b.path)
        self.assertEqual((got["status"], got["code"]), ("error", "BRAIN_UNREADABLE"))


if __name__ == "__main__":
    unittest.main()
