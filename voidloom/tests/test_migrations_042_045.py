"""Migrations 042-045: the provenance identity, the upgrade log's DEFAULTs, the seed columns, the derived-layer stamp.

What these four files must do, read from the brain, never from the files' own claims:

  * on a FRESH brain they apply, and leave no `_new` / `_old` table behind;
  * on a brain already at migration_041 the migrator derives EXACTLY these four as missing and applies them,
    WITH ROWS in the two rebuilt tables: every embedding_provenance row is carried forward with the declared
    absence weights_hash 'unresolved' (never a checksum nobody computed) and input_kind 'text'; every
    embedding_upgrade_log row and its AUTOINCREMENT mark survive; the DEFAULTs naming one model are gone;
  * both paths end in the SAME normalised DDL for every object these files touch (path independence);
  * a second apply runs none of them;
  * a table this migration does not know is refused whole, never truncated (the named CHECK fires and the
    file rolls back);
  * the write path brings a 033-shaped sidecar to the 042 shape by itself and states all eleven columns.

Seconds, not minutes; stdlib + sqlite3. Never opens a live brain.
"""
from __future__ import annotations

import re
import shutil
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

BRAIN = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BRAIN / "organs"))
import schema_migrator as sm  # noqa: E402

MDIR = BRAIN / "migrations"
BOXES = BRAIN / ".pytest_boxes"
NEW = ("migration_042_embedder_lock.sql", "migration_043_retire_encumbered_default.sql",
       "migration_044_seed_origin.sql", "migration_045_derived_layer_stamp.sql")
TOUCHED = ("embedding_provenance", "embedding_upgrade_log", "memories", "memory_links", "memory_communities",
           "community_summaries")


def _norm(sql):
    if sql is None:
        return None
    s = re.sub(r"\s+", " ", sql).strip()
    s = re.sub(r"(?i)\bIF NOT EXISTS\b ?", "", s)
    s = re.sub(r'(?i)^CREATE TABLE "([A-Za-z_][A-Za-z0-9_]*)"', r"CREATE TABLE \1", s)
    return s


def _touched_schema(db: Path) -> dict:
    c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        rows = c.execute("SELECT type, name, tbl_name, sql FROM sqlite_master WHERE tbl_name IN (%s) "
                         "AND name NOT LIKE 'sqlite_%%'" % ",".join("?" * len(TOUCHED)), TOUCHED).fetchall()
        out = {(t, n): (tb, _norm(s)) for t, n, tb, s in rows}
        for t in TOUCHED:
            out[("table_info", t)] = tuple(tuple(r) for r in c.execute(f"PRAGMA table_info({t})"))
        return out
    finally:
        c.close()


class _Box(unittest.TestCase):

    def setUp(self):
        BOXES.mkdir(exist_ok=True)
        self.box = Path(tempfile.mkdtemp(prefix="mig042_", dir=str(BOXES)))
        self.mdir041 = self.box / "migrations041"
        self.mdir041.mkdir()
        for f in MDIR.iterdir():
            if f.name not in NEW and not f.name.startswith("migration_046_"):      # 041 is before all of them
                shutil.copy2(f, self.mdir041 / f.name)

    def tearDown(self):
        shutil.rmtree(self.box, ignore_errors=True)

    def _new_db(self, name: str) -> Path:
        p = self.box / name
        sqlite3.connect(p).close()
        return p

    def _brain_at_041(self, rows: bool = True) -> Path:
        """A brain built by the package WITHOUT 042-045 (public main's chain to 041), then given rows the way an
        older install wrote them: provenance stamps with no weights_hash, upgrade-log rows that leaned on 013's
        DEFAULTs (the file as it shipped then carried them), links and communities."""
        db = self._new_db("at041.db")
        r = sm.apply(str(db), self.mdir041)
        self.assertTrue(r["ok"], r["files_with_errors"])
        if not rows:
            return db
        c = sqlite3.connect(db)
        try:
            c.execute("INSERT INTO memories (content, category, created_at, updated_at) "
                      "VALUES ('a', 'insight', datetime('now'), datetime('now'))")
            c.execute("INSERT INTO memories (content, category, created_at, updated_at) "
                      "VALUES ('b', 'insight', datetime('now'), datetime('now'))")
            for mid in (1, 2):
                c.execute("INSERT INTO embedding_provenance (memory_id, vec_table, model_id, model_ref, dim, "
                          "content_hash, embedded_at, organ) VALUES (?, 'memory_embeddings_v2', 'old/model', "
                          "'abc123', 1024, ?, '2026-05-01T00:00:00+00:00', 'claude_memory.store')",
                          (mid, "0" * 64))
            for i in range(3):
                c.execute("INSERT INTO embedding_upgrade_log (started_at, model_name, embedding_dim, status) "
                          "VALUES (datetime('now'), 'old/model', 1024, 'completed')")
            c.execute("DELETE FROM embedding_upgrade_log WHERE id = 3")   # the mark (3) is above max(id) (2)
            c.commit()
        finally:
            c.close()
        return db

    def _ran(self, report: dict) -> list:
        return sorted(f.name for f in sm._migration_files(MDIR) if f.name not in report["skipped_already_applied"])


