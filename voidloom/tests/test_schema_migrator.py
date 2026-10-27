"""The destructive module nothing collected.

VAEL, Council on the cycle-3 scope: `organs/schema_migrator.py` is 538 lines of
SHIPPED code whose own docstring says four of the migrations it runs are
"DESTRUCTIVE on re-application -- 001, 022, 023 and 030 all DROP or DELETE", and
it was touched by ZERO of the 87 collected pytest ids. Its only exercisers were
two harnesses `pytest` does not collect and `install.sh` does not invoke.

Measured before this file existed:

    lines in schema_migrator.py            538
    migrations containing DROP/DELETE        4
    pytest ids mentioning it                 0
    test files referencing it                0

That is finding N3 ("nothing executes the binders") still standing over the
newest and most dangerous code in the tree -- N3's own repair bound the three
organ self-checks and missed this. The defect it guards against is not
hypothetical: the first draft of this module took `cross_tier_bridges` from 26
rows to 0 on a copy of the live brain while reporting `missing: 0` and rc 0. It
did not error. It succeeded, and succeeding was the damage.

These tests are seconds, not minutes, so they run on every `pytest` invocation.
"""
from __future__ import annotations

import shutil
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

BRAIN = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BRAIN / "organs"))
import schema_migrator as sm  # noqa: E402

BOXES = BRAIN / ".pytest_boxes"
LIVE = BRAIN / "claude_memory.db"


class TestMigratorIsNonDestructive(unittest.TestCase):

    def setUp(self):
        BOXES.mkdir(exist_ok=True)
        self.box = Path(tempfile.mkdtemp(prefix="migrator_", dir=str(BOXES)))
        self.db = self.box / "probe.db"

    def tearDown(self):
        shutil.rmtree(self.box, ignore_errors=True)

    def _fresh(self) -> Path:
        sqlite3.connect(self.db).close()
        sm.apply(str(self.db))
        return self.db

    def _counts(self) -> dict:
        c = sqlite3.connect(f"file:{self.db}?mode=ro", uri=True)
        out = {}
        for t in ("memories", "memory_tags", "cross_tier_bridges",
                  "decision_units", "edge_proposals"):
            try:
                out[t] = c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            except sqlite3.Error:
                out[t] = None
        c.close()
        return out

    def test_bootstrap_builds_a_brain_from_nothing(self):
        """Tier 0 must be able to CREATE a brain: `claude_memory.py init` needs
        sqlite_vec, so on the interpreter the Tier-0 promise names this is the
        only route."""
        self._fresh()
        audit = sm.audit(str(self.db))
        self.assertEqual(audit["missing"], [], audit)
        self.assertGreater(audit["brain_objects"], 20, audit)

    def test_reapply_is_a_clean_no_op(self):
        self._fresh()
        first = sm.apply(str(self.db))
        self.assertEqual(first["missing"], [], first)
        second = sm.apply(str(self.db))
        self.assertEqual(second["missing"], [], second)
        self.assertEqual(second["files_with_errors"], [], second)
        # every migration recognised as already applied, nothing re-executed
        self.assertEqual(second["statements_executed"], 0, second)
        self.assertEqual(len(second["skipped_already_applied"]),
                         second["migrations_on_disk"], second)

    def test_reapply_destroys_no_rows(self):
        """THE ONE THAT MATTERS. migration_022 purges cross_tier_bridges by
        design; re-running it unconditionally wiped 26 live rows and reported
        success. `already_applied` is decided from the WORLD -- the objects AND
        COLUMNS each migration introduces -- precisely so this cannot recur."""
        self._fresh()
        c = sqlite3.connect(self.db)
        c.execute("INSERT INTO cross_tier_bridges (local_memory_id, remote_tier,"
                  " remote_db_path, remote_memory_id, bridge_type)"
                  " VALUES (1,'eddie-windows','/x',1,'same_topic')")
        c.execute("INSERT INTO memories (content, category) VALUES ('probe','insight')")
        c.commit(); c.close()
        before = self._counts()
        self.assertEqual(before["cross_tier_bridges"], 1, before)
        for _ in range(3):
            sm.apply(str(self.db))
        self.assertEqual(self._counts(), before,
                         "re-applying migrations changed row counts")

    def test_a_failed_migration_is_atomic(self):
        """A migration that errors must leave NOTHING behind: 022's ALTER RENAME
        makes SQLite revalidate every view, and a dangling one aborted it AFTER
        the DROP had committed."""
        self._fresh()
        conn = sm.connect(str(self.db))
        before = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table','view')")}
        bad = self.box / "migration_999_bad.sql"
        bad.write_text("CREATE TABLE zzz_probe (a INTEGER);\n"
                       "INSERT INTO no_such_table_anywhere VALUES (1);\n",
                       encoding="utf-8")
        r = sm._run_file(conn, bad)
        after = {r2[0] for r2 in conn.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table','view')")}
        conn.close()
        self.assertTrue(r["errors"], r)
        self.assertEqual(r["applied"], 0, "a failed file reported work as kept")
        self.assertEqual(after, before,
                         "a failed migration left objects behind (not atomic)")

    def test_audit_reports_a_truncated_brain_as_incomplete(self):
        self._fresh()
        c = sqlite3.connect(self.db)
        c.execute("DROP TABLE IF EXISTS decision_units")
        c.commit(); c.close()
        audit = sm.audit(str(self.db))
        self.assertFalse(audit["complete"], audit)
        self.assertIn("decision_units", audit["missing"], audit)

    def test_statement_splitter_keeps_comment_led_statements(self):
        """The splitter dropped every statement preceded by a comment, which in
        this package is most of them: schema.sql parsed to 1 statement of 18."""
        base = sm.migrations_dir() / "schema.sql"
        n = len(list(sm.iter_statements(base.read_text(encoding="utf-8"))))
        self.assertGreater(n, 10, f"schema.sql parsed to only {n} statements")

    def test_bare_BEGIN_is_recognised_as_transaction_control(self):
        """`t.split()[0]` on 'BEGIN;' is the token 'begin;' -- semicolon
        attached -- so bare BEGIN leaked its transaction into the next file."""
        for stmt in ("BEGIN;", "BEGIN TRANSACTION;", "  begin;\n", "COMMIT;"):
            self.assertTrue(sm._is_txn_control(stmt), stmt)
        for stmt in ("CREATE TABLE beginning (a INT);", "SELECT 1;"):
            self.assertFalse(sm._is_txn_control(stmt), stmt)


@unittest.skipUnless(LIVE.exists(), "live brain not present")
class TestMigratorAgainstACopyOfTheLiveBrain(unittest.TestCase):
    """The shape the first draft got wrong, on the data it would have destroyed."""

    def test_apply_to_a_live_copy_changes_no_rows(self):
        BOXES.mkdir(exist_ok=True)
        box = Path(tempfile.mkdtemp(prefix="migrator_live_", dir=str(BOXES)))
        try:
            db = box / "live_copy.db"
            shutil.copy2(LIVE, db)
            def counts():
                c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
                o = {}
                for t in ("memories", "memory_tags", "cross_tier_bridges",
                          "decision_units", "decision_events", "edge_proposals"):
                    try:
                        o[t] = c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                    except sqlite3.Error:
                        o[t] = None
                c.close()
                return o
            before = counts()
            res = sm.apply(str(db))
            self.assertEqual(res["files_with_errors"], [], res)
            self.assertEqual(counts(), before,
                             "applying migrations to the live brain changed row counts")
        finally:
            shutil.rmtree(box, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
