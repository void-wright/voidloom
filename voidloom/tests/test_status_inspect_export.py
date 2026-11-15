"""`voidloom status`, `inspect` and `export`: what a customer reads about a brain, read-only and with no model.

Real brains (every migration in this tree) through the real CLI. `export --all` is
refused, typed, naming the wards a full-corpus export would need.
"""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import random
import sqlite3
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "organs"))
sys.path.insert(0, str(ROOT))

import embedder_lock as el  # noqa: E402
import memory_write as mw  # noqa: E402
import schema_migrator  # noqa: E402

MIGRATIONS = ROOT / "migrations"
FOREIGN = "b" * 64
TABLE, DIM = "memory_embeddings", 768


def _report_double(conn):
    """memory_write.derived_layer_report by its archive signature, for a branch that does not carry it yet: one
    GROUP BY per table, stale = stamped under anything but the lock's checksum."""
    try:
        whash = mw.lock_identity(conn)["weights_hash"]
        basis = "rows under another checksum are STALE"
    except Exception:
        whash, basis = None, "no lock on this brain: every stamped row counts as stale"
    out = {"lock_weights_hash": whash, "basis": basis, "tables": {}, "stale_derived": 0, "unstamped": 0,
           "current": 0, "total": 0, "unmeasured": []}
    for tab in ("memory_links", "memory_communities", "community_summaries"):
        groups = conn.execute(f"SELECT weights_hash, COUNT(*) FROM {tab} GROUP BY weights_hash").fetchall()
        hist = {h: n for h, n in groups if h}
        total = sum(n for _h, n in groups)
        unstamped = total - sum(hist.values())
        current = hist.get(whash, 0) if whash else 0
        out["tables"][tab] = {"present": True, "total": total, "current": current,
                              "stale": total - unstamped - current, "unstamped": unstamped, "histogram": hist}
        out["stale_derived"] += total - unstamped - current
        out["unstamped"] += unstamped
        out["current"] += current
        out["total"] += total
    out["line"] = f"derived layer: {out['stale_derived']} stale"
    return out


def setUpModule():
    if not hasattr(mw, "derived_layer_report"):
        mw.derived_layer_report = _report_double


def tearDownModule():
    if getattr(mw, "derived_layer_report", None) is _report_double:
        del mw.derived_layer_report


def run(*args):
    from voidloom import cli
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = cli.dispatch(list(args))
    return rc, json.loads(buf.getvalue())


def stamp(conn, mid, whash, table=TABLE, dim=DIM, at="2026-01-01T00:00:00+00:00", kind="text", inputs=None):
    # a text stamp carries the hash of the text it was embedded from, as the writer's does (export checks it)
    row = conn.execute("SELECT content FROM memories WHERE id = ?", (mid,)).fetchone()
    digest = mw.content_hash(row[0]) if kind == "text" and row else "d" * 64
    conn.execute(
        f"INSERT OR REPLACE INTO {mw.PROVENANCE_TABLE} ({', '.join(mw.PROVENANCE_COLUMNS)}) "
        "VALUES (?, ?, 'model', 'ref', ?, ?, ?, ?, 'test', ?, ?)", (mid, table, whash, dim, digest, at, kind, inputs))