class TestFreshBrain(_Box):

    def test_control_fresh_brain_applies_all_four_and_leaves_no_scaffolding(self):
        db = self._new_db("fresh.db")
        r = sm.apply(str(db), MDIR)
        self.assertTrue(r["ok"], r["files_with_errors"])
        c = sqlite3.connect(db)
        try:
            tables = {n for (n,) in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertFalse({t for t in tables if t.endswith(("_new", "_old"))}, tables)
            cols = [r[1] for r in c.execute("PRAGMA table_info(embedding_provenance)")]
            self.assertEqual(cols, ["memory_id", "vec_table", "model_id", "model_ref", "weights_hash", "dim",
                                    "content_hash", "embedded_at", "organ", "input_kind", "inputs"])
            self.assertTrue(all(r[4] is None for r in c.execute("PRAGMA table_info(embedding_provenance)")))
            idx = {n for (n,) in c.execute("SELECT name FROM sqlite_master WHERE type='index' "
                                           "AND tbl_name='embedding_provenance'")}
            self.assertTrue({"idx_embedding_provenance_model", "idx_embedding_provenance_lock"} <= idx, idx)
            log = {r[1]: r[4] for r in c.execute("PRAGMA table_info(embedding_upgrade_log)")}
            self.assertIsNone(log["model_name"])
            self.assertIsNone(log["embedding_dim"])
            self.assertIn("weights_hash", log)
            for t in ("memory_links", "memory_communities", "community_summaries"):
                self.assertIn("weights_hash", [r[1] for r in c.execute(f"PRAGMA table_info({t})")])
            mem = {r[1]: (r[3], r[4]) for r in c.execute("PRAGMA table_info(memories)")}
            self.assertEqual(mem["seed"], (1, "0"))
            self.assertEqual(mem["origin"], (0, None))
        finally:
            c.close()

    def test_a_second_apply_runs_none_of_them(self):
        db = self._new_db("fresh.db")
        sm.apply(str(db), MDIR)
        again = sm.apply(str(db), MDIR)
        self.assertTrue(set(NEW) <= set(again["skipped_already_applied"]), again["skipped_already_applied"])


class TestUpgradeFrom041(_Box):

    def test_control_the_migrator_derives_exactly_the_four_and_keeps_every_row(self):
        db = self._brain_at_041()
        r = sm.apply(str(db), MDIR)
        self.assertTrue(r["ok"], r["files_with_errors"])
        self.assertEqual([f for f in self._ran(r) if f < "migration_046"], sorted(NEW))
        c = sqlite3.connect(db)
        try:
            rows = c.execute("SELECT memory_id, model_id, model_ref, weights_hash, input_kind, inputs "
                             "FROM embedding_provenance ORDER BY memory_id").fetchall()
            self.assertEqual(rows, [(1, "old/model", "abc123", "unresolved", "text", None),
                                    (2, "old/model", "abc123", "unresolved", "text", None)])
            self.assertEqual(c.execute("SELECT id, model_name, embedding_dim, weights_hash FROM "
                                       "embedding_upgrade_log ORDER BY id").fetchall(),
                             [(1, "old/model", 1024, None), (2, "old/model", 1024, None)])
            self.assertEqual(c.execute("SELECT seq FROM sqlite_sequence WHERE name='embedding_upgrade_log'")
                             .fetchone()[0], 3, "the AUTOINCREMENT mark must survive the rebuild")
            self.assertEqual(c.execute("SELECT DISTINCT seed FROM memories").fetchall(), [(0,)])
            self.assertEqual(c.execute("PRAGMA integrity_check").fetchall(), [("ok",)])
            self.assertEqual(c.execute("PRAGMA foreign_key_check").fetchall(), [])
        finally:
            c.close()

    def test_both_paths_end_in_the_same_ddl_for_every_object_these_files_touch(self):
        fresh = self._new_db("fresh.db")
        sm.apply(str(fresh), MDIR)
        up = self._brain_at_041()
        sm.apply(str(up), MDIR)
        a, b = _touched_schema(fresh), _touched_schema(up)
        diff = sorted(str(k) for k in set(a) | set(b) if a.get(k) != b.get(k))
        self.assertEqual(diff, [], "path dependence in the objects 042-045 touch")

    def test_the_writer_that_leaned_on_013s_default_is_refused_loudly(self):
        db = self._brain_at_041(rows=False)
        sm.apply(str(db), MDIR)
        c = sqlite3.connect(db)
        try:
            with self.assertRaises(sqlite3.IntegrityError):
                c.execute("INSERT INTO embedding_upgrade_log (started_at, memories_total, status) "
                          "VALUES (datetime('now'), 1, 'in_progress')")
        finally:
            c.close()


class TestRefusals(_Box):

    def test_saboteur_a_provenance_table_with_a_column_042_does_not_know_is_refused_whole(self):
        db = self._brain_at_041()
        c = sqlite3.connect(db)
        c.execute("ALTER TABLE embedding_provenance ADD COLUMN extra TEXT")
        c.commit()
        before = c.execute("SELECT COUNT(*) FROM embedding_provenance").fetchone()[0]
        c.close()
        r = sm.apply(str(db), MDIR)
        bad = {e["file"] for e in r["files_with_errors"]}
        self.assertIn("migration_042_embedder_lock.sql", bad)
        self.assertTrue(any("eight_column_table_migration_042_knows" in str(e["errors"])
                            for e in r["files_with_errors"]), r["files_with_errors"])
        c = sqlite3.connect(db)
        try:
            cols = [r[1] for r in c.execute("PRAGMA table_info(embedding_provenance)")]
            self.assertIn("extra", cols)
            self.assertNotIn("weights_hash", cols)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM embedding_provenance").fetchone()[0], before)
            self.assertIsNone(c.execute("SELECT 1 FROM sqlite_master WHERE name='embedding_provenance_new'")
                              .fetchone())
        finally:
            c.close()


class TestWritePathEnsures042(_Box):

    def test_control_a_033_shaped_populated_sidecar_is_brought_to_042_by_the_writer(self):
        import memory_write as mw
        db = self._brain_at_041()
        c = sqlite3.connect(db)
        try:
            mw.ensure_provenance_table(c)
            c.commit()
            cols = [r[1] for r in c.execute("PRAGMA table_info(embedding_provenance)")]
            self.assertEqual(tuple(cols), mw.PROVENANCE_COLUMNS)
            self.assertEqual(c.execute("SELECT COUNT(*), MIN(weights_hash), MAX(weights_hash) "
                                       "FROM embedding_provenance").fetchone(), (2, "unresolved", "unresolved"))
            mw.ensure_provenance_table(c)            # idempotent
            self.assertEqual(c.execute("SELECT COUNT(*) FROM embedding_provenance").fetchone()[0], 2)
        finally:
            c.close()

    def test_the_writer_states_every_column_and_never_a_computed_looking_hash(self):
        import memory_write as mw
        self.assertEqual(set(mw.PROVENANCE_COLUMNS) & {"weights_hash", "input_kind", "inputs"},
                         {"weights_hash", "input_kind", "inputs"})
        self.assertEqual(mw.WEIGHTS_HASH_UNRESOLVED, "unresolved")


if __name__ == "__main__":
    unittest.main()
