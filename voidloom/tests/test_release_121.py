# -*- coding: utf-8 -*-
"""What 1.2.1 fixes in the schema steps and the store's answers.

WHAT WOULD BREAK WITHOUT IT: migration_047 changes a brain and no record says so, so an audit or a later release cannot
tell it ran; a fresh brain keeps a copy of the legacy names it has no use for; a fresh install's schema text still names
the persona; and a store that fails because a table is gone, or because the file cannot be written, answers with the same
code as any other failure, so a caller cannot tell a damaged brain from a read-only one.
"""
import contextlib
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _wall_brain as wb
from _wall_brain import Brain, Case, ROOT, data
from test_names_confined import LEGACY, old_brain

sys.path.insert(0, str(ROOT / "organs"))
import marks
import schema_migrator

SEED = ROOT / "seed" / "claude_memory_blank.db"
F047 = "migration_047_brain_label.sql"
F048 = "migration_048_persona_schema_text.sql"


def file_sha(name):
    return hashlib.sha256((ROOT / "migrations" / name).read_bytes()).hexdigest()


def record(db, name):
    c = sqlite3.connect(db)
    try:
        return c.execute("SELECT sha256, statements FROM schema_migrations WHERE name = ?", (name,)).fetchone()
    finally:
        c.close()


def saved(db):
    c = sqlite3.connect(db)
    try:
        return c.execute("SELECT value FROM metadata WHERE key = ?", (marks.SAVED_SQL_KEY,)).fetchone()
    finally:
        c.close()


def fresh_install(tmp):
    """The blank seed brought up by the shipped migrator, as the installer does."""
    tmp.mkdir(parents=True, exist_ok=True)
    db = tmp / "claude_memory.db"
    shutil.copy2(str(SEED), str(db))
    assert schema_migrator.apply(str(db))["ok"]
    return str(db)


def persona_census(db):
    """Every schema object whose text names a legacy label, with how many times: (type, name) -> count."""
    c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        out = {}
        for t, n, s in c.execute("SELECT type, name, sql FROM sqlite_master WHERE sql IS NOT NULL"):
            k = sum(s.lower().count(x) for x in LEGACY)
            if k:
                out[(t, n)] = k
        return out
    finally:
        c.close()


class Recorded047(Case):
    def test_a_fresh_install_records_it_and_keeps_no_undo_copy(self):
        db = fresh_install(self.tmp)
        self.assertEqual(record(db, F047), (file_sha(F047), 0))
        self.assertIsNone(saved(db))
        self.assertEqual(marks.rollback_047(db).get("code"), "NOTHING_TO_UNDO")

    def test_a_populated_brain_records_it_and_keeps_the_undo_copy(self):
        db = old_brain(self.tmp, "027 file")
        got = schema_migrator.apply(db)["migration_047"]
        self.assertEqual((got["status"], got["undo_copy_kept"]), ("applied", True))
        self.assertEqual(record(db, F047), (file_sha(F047), 0))
        self.assertEqual(saved(db)[0].count("CHECK (source_brain"), 1)
        self.assertEqual(marks.rollback_047(db)["status"], "ok")
        self.assertIsNone(record(db, F047))                         # the undo takes the record with it

    def test_already_applied_is_recorded_and_an_empty_brains_copy_is_dropped(self):
        db = fresh_install(self.tmp)
        c = sqlite3.connect(db)
        c.execute("DELETE FROM schema_migrations WHERE name = ?", (F047,))                  # as 1.2.0 left it
        c.execute("INSERT INTO metadata (key, value) VALUES (?, 'the old text')", (marks.SAVED_SQL_KEY,))
        c.commit()
        c.close()
        got = marks.migrate_047(db)
        self.assertEqual((got["status"], got["recorded"], got["undo_copy_dropped"]), ("already", True, True))
        self.assertEqual(record(db, F047), (file_sha(F047), 0))
        self.assertIsNone(saved(db))
        self.assertEqual(marks.migrate_047(db)["status"], "already")


