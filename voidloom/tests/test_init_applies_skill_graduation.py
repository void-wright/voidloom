# -*- coding: utf-8 -*-
"""`init` adds what the armed skill-graduation unit needs.

migration_007's column is added programmatically, so the schema sweep cannot reach
it, and the weekly unit refused rc 3 on every clean install until someone applied
the migration by hand. The real `init` in a child, then the unit's own command line.
"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(os.environ.get("VOIDLOOM_ROOT") or Path(__file__).resolve().parents[2]).resolve()
ORGANS = ROOT / "organs"


class InitAppliesGraduation(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)
        self.db = self.d / "claude_memory.db"

    def tearDown(self):
        self.tmp.cleanup()

    def env(self):
        return {**{k: v for k, v in os.environ.items() if k not in ("WIZARD_LIBRARY_DB", "WIZARD_EMBED_MODEL")},
                "PYTHONDONTWRITEBYTECODE": "1", "HOME": str(self.d)}

    def init(self):
        child = ("import sys, pathlib\n"
                 f"sys.path.insert(0, {str(ORGANS)!r})\n"
                 "import claude_memory as cm\n"
                 f"cm.DB_PATH = pathlib.Path({str(self.db)!r})\n"
                 "cm.main()\n")
        r = subprocess.run([sys.executable, "-c", child, "init"], capture_output=True, text=True, timeout=300,
                           env=self.env(), cwd=str(self.d))
        self.assertEqual(r.returncode, 0, r.stdout[-500:] + r.stderr[-500:])
        return json.loads(r.stdout.strip().splitlines()[-1])

    def graduate(self):
        return subprocess.run([sys.executable, str(ORGANS / "skill_graduation.py"), str(self.db), "--run",
                               "--graduated-by", "cron"], capture_output=True, text=True, timeout=120, env=self.env())

    def columns(self):
        c = sqlite3.connect(str(self.db))
        try:
            return {r[1] for r in c.execute("PRAGMA table_info(memories)")}
        finally:
            c.close()

    def test_control_a_clean_init_adds_the_column_and_says_so(self):
        out = self.init()
        self.assertIn("skill_tier", self.columns())
        self.assertIn(out["graduation_migration"].get("status"), ("ok", "applied"), out["graduation_migration"])

    def test_control_the_armed_unit_runs_on_a_clean_install(self):
        self.init()
        r = self.graduate()
        self.assertEqual(r.returncode, 0, r.stdout[-500:] + r.stderr[-500:])
        self.assertNotIn("graduation_schema_absent", r.stdout)

    def test_saboteur_without_the_column_the_unit_refuses(self):
        self.init()
        c = sqlite3.connect(str(self.db))
        c.execute("DROP INDEX IF EXISTS idx_memories_skill_tier")
        c.execute("ALTER TABLE memories DROP COLUMN skill_tier")
        c.commit()
        c.close()
        r = self.graduate()
        self.assertEqual(r.returncode, 3, r.stdout[-400:])
        self.assertIn("graduation_schema_absent", r.stdout)

    def test_control_a_second_init_changes_nothing_and_an_upgraded_brain_gets_the_column(self):
        self.init()
        c = sqlite3.connect(str(self.db))
        c.execute("DROP INDEX IF EXISTS idx_memories_skill_tier")
        c.execute("ALTER TABLE memories DROP COLUMN skill_tier")
        c.commit()
        c.close()
        self.init()
        self.assertIn("skill_tier", self.columns())
        self.assertEqual(self.graduate().returncode, 0)
        out = self.init()
        self.assertNotEqual(out["graduation_migration"].get("status"), "error", out["graduation_migration"])


if __name__ == "__main__":
    unittest.main()
