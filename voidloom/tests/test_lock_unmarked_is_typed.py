"""A lock written without the brain's own record of it is said in the verb's answer, typed, never only on stderr.

The row `mark_brain` writes is what tells a lost lock from a brain that never had one once both files are gone. When
the brain would not take it (busy, damaged, read-only), the failure was printed to stderr and the verb answered ok.
  * saboteur: `mark_brain` on a brain that cannot be written refuses LOCK_UNMARKED, typed;
  * saboteur: `init --tier cpu` on such a brain writes the lock and answers LOCK_UNMARKED, typed, exit 1;
  * control: run again once the brain is writable, the same verb writes the row and answers ok, and the record says
    a lock was written.
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

    def test_saboteur_mark_brain_refuses_typed_on_a_brain_it_cannot_write(self):
        os.chmod(self.db, 0o444)
        with self.assertRaises(el.EmbedderLockError) as cm:
            el.mark_brain(self.db, "cpu")
        self.assertEqual(cm.exception.code, "LOCK_UNMARKED")

    def init(self):
        env = {k: v for k, v in os.environ.items() if k not in ("WIZARD_LIBRARY_DB", "WIZARD_EMBED_MODEL")}
        env.update({"PYTHONDONTWRITEBYTECODE": "1", "HF_HUB_OFFLINE": "1"})
        code = ("import sys; sys.path.insert(0, sys.argv[1]); import claude_memory as cm; from pathlib import Path; "
                "cm.DB_PATH = Path(sys.argv[2]); sys.argv = ['claude_memory.py', 'init', '--tier', 'cpu']; cm.main()")
        p = subprocess.run([sys.executable, "-B", "-c", code, str(ROOT / "organs"), str(self.db)], capture_output=True,
                           text=True, env=env, timeout=300)
        out = p.stdout.strip()
        return p.returncode, (json.loads(out[out.rindex("\n{") + 1:] if "\n{" in out else out) if out else {}), p.stderr

    def test_saboteur_init_answers_lock_unmarked_typed_and_a_rerun_writes_the_row(self):
        os.chmod(self.db, 0o444)
        rc, env, err = self.init()
        self.assertEqual((rc, env.get("status"), env.get("error")), (1, "error", "LOCK_UNMARKED"), (env, err[-600:]))
        self.assertTrue((self.d / el.LOCK_NAME).is_file(), "the lock itself was not written")
        self.assertIsNone(self.mark())
        os.chmod(self.db, 0o644)
        rc, env, err = self.init()
        self.assertEqual((rc, env.get("status")), (0, "ok"), (env, err[-600:]))
        self.assertEqual(self.mark(), "cpu")
        self.assertEqual(el.lost_lock(self.d, self.db), {"tier": "cpu"})


if __name__ == "__main__":
    unittest.main()
