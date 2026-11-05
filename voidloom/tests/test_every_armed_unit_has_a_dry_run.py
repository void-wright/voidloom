"""Every armed unit answers `--dry-run`: its declared ExecStart, plus the flag, exits 0 and writes nothing.

Read straight from the shipped service files and the roster, so a unit added later is held to the same line, against a
real schema. Each plan's counts come from the brain; a stub that printed ok would fail on `writes` or on the brain's bytes.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "organs"))

import schema_migrator  # noqa: E402

try:
    import sqlite_vec  # noqa: F401
    HAVE_VEC = True
except Exception:
    HAVE_VEC = False

UNITS = ("affect-probe", "anti-recency", "belief-scan", "cognitive-map", "dream-daytime", "dreaming", "gap-mapping",
         "gap-research", "organ-run-log", "organ-yield", "pre-cognitive", "self-eval")


def exec_start(unit: str, home: Path) -> list:
    text = (ROOT / "cron" / f"wizard-library-{unit}.service").read_text(encoding="utf-8").replace("\\\n", " ")
    line = next(ln for ln in text.splitlines() if ln.startswith("ExecStart="))[len("ExecStart="):]
    return shlex.split(line.replace("%h", str(home)))


@unittest.skipUnless(HAVE_VEC, "needs sqlite_vec")
class EveryUnit(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.td = tempfile.TemporaryDirectory()
        cls.home = Path(cls.td.name).resolve()
        cls.brain = cls.home / ".claude" / "memory"
        cls.brain.mkdir(parents=True)
        for name in ("organs", "migrations", "install", "cron"):
            (cls.brain / name).symlink_to(ROOT / name)
        cls.db = cls.brain / "claude_memory.db"
        schema_migrator.apply(str(cls.db), ROOT / "migrations")
        c = schema_migrator.connect(str(cls.db))
        c.execute("INSERT INTO memories (content, summary, category, importance) VALUES ('a live memory', 's', 'insight', 0.5)")
        c.commit()
        c.close()

    @classmethod
    def tearDownClass(cls):
        cls.td.cleanup()

    def _files(self):
        return sorted(str(p.relative_to(self.home)) for p in self.home.rglob("*")
                      if p.is_file() and "organs" not in p.parts and "__pycache__" not in p.parts
                      and not p.name.endswith(("-wal", "-shm")))

    def _rows(self):
        """Every ordinary table's content, hashed: closing a connection may checkpoint the log, which changes the file
        and no fact in it."""
        c = sqlite3.connect(str(self.db))
        try:
            out = {}
            for (name,) in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' "
                                     "AND sql NOT LIKE 'CREATE VIRTUAL%' AND name NOT LIKE 'memories_fts%' "
                                     "AND name NOT LIKE 'memory_embeddings%'").fetchall():
                out[name] = hashlib.sha256(repr(c.execute(f'SELECT * FROM "{name}"').fetchall()).encode()).hexdigest()
            return out
        finally:
            c.close()

    def _dry(self, unit):
        argv = exec_start(unit, self.home)
        argv = [sys.executable if a == "python3" else a for a in argv if a != "/usr/bin/env"]
        env = {k: v for k, v in os.environ.items() if not k.startswith(("WIZARD_", "VOIDLOOM_", "CLAUDE_"))}
        env.update(HOME=str(self.home), WIZARD_LIBRARY_DB=str(self.db), PYTHONDONTWRITEBYTECODE="1")
        return subprocess.run(argv + ["--dry-run"], capture_output=True, text=True, timeout=180, env=env, cwd=str(self.home))

    def test_every_unit(self):
        for unit in UNITS:
            with self.subTest(unit=unit):
                before = self._rows(), self._files()
                p = self._dry(unit)
                self.assertEqual(p.returncode, 0, (unit, p.stdout[-400:], p.stderr[-400:]))
                self.assertEqual((self._rows(), self._files()), before, unit)

    def test_a_plan_reports_a_count_from_the_brain(self):
        p = self._dry("anti-recency")
        out = json.loads(p.stdout.strip().splitlines()[-1])
        self.assertEqual((out["writes"], out["counts"]["live_memories"]), (0, 1), out)
        self.assertEqual(out["counts"]["resurfaced_so_far"], 0)

    def test_a_table_it_cannot_read_is_null_not_zero(self):
        import dry_plan
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertEqual(dry_plan.plan("x", self.db, "w", {"absent": "SELECT COUNT(*) FROM no_such_table"}), 0)
        self.assertIsNone(json.loads(buf.getvalue())["counts"]["absent"])

    def test_no_brain_is_an_error(self):
        import dry_plan
        import contextlib
        import io
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(dry_plan.plan("x", self.home / "absent.db", "w"), 1)


if __name__ == "__main__":
    unittest.main()
