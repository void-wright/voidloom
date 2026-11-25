# -*- coding: utf-8 -*-
"""A store that cannot be written answers so: never `ok` with the id of a row that is not there.

WHAT WOULD BREAK WITHOUT IT: the disk fills during the store's last update; the error is swallowed, the commit writes
nothing, and the caller is told `ok` with an id. It records that id as the memory it just saved.

Each statement of a store is made to fail in turn, as a full disk fails it ("database or disk is full"), on the full
engine, the lite engine and batch-store; and a real limit (PRAGMA max_page_count) is reached once on each engine.
"""
import contextlib
import json
import re
import sqlite3
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _wall_brain as wb
from _wall_brain import Brain, Case, data

FULL = "database or disk is full"
CODES = ("DISK_FULL", "STORE_FAILED", "STORE_READBACK_FAILED")
#: where a store writes, as each engine says it
POSITIONS = {
    "row": r"^\s*INSERT INTO memories \(",
    "vector": r"^\s*INSERT INTO memory_embeddings",
    "stamp": r"INTO embedding_provenance",
    "novelty": r"^\s*UPDATE memories SET encoding_surprise",
    "tags": r"INTO memory_tags",
    "links": r"INTO memory_parents",
    "commit": None,
    "read-back": r"^SELECT content",
}
LITE = ("row", "tags", "links", "commit", "read-back")


class Failing(sqlite3.Connection):
    """A connection that fails one kind of statement the way a full disk does, once it is armed."""
    armed = None
    fired = 0

    def execute(self, sql, *a):
        pattern = POSITIONS.get(Failing.armed)
        if pattern and re.search(pattern, sql, re.I | re.S):
            Failing.fired += 1
            raise sqlite3.OperationalError(FULL)
        return super().execute(sql, *a)

    def commit(self):
        if Failing.armed == "commit" and self.in_transaction:
            Failing.fired += 1
            raise sqlite3.OperationalError(FULL)
        return super().commit()


@contextlib.contextmanager
def failing(db, position):
    real = sqlite3.connect
    Failing.armed, Failing.fired = position, 0

    def connect(path, *a, **k):
        if str(Path(db).name) in str(path) and "factory" not in k:
            k["factory"] = Failing
        return real(path, *a, **k)
    with mock.patch("sqlite3.connect", connect):
        yield
    Failing.armed = None


class DiskFull(Case):
    def store(self, b, lite, *extra):
        env = {"VOIDLOOM_TIER0_ONLY": "1"} if lite else None
        return wb.full_cli(b.path, "store", "--content", "gradle daemon note about the cache", "--category", "insight",
                           "--tags", "a,b", *extra, env=env)

    def brain(self):
        b = Brain(self.tmp / "b")
        self.parent = b.add("a note this one is written from")
        b.add("gradle cache daemon one")                        # a neighbour, so the novelty update is made
        return b

    def check(self, b, rc, env, where):
        acked = data(env).get("memory_id") if env and env.get("status") == "ok" else None
        self.assertIsNone(acked, f"{where}: answered ok")
        self.assertNotEqual(rc, 0, where)
        self.assertIn((env or {}).get("code"), CODES, f"{where}: {json.dumps(env)[:300]}")
        if (env or {}).get("memory_id") is not None:
            self.assertIsNotNone(b.one("SELECT 1 FROM memories WHERE id = ?", (env["memory_id"],)), where)

    def test_each_statement_of_a_store_failing(self):
        for lite in (False, True):
            for position in (LITE if lite else POSITIONS):
                b = self.brain()
                before = b.top()
                with failing(b.path, position):
                    rc, env, err = self.store(b, lite, "--source-ids", str(self.parent))
                where = f"{'lite' if lite else 'full'} {position}"
                self.assertTrue(Failing.fired, f"{where}: the statement was never reached, so this proves nothing")
                self.check(b, rc, env, where)
                if position != "read-back":
                    self.assertEqual(b.top(), before, f"{where}: something was kept")
                    self.assertIs(data(env).get("row_may_exist"), False, where)
                rc, env, _err = self.store(b, lite)                    # the brain still takes a store afterwards
                self.assertEqual((rc, env["status"]), (0, "ok"), where)
                self.assertIsNotNone(b.one("SELECT 1 FROM memories WHERE id = ?", (data(env)["memory_id"],)), where)
                b.c.close()

    def test_a_batch_answers_for_each_item(self):
        for lite in (False, True):
            for position in ("row", "tags", "commit") + (() if lite else ("novelty",)):
                b = self.brain()
                items = [dict(content=f"gradle cache item {i}", category="insight", tags=["t"]) for i in range(3)]
                real = Failing.execute

                with failing(b.path, None):
                    calls = {"n": 0}

                    def arm_on_second(self_, sql, *a):
                        if re.search(r"^\s*INSERT INTO memories \(", sql):
                            calls["n"] += 1
                            Failing.armed = position if calls["n"] == 2 else None
                        return real(self_, sql, *a)
                    with mock.patch.object(Failing, "execute", arm_on_second):
                        rc, env, _err = wb.full_cli(b.path, "batch-store", "--items", json.dumps(items),
                                                    env={"VOIDLOOM_TIER0_ONLY": "1"} if lite else None)
                where = f"{'lite' if lite else 'full'} batch {position}"
                results = data(env)["results"]
                self.assertEqual([r["status"] for r in results], ["ok", "error", "ok"], where)
                self.assertNotEqual(rc, 0, where)
                acked = [r["memory_id"] for r in results if r["status"] == "ok"]
                present = [m for m in acked if b.one("SELECT 1 FROM memories WHERE id = ?", (m,))]
                self.assertEqual(acked, present, where)
                self.assertEqual(b.one("SELECT COUNT(*) FROM memories WHERE content LIKE 'gradle cache item %'")[0], 2, where)
                b.c.close()

    def test_a_real_limit_is_reached(self):
        for lite in (False, True):
            b = self.brain()
            before = b.top()
            real = sqlite3.connect

            def connect(path, *a, **k):
                conn = real(path, *a, **k)
                if Path(b.path).name in str(path) and "mode=ro" not in str(path):
                    conn.execute("PRAGMA max_page_count = %d" % conn.execute("PRAGMA page_count").fetchone()[0])
                return conn
            with mock.patch("sqlite3.connect", connect):
                rc, env, _err = wb.full_cli(b.path, "store", "--content", "gradle " * 40000, "--category", "insight",
                                            env={"VOIDLOOM_TIER0_ONLY": "1"} if lite else None)
            where = "lite" if lite else "full"
            self.check(b, rc, env, where)
            self.assertEqual((env["code"], b.top()), ("DISK_FULL", before), where)
            b.c.close()


if __name__ == "__main__":
    unittest.main()
