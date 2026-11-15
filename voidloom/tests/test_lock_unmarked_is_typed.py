"""A lock written without the brain's own record of it is said in the verb's answer, typed, never only on stderr.

The row `mark_brain` writes is what tells a lost lock from a brain that never had one once both files are gone. When
the brain would not take it (busy, damaged, read-only), the failure was printed to stderr and the verb answered a plain
ok.
  * saboteur: `mark_brain` on a brain that cannot be written answers a typed note, LOCK_UNMARKED, and prints nothing;
    `write_lock` carries the note beside the lock it wrote;
  * saboteur: `init --tier cpu` on such a brain writes the lock and answers status ok with degraded true, code
    init_degraded and the note at the top of its envelope;
  * saboteur: a `tier set` lock move whose record cannot be written is refused, typed, inside its transaction;
  * control: run again once the brain is writable, the same verb writes the row and answers a plain ok, and the record
    says a lock was written.
"""
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(os.environ.get("VOIDLOOM_ROOT") or Path(__file__).resolve().parents[2]).resolve()
sys.path.insert(0, str(ROOT / "organs"))

import embedder_lock as el  # noqa: E402
import schema_migrator  # noqa: E402


class TheUnmarkedLockIsTyped(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory(prefix="vl_unmarked_")
        self.addCleanup(self.td.cleanup)
        self.d = Path(self.td.name)
        self.db = self.d / "claude_memory.db"
        out = schema_migrator.apply(str(self.db), ROOT / "migrations")
        self.assertEqual(out.get("failed") or [], [], out)
        self.addCleanup(lambda: os.chmod(self.db, 0o644))

    def mark(self):
        c = sqlite3.connect(f"file:{self.db}?mode=ro", uri=True)
        try:
            row = c.execute("SELECT value FROM metadata WHERE key = ?", (el.BRAIN_LOCK_KEY,)).fetchone()
        finally:
            c.close()
        return row[0] if row else None

    def test_saboteur_mark_brain_answers_a_typed_note_on_a_brain_it_cannot_write(self):
        import contextlib
        import io
        os.chmod(self.db, 0o444)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            note = el.mark_brain(self.db, "cpu")
        self.assertEqual((note or {}).get("code"), "LOCK_UNMARKED", note)
        self.assertEqual(err.getvalue(), "", "the failure went to stderr instead of the answer")
        lock = el.write_lock(self.d, "cpu", brain=self.db)
        self.assertEqual(lock["_unmarked"]["code"], "LOCK_UNMARKED")

    def test_saboteur_a_tier_move_whose_record_cannot_be_written_is_refused_typed(self):
        import tier_set
        with self.assertRaises(tier_set.TierRefused) as cm:
            tier_set._unmarked(el.mark_brain(self.d / "no_such.db", "cpu") or {"code": "LOCK_UNMARKED", "message": "x"})
        self.assertEqual(cm.exception.code, "LOCK_UNMARKED")
        tier_set._unmarked(None)                                # a written row moves nothing

    def init(self):
        env = {k: v for k, v in os.environ.items() if k not in ("WIZARD_LIBRARY_DB", "WIZARD_EMBED_MODEL")}
        env.update({"PYTHONDONTWRITEBYTECODE": "1", "HF_HUB_OFFLINE": "1"})
        code = ("import sys; sys.path.insert(0, sys.argv[1]); import claude_memory as cm; from pathlib import Path; "
                "cm.DB_PATH = Path(sys.argv[2]); sys.argv = ['claude_memory.py', 'init', '--tier', 'cpu']; cm.main()")
        p = subprocess.run([sys.executable, "-B", "-c", code, str(ROOT / "organs"), str(self.db)], capture_output=True,
                           text=True, env=env, timeout=300)
        out = p.stdout.strip()
        return p.returncode, (json.loads(out[out.rindex("\n{") + 1:] if "\n{" in out else out) if out else {}), p.stderr

    def refuse_the_record(self, on=True):
        """The planted fault: a brain that takes every write but the one row that records a lock."""
        c = sqlite3.connect(str(self.db))
        c.execute("DROP TRIGGER IF EXISTS planted_no_mark")
        if on:
            c.execute(f"CREATE TRIGGER planted_no_mark BEFORE INSERT ON metadata WHEN NEW.key = '{el.BRAIN_LOCK_KEY}' "
                      "BEGIN SELECT RAISE(ABORT, 'planted: the brain will not take the record'); END")
        c.commit()
        c.close()

    def test_saboteur_init_says_lock_unmarked_at_the_top_and_a_rerun_writes_the_row(self):
        self.refuse_the_record()
        rc, env, err = self.init()
        self.assertEqual((env.get("status"), env.get("degraded"), env.get("code")), ("ok", True, "init_degraded"),
                         (rc, env, err[-600:]))
        self.assertEqual(env["lock_unmarked"]["code"], "LOCK_UNMARKED")
        self.assertNotIn("its row in the brain is not", err)
        self.assertTrue((self.d / el.LOCK_NAME).is_file(), "the lock itself was not written")
        self.assertIsNone(self.mark())
        self.refuse_the_record(on=False)
        rc, env, err = self.init()
        self.assertEqual((rc, env.get("status"), env.get("degraded")), (0, "ok", None), (env, err[-600:]))
        self.assertEqual(self.mark(), "cpu")
        self.assertEqual(el.lost_lock(self.d, self.db), {"tier": "cpu"})


if __name__ == "__main__":
    unittest.main()