class _Brain(unittest.TestCase):
    LOCK = "cpu"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)
        self.db = self.d / "claude_memory.db"
        out = schema_migrator.apply(str(self.db), MIGRATIONS)
        self.assertEqual(out.get("failed") or [], [], out)
        self.lock = el.write_lock(self.d, self.LOCK) if self.LOCK else None
        self.h = self.lock["weights_hash"] if self.lock else None
        self.conn = self.connect()
        self.conn.execute(f"CREATE VIRTUAL TABLE IF NOT EXISTS {TABLE} USING vec0("
                          f"memory_id INTEGER PRIMARY KEY, embedding FLOAT[{DIM}])")
        self.conn.commit()

    def connect(self):
        import sqlite_vec
        c = sqlite3.connect(str(self.db))
        c.enable_load_extension(True)
        sqlite_vec.load(c)
        c.enable_load_extension(False)
        return c

    def tearDown(self):
        self.conn.close()
        el.clear_loaded()
        os.chmod(self.d, stat.S_IRWXU)
        self.tmp.cleanup()

    def memory(self, text="a thing", *, whash=None, vector=True, seed=0, origin=None, archived=0, tags=()):
        cur = self.conn.execute(
            "INSERT INTO memories (content, summary, category, importance, seed, origin, is_archived) "
            "VALUES (?, 's', 'insight', 0.5, ?, ?, ?)", (text, seed, origin, archived))
        mid = int(cur.lastrowid)
        if vector:
            self.conn.execute(f"INSERT INTO {TABLE} (memory_id, embedding) VALUES (?, ?)", (mid, bytes(4 * DIM)))
        if whash:
            stamp(self.conn, mid, whash)
        for t in tags:
            self.conn.execute("INSERT INTO memory_tags (memory_id, tag) VALUES (?, ?)", (mid, t))
        self.conn.commit()
        return mid