class NoPersonaInTheSchema(Case):
    ALLOWED = {("table", "cross_tier_bridges"): 2}      # its frozen migration's CHECK names both legacy tiers: declared

    def test_the_seed_names_nobody(self):
        self.assertEqual(persona_census(SEED), {})

    def test_a_fresh_install_names_nobody_but_the_declared_check(self):
        db = fresh_install(self.tmp)
        self.assertEqual(persona_census(db), self.ALLOWED)
        self.assertEqual(record(db, F048), (file_sha(F048), 0))

    def test_a_brain_built_without_the_seed_names_nobody_either(self):
        self.tmp.mkdir(parents=True, exist_ok=True)
        db = str(self.tmp / "bare.db")
        sqlite3.connect(db).close()
        self.assertTrue(schema_migrator.apply(db)["ok"])
        self.assertEqual(persona_census(db), self.ALLOWED)

    def older(self):
        """A brain as 1.2.0 left it: the three tables in the frozen migrations' words, with rows."""
        db = fresh_install(self.tmp)
        self.assertEqual(marks.rollback_048(db)["status"], "ok")
        c = sqlite3.connect(db)
        c.execute("INSERT INTO council_sessions (trigger_type, topic) VALUES ('manual', 'took the DEFAULT')")
        c.execute("INSERT INTO council_sessions (trigger_type, topic, participants) VALUES ('manual', 't', '[\"orin\"]')")
        c.execute("INSERT INTO self_eval_reports (month, health_scorecard, proposed_enhancements) VALUES ('2026-01', '{}', '[]')")
        c.commit()
        c.close()
        return db

    def participants(self, db):
        c = sqlite3.connect(db)
        try:
            return c.execute("SELECT id, participants FROM council_sessions ORDER BY id").fetchall()
        finally:
            c.close()

    def test_a_populated_brain_is_edited_and_no_stored_row_changes(self):
        db = self.older()
        self.assertEqual(len(persona_census(db)), 4)
        before = self.participants(db)
        self.assertIn(LEGACY[0], before[0][1])                    # a row that took the old DEFAULT keeps its own value
        got = marks.migrate_048(db)
        self.assertEqual((got["status"], got["recorded"]), ("applied", True))
        self.assertEqual(self.participants(db), before)
        self.assertEqual(persona_census(db), self.ALLOWED)
        c = sqlite3.connect(db)
        self.assertEqual(c.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        c.execute("INSERT INTO council_sessions (trigger_type, topic) VALUES ('manual', 'new')")
        self.assertNotIn(LEGACY[0], c.execute("SELECT participants FROM council_sessions ORDER BY id DESC").fetchone()[0])
        c.close()

    def test_only_the_named_pieces_change(self):
        db = self.older()
        c = sqlite3.connect(db)
        old = {n: s for n, s in c.execute("SELECT name, sql FROM sqlite_master")}
        c.close()
        marks.migrate_048(db)
        c = sqlite3.connect(db)
        new = {n: s for n, s in c.execute("SELECT name, sql FROM sqlite_master")}
        c.close()
        changed = {n for n in old if old[n] != new[n]}
        self.assertEqual(changed, {"council_sessions", "arcane_manifests", "self_eval_reports"})
        for n in changed:
            back = new[n]
            for o, w in marks._persona_edits()[n]:
                back = back.replace(w, o)
            self.assertEqual(back, old[n], n)

    def test_a_text_it_does_not_match_is_an_abort_that_changes_nothing(self):
        db = self.older()
        c = sqlite3.connect(db)
        sql = c.execute("SELECT sql FROM sqlite_master WHERE name = 'arcane_manifests'").fetchone()[0]
        piece = marks._persona_edits()["arcane_manifests"][0][0]
        self.assertIn(piece, sql)
        ver = c.execute("PRAGMA schema_version").fetchone()[0]
        c.execute("PRAGMA writable_schema = ON")
        c.execute("UPDATE sqlite_master SET sql = ? WHERE name = 'arcane_manifests'",
                  (sql.replace(piece, piece + "\n    " + piece, 1),))        # the piece twice: which one is meant?
        c.execute("PRAGMA writable_schema = OFF")
        c.execute(f"PRAGMA schema_version = {ver + 1}")
        c.commit()
        c.close()
        raw = Path(db).read_bytes()
        got = marks.migrate_048(db)
        self.assertEqual((got["status"], got["code"]), ("aborted", "TEXT_NOT_MATCHED"))
        self.assertEqual(Path(db).read_bytes(), raw)

    def test_an_edit_that_breaks_the_table_is_refused(self):
        db = self.older()
        raw = Path(db).read_bytes()
        got = marks.migrate_048(db, edit=lambda t, text: text.replace("id INTEGER PRIMARY KEY AUTOINCREMENT,", "", 1))
        self.assertEqual(got["status"], "aborted")
        self.assertEqual(Path(db).read_bytes(), raw)

    def test_a_read_only_brain_is_left_alone(self):
        db = self.older()
        raw = Path(db).read_bytes()
        os.chmod(db, 0o444)
        try:
            got = marks.migrate_048(db)
        finally:
            os.chmod(db, 0o644)
        self.assertEqual((got["status"], got["code"]), ("aborted", "READ_ONLY"))
        self.assertEqual(Path(db).read_bytes(), raw)

    def test_doctor_reads_it(self):
        db = self.older()
        rc, env, _err = wb.full_cli(db, "doctor")
        self.assertEqual(data(env)["checks"]["brain_label"]["migration_048"]["status"], "pending")
        marks.migrate_048(db)
        rc, env, _err = wb.full_cli(db, "doctor")
        self.assertEqual(data(env)["checks"]["brain_label"]["migration_048"]["status"], "applied")


class Failing(sqlite3.Connection):
    """A connection whose row insert fails with the armed error."""
    error = None

    def execute(self, sql, *a):
        if Failing.error is not None and re.search(r"^\s*INSERT INTO memories \(", sql, re.I):
            raise Failing.error
        return super().execute(sql, *a)


@contextlib.contextmanager
def failing(db, error):
    real = sqlite3.connect
    Failing.error = error

    def connect(path, *a, **k):
        if str(Path(db).name) in str(path) and "factory" not in k:
            k["factory"] = Failing
        return real(path, *a, **k)
    with mock.patch("sqlite3.connect", connect):
        yield
    Failing.error = None


CLASSES = {
    "SCHEMA_DAMAGED": sqlite3.OperationalError("no such table: memory_embeddings_v2"),
    "DB_ERROR": sqlite3.OperationalError("attempt to write a readonly database"),
    "DISK_FULL": sqlite3.OperationalError("database or disk is full"),
    "STORE_FAILED": sqlite3.IntegrityError("CHECK constraint failed: something"),
}


class StoreCodes(Case):
    def test_each_class_keeps_its_code_on_every_engine(self):
        for n, (code, error) in enumerate(CLASSES.items()):
            for engine in ("full", "lite"):
                b = Brain(self.tmp / f"{n}{engine}")
                count = b.c.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
                env = {"VOIDLOOM_TIER0_ONLY": "1"} if engine == "lite" else None
                with failing(b.path, error):
                    rc, out, _err = wb.full_cli(b.path, "store", "--content", "a note", "--category", "insight", env=env)
                    brc, bout, _berr = wb.full_cli(b.path, "batch-store", "--items",
                                                   json.dumps([{"content": "a batch note", "category": "insight"}]), env=env)
                said = json.dumps(out)
                self.assertEqual(rc, 1, (code, engine, said))
                self.assertIn(code, said, (engine, said))
                self.assertNotEqual((out or {}).get("status"), "ok", (code, engine))
                self.assertNotIn('"row_may_exist": true', said, (code, engine))
                item = data(bout)["results"][0]
                self.assertEqual((item["status"], item.get("code")), ("error", code), (engine, bout))
                self.assertEqual(b.c.execute("SELECT COUNT(*) FROM memories").fetchone()[0], count, (code, engine))

    def test_a_dropped_vector_table_and_a_read_only_brain_on_the_engine_file(self):
        b = Brain(self.tmp / "vec")
        b.add("a note before")
        b.c.execute("DROP TABLE memory_embeddings_v2")
        b.c.commit()
        rc, out, _err = wb.full_cli(b.path, "store", "--content", "after the drop", "--category", "insight")
        self.assertEqual(rc, 1, out)
        self.assertIn("SCHEMA_DAMAGED", json.dumps(out))
        self.assertEqual(marks.write_failure(sqlite3.OperationalError("attempt to write a readonly database")).code,
                         "DB_ERROR")
        self.assertEqual(marks.write_failure(sqlite3.OperationalError("database is locked")).code, "DB_ERROR")


class OneModelForTwoBrains(Case):
    """A note, not a change: the engine's cache is keyed by the brain, and every key holds the one model object."""
    def test_two_brains_load_the_model_once(self):
        import claude_memory as cm
        import embedding_upgrade as eu
        loads = []

        class Model:
            def __init__(self, *a, **k):
                loads.append(a)
        lock = {"weights_hash": "a" * 64, "model_id": "nomic-ai/nomic-embed-text-v1.5", "revision": "b" * 40,
                "dim": 768, "vec_table": "memory_embeddings_v2", "tier": "nomic"}
        fake_lock = types.SimpleNamespace(verify_snapshot_against_lock=lambda lk: (self.tmp, lk["weights_hash"]),
                                          register_loaded=lambda **k: None, EmbedderLockError=RuntimeError)
        one, two = Brain(self.tmp / "one"), Brain(self.tmp / "two")
        with mock.patch.dict(cm._model_cache, clear=True), mock.patch.dict(eu._model_cache, clear=True), \
                mock.patch.object(eu, "_lock_module", lambda: fake_lock), \
                mock.patch.object(eu, "_embedding_runtime", lambda m: (None, Model)), \
                mock.patch.object(eu, "_torch_device", lambda t: "cpu"), \
                mock.patch.object(eu, "brain_lock", lambda target, tier: dict(lock)), \
                contextlib.redirect_stderr(open(os.devnull, "w")):
            got = []
            for b in (one, two, one):
                with mock.patch.object(cm, "DB_PATH", Path(b.path)):
                    got.append(cm._load_model())
            self.assertEqual(len(loads), 1)
            self.assertEqual(len(cm._model_cache), 2)
            self.assertTrue(got[0] is got[1] is got[2])


if __name__ == "__main__":
    unittest.main()
