"""A brain with memories reaches semantic recall by a printed route, and nothing embeds it unasked.

THE CLAIM UNDER TEST
  * `install/brain_is_new.py` is the one answer both installers read: no file, or only the seed pack and no vector, is new;
  * a brain older than the `seed` column marks the pack by its tag, so a pure-seed v1.0 brain is new;
  * anything an owner wrote, or any vector, is an existing brain;
  * the command an upgrade prints is quoted, absolute, and runs from another directory, under a path with a space.
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


class ACommandPrintedOnWindows(unittest.TestCase):
    def test_saboteur_powershell_calls_it_rather_than_echoing_it(self):
        import embedder_lock as el
        got = el.shell_command(r"C:\a b\python.exe", r"C:\x\embed_backfill.py", "--only-missing", platform="win32")
        self.assertEqual(got, r"& 'C:\a b\python.exe' 'C:\x\embed_backfill.py' '--only-missing'")

    def test_saboteur_a_dollar_a_backtick_and_a_quote_in_a_path_stay_literal(self):
        import embedder_lock as el
        word = "C:\\Users\\you\\o'neil\\$env:PATH `n brain"
        self.assertEqual(el._ps_quote(word), "'" + word.replace("'", "''") + "'")
        ps = "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"
        if not os.path.isfile(ps):
            self.skipTest("no powershell.exe reachable: the quoting is read, not run")
        got = subprocess.run([ps, "-NoProfile", "-Command", "Write-Output " + el._ps_quote(word)], capture_output=True, cwd="/",
                             text=True, timeout=60)
        self.assertEqual(got.stdout.rstrip("\r\n"), word, got.stderr)

    def test_control_a_posix_shell_gets_posix_quoting(self):
        import embedder_lock as el
        self.assertEqual(el.shell_command("/a b/python3", "--x", platform="linux"), "'/a b/python3' --x")


class ThePipFailureHint(unittest.TestCase):
    """The installer's own lines, run with a pip that fails: the hint reads this run's reason."""

    def chain(self, d: Path, *, pip_rc: int) -> str:
        src = (ROOT / "install.sh").read_text(encoding="utf-8")
        i = src.index(': > "${VENV_DIR}/pip-install.err"')
        j = src.index("; then\n", src.index('"$VENV_PY" -m pip install "${PIP_ARGS[@]}"', i))
        pip = d / "pip"
        pip.write_text(f'#!/bin/sh\necho "this run: pip said no" >&2\nexit {pip_rc}\n')
        pip.chmod(0o755)
        (d / "pip-install.err").write_text("a reason from an earlier run\n")
        script = (f'VENV_DIR="{d}"; VENV_PY="{pip}"; PIP_ARGS=(-r x); torch_into_venv() {{ return 0; }}\n'
                  + src[i:j].replace('if "$VENV_PY"', '"$VENV_PY"', 1) + "\necho rc=$?\n")
        return subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=60).stdout

    def test_saboteur_a_stale_reason_is_not_printed_as_this_runs(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            self.assertIn("rc=1", self.chain(d, pip_rc=1))
            got = (d / "pip-install.err").read_text()
        self.assertNotIn("earlier run", got)
        self.assertIn("this run: pip said no", got)


class TheVerifyGatesWord(unittest.TestCase):
    """install.sh's own verify block, run: EMBED_OWED on stdout is the one failure that means nothing here has a vector."""

    def verify(self, d: Path):
        src = (ROOT / "install.sh").read_text(encoding="utf-8")
        i = src.index("<<'T1_VERIFY_PY'\n") + len("<<'T1_VERIFY_PY'\n")
        script = d / "verify.py"
        script.write_text(src[i:src.index("\nT1_VERIFY_PY\n", i)] + "\n", encoding="utf-8")
        return subprocess.run([sys.executable, "-B", str(script)], capture_output=True, text=True, timeout=120,
                              env={**os.environ, "WIZARD_LIBRARY_DB": str(d / "claude_memory.db"), "BRAIN_DIR": str(ROOT),
                                   "ORGANS_DIR": str(ROOT / "organs")})

    def brain(self, d: Path):
        import embedder_lock as el
        db = d / "claude_memory.db"
        schema_migrator.apply(str(db), ROOT / "migrations")
        c = schema_migrator.connect(str(db))
        c.execute("CREATE VIRTUAL TABLE IF NOT EXISTS memory_embeddings USING vec0(memory_id INTEGER PRIMARY KEY, embedding float[768])")
        c.execute("INSERT OR REPLACE INTO metadata (key, value) VALUES ('active_embedding_table', 'memory_embeddings')")
        c.execute("INSERT INTO memories (content, summary, category, importance) VALUES ('a note', 's', 'insight', 0.5)")
        c.commit()
        el.write_lock(d, "cpu", brain=c)
        c.commit()
        return c

    def test_saboteur_no_vector_at_all_is_what_is_owed(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td).resolve()
            self.brain(d).close()
            p = self.verify(d)
        self.assertEqual((p.returncode, p.stdout.strip()), (1, "EMBED_OWED"), p.stderr[-400:])

    def test_saboteur_no_vector_on_a_brain_with_no_lock_is_what_is_owed_too(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td).resolve()
            self.brain(d).close()
            (d / "embedder.lock.json").unlink()
            (d / "embedder.lock.history.jsonl").unlink()
            c = sqlite3.connect(d / "claude_memory.db"); c.execute("DELETE FROM metadata WHERE key = 'embedder_lock_written'"); c.commit(); c.close()
            p = self.verify(d)
        self.assertEqual((p.returncode, p.stdout.strip()), (1, "EMBED_OWED"), p.stderr[-400:])

    def test_control_vectors_the_lock_refuses_are_a_different_fault(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td).resolve()
            c = self.brain(d)
            c.execute("INSERT INTO memory_embeddings (memory_id, embedding) VALUES (1, ?)", (bytes(4 * 768),))
            c.execute("INSERT INTO embedding_provenance (memory_id, vec_table, model_id, model_ref, weights_hash, dim, content_hash, "
                      "embedded_at, organ, input_kind) VALUES (1, 'memory_embeddings', 'm', 'r', ?, 768, ?, 't', 'o', 'text')",
                      ("f" * 64, "c" * 64))
            c.commit(); c.close()
            p = self.verify(d)
        self.assertEqual((p.returncode, p.stdout.strip()), (1, ""), p.stderr[-400:])