class Status(_Brain):
    def test_control_every_number_is_derived(self):
        self.memory(whash=self.h)
        self.memory(whash=self.h)
        self.memory(vector=False)
        self.memory(archived=1)
        self.conn.execute("INSERT INTO memory_links (source_id, target_id, link_type, strength, weights_hash) "
                          "VALUES (1, 2, 'related', 0.7, ?)", (FOREIGN,))
        self.conn.commit()
        # The lock's model bytes being on THIS disk is not what this control is about (a host without them reads
        # status_degraded, by design, tested where that refusal lives); every number below is derived either way.
        from voidloom import cli
        with __import__("unittest.mock").mock.patch.object(cli, "_absent_model", lambda *a, **k: None):
            rc, env = run("status", "--db", str(self.db), "--json")
        self.assertEqual((rc, env["status"], env["code"]), (0, "ok", "status_ok"))
        d = env["data"]
        self.assertEqual(d["lock"]["weights_hash"], self.h)
        self.assertEqual((d["coverage"]["matched"], d["coverage"]["total"]), (2, 3))
        self.assertEqual(d["counts"]["memories_active"], 3)
        self.assertEqual(d["counts"]["memories_archived"], 1)
        self.assertEqual(d["counts"]["memory_links"], 1)
        self.assertEqual(d["derived_layer"]["stale_derived"], 1)
        self.assertEqual(d["derived_layer"]["tables"]["memory_links"]["stale"], 1)
        self.assertIn("1 stale", d["line"])
        self.assertTrue(d["seed"]["columns_present"])

    def test_saboteur_a_base_moved_under_a_derived_row_reads_stale_one(self):
        self.memory(whash=self.h)
        self.memory(whash=self.h)
        self.conn.execute("INSERT INTO memory_links (source_id, target_id, link_type, strength, weights_hash) "
                          "VALUES (1, 2, 'related', 0.7, ?)", (self.h,))
        self.conn.commit()
        self.assertEqual(run("status", "--db", str(self.db), "--json")[1]["data"]["derived_layer"]["stale_derived"], 0)
        self.conn.execute("UPDATE memory_links SET weights_hash = ?", (FOREIGN,))
        self.conn.commit()
        self.assertEqual(run("status", "--db", str(self.db), "--json")[1]["data"]["derived_layer"]["stale_derived"], 1)

    def test_control_it_writes_nothing_and_answers_on_a_read_only_brain(self):
        self.memory(whash=self.h)
        self.conn.close()
        before = hashlib.sha256(self.db.read_bytes()).hexdigest()
        names = sorted(p.name for p in self.d.iterdir())
        os.chmod(self.db, stat.S_IRUSR)
        os.chmod(self.d, stat.S_IRUSR | stat.S_IXUSR)
        try:
            rc, env = run("status", "--db", str(self.db), "--json")
        finally:
            os.chmod(self.d, stat.S_IRWXU)
            os.chmod(self.db, stat.S_IRUSR | stat.S_IWUSR)
        self.conn = self.connect()
        self.assertEqual((rc, env["status"]), (0, "ok"))
        self.assertEqual(env["data"]["counts"]["memories_active"], 1)
        self.assertEqual(hashlib.sha256(self.db.read_bytes()).hexdigest(), before)
        self.assertEqual(sorted(p.name for p in self.d.iterdir()), names)

    def test_saboteur_a_live_write_ahead_log_is_never_skipped_for_read_only_media(self):
        """`immutable` would read past it, so the open must fail rather than answer from a stale page."""
        from voidloom import cli
        self.memory(whash=self.h)
        self.conn.close()
        wal = Path(str(self.db) + "-wal")
        wal.write_bytes(b"x" * 64)
        os.chmod(self.d, stat.S_IRUSR | stat.S_IXUSR)
        real = sqlite3.connect
        calls = []

        def spy(target, *a, **k):
            calls.append(str(target))
            return real(target, *a, **k)
        try:
            with __import__("unittest.mock").mock.patch.object(cli.sqlite3, "connect", spy):
                try:
                    cli._open_brain_ro(str(self.db)).close()
                except sqlite3.Error:
                    pass
        finally:
            os.chmod(self.d, stat.S_IRWXU)
        wal.unlink()
        self.conn = self.connect()
        self.assertFalse([c for c in calls if "immutable" in c], calls)

    def test_saboteur_a_malformed_lock_is_named_and_the_page_still_answers(self):
        self.memory(whash=self.h)
        (self.d / "embedder.lock.json").write_text("{ not json", encoding="utf-8")
        rc, env = run("status", "--db", str(self.db), "--json")
        self.assertEqual((rc, env["data"]["lock"]["refusal"]), (0, "LOCK_MALFORMED"))
        self.assertFalse(env["data"]["coverage"]["ok"])
        self.assertEqual(env["data"]["counts"]["memories_active"], 1)

    def test_saboteur_a_file_that_is_no_database_is_typed(self):
        garbage = self.d / "garbage.db"
        garbage.write_bytes(b"not a database" * 100)
        for verb in (["status"], ["inspect", "1"], ["export", "--ids", "1"]):
            rc, env = run(*verb, "--db", str(garbage), "--json")
            self.assertEqual((rc, env["code"]), (1, "brain_unreadable"), verb)

    def test_no_brain_is_a_typed_error(self):
        rc, env = run("status", "--db", str(self.d / "nope.db"), "--json")
        self.assertEqual((rc, env["code"]), (1, "db_missing"))

    def test_no_model_is_imported(self):
        self.memory(whash=self.h)
        code = ("import sys, json, io, contextlib\n"
                f"sys.path.insert(0, {str(ROOT)!r})\n"
                "from voidloom import cli\n"
                f"rcs = [cli.dispatch(a) for a in ({['status', '--db', str(self.db), '--json']!r}, "
                f"{['inspect', '1', '--db', str(self.db), '--json']!r}, "
                f"{['export', '--ids', '1', '--db', str(self.db), '--json']!r})]\n"
                "heavy = sorted(m for m in ('torch', 'transformers', 'sentence_transformers') if m in sys.modules)\n"
                "print('RESULT', rcs, heavy)\n")
        r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr[-600:])
        self.assertIn("RESULT [0, 0, 0] []", r.stdout)


