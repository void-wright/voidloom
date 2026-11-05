"""A brain with memories reaches semantic recall by a printed route, and nothing embeds it unasked.

THE CLAIM UNDER TEST
  * `install/brain_is_new.py` is the one answer both installers read: no file, or only the seed pack and no vector, is new;
  * a brain older than the `seed` column marks the pack by its tag, so a pure-seed v1.0 brain is new;
  * anything an owner wrote, or any vector, is an existing brain;
  * the command an upgrade prints is quoted, absolute, and runs from another directory, under a path with a space.
"""
from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "organs"))

import schema_migrator  # noqa: E402


def ask(db: Path) -> str:
    return subprocess.run([sys.executable, str(ROOT / "install" / "brain_is_new.py"), str(db)], capture_output=True,
                          text=True, timeout=60).stdout.strip()


class WhatIsNew(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        self.d = Path(self.td.name)
        self.db = self.d / "claude_memory.db"

    def v1_brain(self):
        c = sqlite3.connect(self.db)
        c.executescript("""
            CREATE TABLE memories (id INTEGER PRIMARY KEY, content TEXT);
            CREATE TABLE memory_tags (memory_id INTEGER, tag TEXT);
            CREATE TABLE memory_embeddings_rowids (rowid INTEGER PRIMARY KEY);
            INSERT INTO memories VALUES (1, 'seed one'), (2, 'seed two');
            INSERT INTO memory_tags VALUES (1, 'seed-pack-agnostic'), (2, 'seed-pack-agnostic');""")
        c.commit()
        return c

    def test_control_no_file_is_new(self):
        self.assertEqual(ask(self.db), "yes")

    def test_saboteur_a_pure_seed_v1_brain_is_new(self):
        self.v1_brain().close()
        self.assertEqual(ask(self.db), "yes")

    def test_saboteur_one_owner_memory_on_a_v1_brain_is_not(self):
        c = self.v1_brain()
        c.execute("INSERT INTO memories VALUES (3, 'mine')")
        c.commit(); c.close()
        self.assertEqual(ask(self.db), "no")

    def test_saboteur_a_vector_is_not_new(self):
        c = self.v1_brain()
        c.execute("INSERT INTO memory_embeddings_rowids VALUES (1)")
        c.commit(); c.close()
        self.assertEqual(ask(self.db), "no")

    def test_control_what_cannot_be_read_counts_as_holding_something(self):
        self.db.write_bytes(b"this is not a database, and it is long enough to be read as one" * 40)
        self.assertEqual(ask(self.db), "no")


class ThePrintedRoute(unittest.TestCase):
    def test_saboteur_the_commands_are_quoted_for_a_path_with_a_space(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td) / "a folder"
            d.mkdir()
            db = d / "claude_memory.db"
            schema_migrator.apply(str(db), ROOT / "migrations")
            c = sqlite3.connect(db)
            c.execute("INSERT OR REPLACE INTO metadata (key, value) VALUES ('active_embedding_table', 'memory_embeddings')")
            c.commit(); c.close()
            import sqlite_vec
            c = sqlite3.connect(db); c.enable_load_extension(True); sqlite_vec.load(c)
            c.execute("CREATE VIRTUAL TABLE IF NOT EXISTS memory_embeddings USING vec0(memory_id INTEGER PRIMARY KEY, embedding float[768])")
            c.execute("INSERT INTO memories (content, summary, category, importance) VALUES ('mine', 's', 'insight', 0.5)")
            c.commit(); c.close()
            p = subprocess.run([sys.executable, str(ROOT / "organs" / "embed_backfill.py"), str(db), "--outstanding", "--json"],
                               capture_output=True, text=True, timeout=60)
            due = json.loads(p.stdout)
            self.assertEqual(due["without_a_vector"], 1)
            for key in ("embed_command", "verify_command"):
                self.assertIn(f"'{db}'", due[key], "an unquoted path fails where it is printed")
            dry = subprocess.run(due["embed_command"] + " --dry-run", shell=True, cwd="/", capture_output=True, text=True,
                                 timeout=60)
            self.assertEqual(dry.returncode, 0, dry.stdout + dry.stderr)


class TheDocsSayWhatHappens(unittest.TestCase):
    def read(self, rel):
        return (ROOT / rel).read_text(encoding="utf-8")

    def test_saboteur_the_readme_names_the_route_and_who_embeds(self):
        text = " ".join(self.read("README.md").split())
        self.assertIn("never is unasked", text)
        self.assertIn("the one command that embeds them", text)
        self.assertIn("on a brain with an install lock, a store refuses", text)

    def test_saboteur_no_doc_offers_a_fetch_the_installer_no_longer_does(self):
        self.assertNotIn("VOIDLOOM_PRECACHE_FALLBACK=1 ./install.sh", self.read("docs/ONBOARDING_GUIDE.md"))
        self.assertIn("never fetched by the installer", self.read("docs/ONBOARDING_GUIDE.md"))

    def test_saboteur_a_shipped_migration_is_not_called_absent(self):
        self.assertNotIn("REFERENCED BUT ABSENT", self.read("docs/RESTORE_MANIFEST.md"))
        self.assertTrue((ROOT / "migrations" / "migration_028_communities.sql").is_file())

    def test_saboteur_the_install_help_does_not_promise_an_embed(self):
        self.assertNotIn("every unembedded memory in the brain is", self.read("install.sh"))
        self.assertIn("Nothing already in the brain is embedded", self.read("install.sh"))

    def test_saboteur_the_migration_and_the_loader_agree_on_origin(self):
        self.assertNotIn("idempotency key", self.read("migrations/migration_044_seed_origin.sql"))


if __name__ == "__main__":
    unittest.main()
