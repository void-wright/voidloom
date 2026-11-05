"""An upgrade never embeds a brain unasked; a new brain gets its seed embedded at install.

On an existing brain the installer prints what is outstanding, with counts and the commands, and embeds nothing: at 30,000
memories a silent backfill is hours inside an installer, and on a locked brain it used to rewrite every legacy vector.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "organs"))

from voidloom.tests import test_tier_set as tt  # noqa: E402


def is_new(db: Path) -> str:
    text = (ROOT / "install.sh").read_text(encoding="utf-8")
    i = text.index("hd_brain_is_new() {")
    func = text[i:text.index("BRAIN_IS_NEW=", i)]
    script = f'PY_SYS="{sys.executable}"; PKG_ROOT="{ROOT}"; DB_PATH="{db}"\n{func}\nhd_brain_is_new\n'
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True).stdout.strip()


@unittest.skipUnless(tt._vec_available(), "needs sqlite_vec")
class WhatIsANewBrain(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        self.d = Path(self.td.name).resolve()

    def test_no_file_is_new(self):
        self.assertEqual(is_new(self.d / "absent.db"), "yes")

    def test_a_brain_with_vectors_is_not(self):
        self.assertEqual(is_new(tt.make_brain(self.d, "nomic")), "no")

    def test_a_keyword_brain_with_the_owners_memories_is_not(self):
        db = self.d / "b.db"
        c = sqlite3.connect(db)
        c.execute("CREATE TABLE memories (id INTEGER PRIMARY KEY, content TEXT, seed INTEGER DEFAULT 0)")
        c.execute("INSERT INTO memories (content) VALUES ('mine')")
        c.commit(); c.close()
        self.assertEqual(is_new(db), "no")

    def test_a_seed_only_brain_with_no_vector_is_new(self):
        db = self.d / "b.db"
        c = sqlite3.connect(db)
        c.execute("CREATE TABLE memories (id INTEGER PRIMARY KEY, content TEXT, seed INTEGER DEFAULT 0)")
        c.execute("INSERT INTO memories (content, seed) VALUES ('pack', 1)")
        c.commit(); c.close()
        self.assertEqual(is_new(db), "yes")


class TheInstallerGate(unittest.TestCase):
    def test_the_only_unlimited_backfill_is_behind_the_new_brain_test(self):
        text = (ROOT / "install.sh").read_text(encoding="utf-8")
        i = text.index('if [ "$BRAIN_IS_NEW" != "yes" ]; then')
        block = text[i:i + 900]
        self.assertLess(block.index("--outstanding"), block.index("seed pack embedded"))
        runs = re.findall(r'^\s*(?:if |elif .*; )?"\$VENV_PY" "\$\{BRAIN_DIR\}/organs/embed_backfill\.py" "\$DB_PATH"(.*)$', text, re.M)
        self.assertEqual(sorted(r.strip() for r in runs), ["--outstanding || true", "; then"])


class TheNightlyJob(unittest.TestCase):
    def test_it_embeds_what_has_no_vector_and_leaves_legacy_vectors_alone(self):
        roster = json.loads((ROOT / "install" / "cron_roster.json").read_text(encoding="utf-8"))
        self.assertIn("--only-missing", roster["units"]["embed-backfill"]["exec"])
        self.assertIn("--only-missing", (ROOT / "cron" / "wizard-library-embed-backfill.service").read_text(encoding="utf-8"))

    def test_the_windows_installer_keeps_the_same_rule(self):
        text = (ROOT / "install" / "install.ps1").read_text(encoding="utf-8")
        i = text.index("if (-not $BrainIsNew) {\n                    Say \"  this brain already existed")
        self.assertIn("--outstanding", text[i:i + 300])


@unittest.skipUnless(tt._vec_available(), "needs sqlite_vec")
class WhatItPrints(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        self.d = Path(self.td.name).resolve()
        self.db = tt.make_brain(self.d, "nomic", n=6, archived=())
        c = tt._connect(self.db)
        c.execute("UPDATE embedding_provenance SET weights_hash = 'unresolved' WHERE memory_id IN (1, 2)")
        c.execute("INSERT INTO memories (content) VALUES ('new and unembedded')")
        c.commit(); c.close()

    def _run(self, *argv):
        return subprocess.run([sys.executable, str(ROOT / "organs" / "embed_backfill.py"), str(self.db), *argv],
                              capture_output=True, text=True, env={**os.environ, "HOME": str(self.d)})

    def test_counts_and_commands_and_no_write(self):
        before = self.db.read_bytes()
        p = self._run("--outstanding", "--json")
        due = json.loads(p.stdout)
        self.assertEqual((due["without_a_vector"], due["legacy_unverified"], due["legacy_of"]), (1, 2, 6), p.stdout)
        self.assertIn("--only-missing", due["embed_command"])
        self.assertIn("re-embed", due["verify_command"])
        self.assertTrue(due["embed_command"].startswith(sys.executable))
        self.assertEqual(self.db.read_bytes(), before)

    def test_the_words_say_to_embed_not_unembedded(self):
        p = self._run("--dry-run", "--only-missing")
        self.assertIn("to embed    : 1 before", p.stdout)
        self.assertNotIn("unembedded  :", p.stdout)

    def test_only_missing_leaves_a_legacy_vector_alone(self):
        import embed_backfill
        c = tt._connect(self.db)
        self.assertEqual(embed_backfill.count_unembedded(c, "memory_embeddings", only_missing=True), 1)
        self.assertEqual(embed_backfill.count_unembedded(c, "memory_embeddings"), 3)
        c.close()


if __name__ == "__main__":
    unittest.main()