class StatusNoLock(_Brain):
    LOCK = None

    def setUp(self):
        super().setUp()
        self.conn.execute("DELETE FROM metadata WHERE key LIKE 'active_embedding%'")
        self.conn.executemany("INSERT INTO metadata (key, value) VALUES (?, ?)",
                              [("active_embedding_table", TABLE), ("active_embedding_dim", str(DIM))])
        self.conn.commit()

    def test_control_a_brain_with_no_lock_is_read_and_says_so(self):
        self.memory(whash=mw.WEIGHTS_HASH_UNRESOLVED)
        rc, env = run("status", "--db", str(self.db), "--json")
        d = env["data"]
        self.assertEqual(rc, 0)
        self.assertIs(d["lock"]["present"], False)
        self.assertNotIn("refusal", d["lock"], "no lock is a state of the brain, never a fault in it")
        self.assertIn("no lock on this brain", d["lock"]["note"])
        self.assertEqual(d["coverage"]["basis"], "lockless")
        self.assertIn("no lock", d["derived_layer"]["basis"])

    def test_inspect_says_the_row_is_searched_as_the_whole_table(self):
        mid = self.memory()
        rc, env = run("inspect", str(mid), "--db", str(self.db), "--json")
        self.assertEqual(rc, 0)
        self.assertTrue(env["data"]["in_search_set"])
        self.assertEqual(env["data"]["search_rule"], mw.SEARCH_RULE_LOCKLESS)
        self.assertIsNone(env["data"]["model_stamp"])