class TheHintOnALockedBrainWithNoModel(unittest.TestCase):
    def hint(self, reason: str, *, locked: bool) -> str:
        src = (ROOT / "install.sh").read_text(encoding="utf-8")
        i = src.index("tier1_upgrade_hint() {\n")
        j = src.index("\n}\n", src.index("HINT\n}", i) - 1) + 3
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "claude_memory.db"
            db.write_bytes(b"")
            if locked:
                (Path(td) / "embedder.lock.json").write_text("{}")
            script = (f'PY_SYS={sys.executable}; DB_PATH="{db}"; EMBED_MODEL=nomic; EMBED_DIM=768; VENV_DIR=v; VENV_PY=p; REQ_FILE=r\n'
                      "real_path() { \"$PY_SYS\" -c 'import os, sys; print(os.path.realpath(sys.argv[1]))' \"$1\"; }\n"
                      + src[i:j] + f'\ntier1_upgrade_hint "{reason}"\n')
            return subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=60).stdout

    def test_saboteur_it_says_a_store_refuses(self):
        self.assertIn("a store refuses", self.hint("the models could not be fetched (MODELS_ABSENT)", locked=True))

    def test_control_a_lockless_brain_is_not_told_so(self):
        self.assertNotIn("a store refuses", self.hint("the models could not be fetched (MODELS_ABSENT)", locked=False))


class AVenvAFailedAttemptLeft(unittest.TestCase):
    """The installer's own lines, run: a venv whose interpreter is gone or whose pip does not answer is rebuilt."""

    def clear(self, venv_py: Path) -> str:
        src = (ROOT / "install.sh").read_text(encoding="utf-8")
        i = src.index('    _venv_clear=""\n')
        j = src.index('\n', src.index('_venv_clear="--clear"', i))
        script = f'VENV_PY="{venv_py}"\n{src[i:j]}\necho "[$_venv_clear]"\n'
        return subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=60).stdout.strip()

    def test_saboteur_an_interpreter_that_is_gone_is_rebuilt(self):
        with tempfile.TemporaryDirectory() as td:
            py = Path(td) / "python3"
            py.symlink_to("/nonexistent/python3.14")
            self.assertEqual(self.clear(py), "[--clear]")

    def test_control_no_venv_yet_and_a_working_one_are_left_alone(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(self.clear(Path(td) / "python3"), "[]")
        self.assertEqual(self.clear(Path(sys.executable)), "[]")


class TheDocsHoldTheseSentences(unittest.TestCase):
    """Words read as words: a sentence present is not a sentence true. A figure in a doc is derived here, never matched."""

    def read(self, rel):
        return (ROOT / rel).read_text(encoding="utf-8")

    def test_saboteur_the_readme_names_the_route_and_who_embeds(self):
        text = " ".join(self.read("README.md").split())
        self.assertIn("never is unasked", text)
        self.assertIn("the one command that embeds them", text)
        self.assertIn("On a brain with an install lock, `voidloom store` and the engine's `store` refuse", text)

    def test_saboteur_no_doc_offers_a_fetch_the_installer_no_longer_does(self):
        self.assertNotIn("VOIDLOOM_PRECACHE_FALLBACK=1 ./install.sh", self.read("docs/ONBOARDING_GUIDE.md"))
        self.assertIn("never fetched by the installer", self.read("docs/ONBOARDING_GUIDE.md"))

    def test_saboteur_the_count_of_migrations_the_code_names_is_the_count_on_disk(self):
        """Derived both ways, so a number typed into the doc cannot pass on a word beside it."""
        text = " ".join(self.read("docs/RESTORE_MANIFEST.md").split())
        tree = {p.name for p in (ROOT / "migrations").glob("migration_*.sql")}
        named = set()
        for py in (ROOT / "organs").rglob("*.py"):
            named |= set(re.findall(r"migration_\d{3}[a-z0-9_]*\.sql", py.read_text(encoding="utf-8", errors="replace")))
        said = re.search(r"The tree ships (\d+) `migration_\*\.sql` and the code names (\d+) of them by file", text)
        self.assertIsNotNone(said, "the sentence moved: read the doc and this test together")
        self.assertEqual((int(said.group(1)), int(said.group(2))), (len(tree), len(named & tree)))
        self.assertIn("`schema_migrator` applies them all", text)

    def test_saboteur_the_readme_says_which_door_answers_when_a_model_is_gone(self):
        text = " ".join(self.read("README.md").split())
        self.assertIn("`voidloom recall` and the engine's `recall` answer by keyword", text)
        self.assertIn("`voidloom store` and the engine's `store` refuse", text)
        self.assertIn("`claude_memory_lite.py` is the floor and keeps storing", text)

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