class Inspect(_Brain):
    SHAPE = {"id", "text", "seed", "origin", "visibility", "model_stamp", "index_coverage"}

    def check(self, mid, code="inspect_ok"):
        rc, env = run("inspect", str(mid), "--db", str(self.db), "--json")
        self.assertEqual((rc, env["code"]), (0, code), env)
        self.assertIs(env.get("degraded"), True if code == "inspect_degraded" else None, env)
        d = env["data"]
        self.assertLessEqual(self.SHAPE, set(d))
        self.assertEqual(d["id"], mid)
        self.assertEqual(set(d["index_coverage"]) & {"matched", "total", "coverage"}, {"matched", "total", "coverage"})
        return d

    def test_control_fifty_random_and_five_seed_ids_all_have_the_shape(self):
        rng = random.Random(3)
        plain = [self.memory(f"memory {i}", whash=self.h if i % 2 else None,
                             tags=("visibility:private",) if i % 3 == 0 else ()) for i in range(60)]
        seeds = [self.memory(f"seed {i}", whash=self.h, seed=1, origin=f"seed_pack:1#{i:016x}") for i in range(5)]
        for mid in rng.sample(plain, 50):
            d = self.check(mid)
            self.assertFalse(d["seed"])
            self.assertIsNone(d["origin"])
            self.assertIn(d["visibility"], ("private", "unclassified"))
        for mid in seeds:
            d = self.check(mid)
            self.assertTrue(d["seed"])
            self.assertTrue(d["origin"].startswith("seed_pack:1#"))

    def test_control_a_verified_row(self):
        mid = self.memory("hello", whash=self.h)
        d = self.check(mid)
        self.assertEqual((d["text"], d["visibility"]), ("hello", "unclassified"))
        self.assertEqual(d["visibility_basis"], "no visibility tag")
        self.assertEqual(d["model_stamp"]["weights_hash"], self.h)
        self.assertTrue(d["in_search_set"])
        self.assertEqual((d["index_coverage"]["matched"], d["index_coverage"]["total"]), (1, 1))

    def test_saboteur_a_row_under_another_checksum_is_outside_the_search_set(self):
        self.memory(whash=self.h)
        mid = self.memory(whash=FOREIGN)
        d = self.check(mid)
        self.assertEqual(d["model_stamp"]["weights_hash"], FOREIGN)
        self.assertFalse(d["in_search_set"])

    def test_a_legacy_row_is_in_the_search_set_and_its_stamp_says_unresolved(self):
        d = self.check(self.memory(whash=mw.WEIGHTS_HASH_UNRESOLVED))
        self.assertTrue(d["in_search_set"])
        self.assertEqual(d["model_stamp"]["weights_hash"], mw.WEIGHTS_HASH_UNRESOLVED)

    def test_saboteur_an_archived_row_is_outside_the_search_set(self):
        self.assertFalse(self.check(self.memory(whash=self.h, archived=1))["in_search_set"])

    def test_a_row_with_no_vector_is_outside_the_search_set(self):
        self.memory(whash=self.h)
        d = self.check(self.memory(vector=False))
        self.assertFalse(d["in_search_set"])
        self.assertIsNone(d["model_stamp"])

    def test_conflicting_visibility_tags_are_unclassified_and_say_why(self):
        d = self.check(self.memory(tags=("visibility:public", "visibility:private")))
        self.assertEqual(d["visibility"], "unclassified")
        self.assertIn("2 conflicting", d["visibility_basis"])

    def test_an_empty_visibility_tag_is_no_classification(self):
        self.assertEqual(self.check(self.memory(tags=("visibility:",)))["visibility"], "unclassified")

    def test_the_stamp_shown_is_the_one_on_the_searched_table(self):
        mid = self.memory(whash=self.h)
        stamp(self.conn, mid, "c" * 64, table="memory_embeddings_v3", dim=1024, at="2027-01-01T00:00:00+00:00")
        self.conn.commit()
        d = self.check(mid)
        self.assertEqual((d["model_stamp"]["vec_table"], d["stamps"]), (TABLE, 2))

    def test_a_centroid_shows_what_it_was_derived_from(self):
        mid = self.memory()
        stamp(self.conn, mid, self.h, kind="centroid", inputs="[1, 2]")
        self.conn.commit()
        d = self.check(mid)
        self.assertEqual((d["model_stamp"]["input_kind"], d["model_stamp"]["inputs"]), ("centroid", "[1, 2]"))

    def test_saboteur_one_corrupt_row_is_read_not_fatal(self):
        """Text that is not valid UTF-8: sqlite3 raises on it by default, and the CLI would print `unhandled`."""
        mid = self.memory("will be damaged", whash=self.h)
        self.conn.execute("UPDATE memories SET content = CAST(x'ff41ff' AS TEXT) WHERE id = ?", (mid,))
        self.conn.commit()
        d = self.check(mid)
        self.assertIn("A", d["text"])
        rc, env = run("export", "--ids", str(mid), "--db", str(self.db), "--json")
        self.assertEqual((rc, env["data"]["exported"]), (0, 1))

    def test_a_missing_id_is_typed(self):
        rc, env = run("inspect", "99999", "--db", str(self.db), "--json")
        self.assertEqual((rc, env["code"]), (1, "not_found"))

    def test_a_sidecar_that_is_gone_is_named_not_fatal(self):
        mid = self.memory(whash=self.h)
        self.conn.execute(f"DROP TABLE {mw.PROVENANCE_TABLE}")
        self.conn.commit()
        d = self.check(mid, "inspect_degraded")     # named at the top, and still an answer: status ok, exit 0
        self.assertIn("unreadable", d["model_stamp"])
        self.assertFalse(d["index_coverage"]["ok"])

    def test_it_is_in_the_help(self):
        from voidloom import cli
        text = cli.build_parser().format_help()
        for verb in ("status", "inspect", "export"):
            self.assertIn(verb, text)


class Export(_Brain):
    def test_control_an_explicit_selection(self):
        a = self.memory("one", whash=self.h, tags=("visibility:public", "x"))
        b = self.memory("two", seed=1, origin="seed_pack:1#0")
        rc, env = run("export", "--ids", f"{b},{a}, 9999", "--db", str(self.db), "--json")
        self.assertEqual((rc, env["code"]), (0, "export_ok"))
        d = env["data"]
        self.assertEqual((d["selection"], d["requested"], d["exported"], d["missing_ids"]), ("explicit", 3, 2, [9999]))
        self.assertEqual([r["id"] for r in d["records"]], [a, b])
        self.assertEqual(d["records"][0]["visibility"], "public")
        self.assertEqual(d["records"][0]["tags"], ["visibility:public", "x"])
        self.assertEqual(d["records"][0]["model_stamp"]["weights_hash"], self.h)
        self.assertTrue(d["records"][1]["seed"])

    def test_control_a_single_id(self):
        a = self.memory("only")
        rc, env = run("export", "--ids", str(a), "--db", str(self.db), "--json")
        self.assertEqual((rc, env["data"]["exported"]), (0, 1))
        self.assertEqual(env["data"]["records"][0]["text"], "only")

    def test_saboteur_all_is_refused_typed_naming_the_wards(self):
        self.memory(tags=("visibility:public",))
        self.memory()
        self.memory()
        rc, env = run("export", "--all", "--db", str(self.db), "--json")
        self.assertEqual((rc, env["status"], env["code"]), (3, "refused", "EXPORT_ALL_REFUSED"))
        detail = env["data"]["detail"]
        self.assertEqual(len(detail["unbuilt_wards"]), 2)
        self.assertEqual((detail["unclassified"], detail["active"]), (2, 3))
        self.assertNotIn("records", env["data"])
        self.assertIn("2 of 3", env["data"]["message"])

    def test_saboteur_conflicting_tags_count_as_unclassified_as_inspect_reads_them(self):
        self.memory(tags=("visibility:public", "visibility:private"))
        self.memory(tags=("visibility:",))
        self.memory(tags=("visibility:public",))
        detail = run("export", "--all", "--db", str(self.db), "--json")[1]["data"]["detail"]
        self.assertEqual((detail["unclassified"], detail["active"]), (2, 3))

    def test_saboteur_all_beside_a_selection_still_refuses(self):
        a = self.memory()
        rc, env = run("export", "--all", "--ids", str(a), "--db", str(self.db), "--json")
        self.assertEqual((rc, env["code"]), (3, "EXPORT_ALL_REFUSED"))

    def test_no_selection_and_a_bad_selection_are_typed(self):
        self.assertEqual(run("export", "--db", str(self.db), "--json")[1]["code"], "no_selection")
        self.assertEqual(run("export", "--ids", ",", "--db", str(self.db), "--json")[1]["code"], "no_selection")
        self.assertEqual(run("export", "--ids", "1,x", "--db", str(self.db), "--json")[1]["code"], "bad_selection")

    def test_it_writes_nothing(self):
        a = self.memory()
        self.conn.close()
        before = hashlib.sha256(self.db.read_bytes()).hexdigest()
        run("export", "--ids", str(a), "--db", str(self.db), "--json")
        run("export", "--all", "--db", str(self.db), "--json")
        self.conn = self.connect()
        self.assertEqual(hashlib.sha256(self.db.read_bytes()).hexdigest(), before)


class TheReportsAreSetBased(unittest.TestCase):
    """A per-row correlated lookup has cost this work twelve times at scale; these three read a whole brain."""

    def test_saboteur_no_new_report_takes_a_correlated_exists(self):
        import inspect
        import re
        from voidloom import cli
        for fn in (cli.verb_status, cli.verb_inspect, cli.verb_export, cli._memory_record, cli._model_stamp):
            self.assertIsNone(re.search(r"\bEXISTS\s*\(", inspect.getsource(fn)), fn.__name__)


class TheAgentCanSeeThem(unittest.TestCase):
    def test_control_the_toolkit_names_them(self):
        from voidloom import _agent_context
        names = {v["name"] for v in _agent_context.manifest()["verbs"]}
        self.assertLessEqual({"status", "inspect", "export"}, names)


class ThePathTravels(_Brain):
    def test_a_path_with_uri_characters_opens(self):
        d = self.d / "a dir #1?x"
        d.mkdir()
        db = d / "claude_memory.db"
        self.conn.close()
        db.write_bytes(self.db.read_bytes())
        self.conn = self.connect()
        el.write_lock(d, "cpu")
        rc, env = run("status", "--db", str(db), "--json")
        self.assertEqual(rc, 0, env)
        self.assertEqual(env["data"]["lock"]["weights_hash"], self.h)


if __name__ == "__main__":
    unittest.main()
