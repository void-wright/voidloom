"""Nothing re-points an existing brain but `tier set`.

An install flag, `--force-cpu` or a shell WIZARD_EMBED_MODEL never changes the tier of a brain that holds vectors, lock or
no lock, and an unshipped tier's brain included: moving a brain means re-embedding every memory, and that has one verb.
Each surface refuses and names the route; a brain with nothing embedded has nothing to protect, so the choice stands.
"""
from __future__ import annotations

import hashlib
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "organs"))

from voidloom.tests.test_install_stops_and_says_why import HAVE_VEC, _Home  # noqa: E402

TABLES = {"nomic": ("memory_embeddings", 768), "jina": ("memory_embeddings_v2", 1024), "qwen": ("memory_embeddings_v3", 1024)}


def plant_brain(d: Path, tier: str, *, rows: int = 3) -> Path:
    """A lockless brain declaring `tier`, holding `rows` vectors in that tier's table."""
    import sqlite_vec
    d.mkdir(parents=True, exist_ok=True)
    table, dim = TABLES[tier]
    db = d / "claude_memory.db"
    c = sqlite3.connect(db)
    c.enable_load_extension(True); sqlite_vec.load(c); c.enable_load_extension(False)
    c.executescript(f"""
        CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT, updated_at TEXT);
        CREATE TABLE memories (id INTEGER PRIMARY KEY AUTOINCREMENT, content TEXT NOT NULL);
        CREATE VIRTUAL TABLE {table} USING vec0(memory_id INTEGER PRIMARY KEY, embedding float[{dim}]);
    """)
    c.execute("INSERT INTO metadata(key, value) VALUES ('active_embedding_table', ?)", (table,))
    for i in range(1, rows + 1):
        c.execute("INSERT INTO memories(id, content) VALUES (?, ?)", (i, f"m{i}"))
        c.execute(f"INSERT INTO {table}(memory_id, embedding) VALUES (?, ?)", (i, bytes(4 * dim)))
    c.commit(); c.close()
    return db


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


@unittest.skipUnless(HAVE_VEC, "needs an interpreter with sqlite_vec to plant the vectors")
class TheInstallerRefusesBeforeItTouchesAnything(_Home):
    def setUp(self):
        super().setUp()
        self.path = "%s:%s" % (Path(sys.executable).parent, self.path)

    def _refused(self, p, db, before):
        out = p.stdout + p.stderr
        self.assertEqual(p.returncode, 2, out[-1500:])
        self.assertIn("voidloom tier set", p.stderr)
        self.assertIn("Nothing was touched", p.stderr)
        self.assertEqual(sha(db), before)
        self.assertFalse((db.parent / "embedder.lock.json").exists())

    def test_force_cpu_does_not_move_a_jina_brain(self):
        db = plant_brain(self.brain, "jina")
        self._refused(self._run("--force-cpu"), db, sha(db))

    def test_force_cpu_does_not_move_a_lockless_qwen_brain(self):
        db = plant_brain(self.brain, "qwen")
        self._refused(self._run("--force-cpu"), db, sha(db))

    def test_a_shell_variable_does_not_move_a_jina_brain(self):
        db = plant_brain(self.brain, "jina")
        self._refused(self._run(WIZARD_EMBED_MODEL="nomic"), db, sha(db))

    def test_control_an_agreeing_variable_passes_the_gate(self):
        db = plant_brain(self.brain, "jina")
        p = self._run("--dry-run", WIZARD_EMBED_MODEL="jina")
        self.assertNotIn("does not change a brain's model", p.stderr)

    def test_control_a_brain_with_nothing_embedded_may_still_choose(self):
        db = plant_brain(self.brain, "jina", rows=0)
        p = self._run("--dry-run", "--force-cpu")
        self.assertNotIn("does not change a brain's model", p.stderr)


@unittest.skipUnless(HAVE_VEC, "needs sqlite_vec")
class TheEngineRefuses(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        self.d = Path(self.td.name).resolve()
        for name in ("organs", "migrations"):        # the engine finds its brain beside its own organs/
            (self.d / name).symlink_to(ROOT / name)

    def _engine(self, *argv, model=None):
        env = {k: v for k, v in os.environ.items() if k != "WIZARD_EMBED_MODEL"}
        env["WIZARD_LIBRARY_DB"] = str(self.d / "claude_memory.db")
        env["HOME"] = str(self.d)
        if model:
            env["WIZARD_EMBED_MODEL"] = model
        return subprocess.run([sys.executable, str(self.d / "organs" / "claude_memory.py"), *argv], env=env,
                              capture_output=True, text=True, cwd=str(self.d))

    def test_init_refuses_a_variable_against_a_held_tier_and_writes_no_lock(self):
        db = plant_brain(self.d, "jina")
        before = sha(db)
        p = self._engine("init", model="nomic")
        self.assertNotEqual(p.returncode, 0, p.stdout)
        self.assertIn("WEIGHTS_MISMATCH", p.stdout + p.stderr)
        self.assertIn("tier set", p.stdout + p.stderr)
        self.assertFalse((self.d / "embedder.lock.json").exists())
        self.assertEqual(sha(db), before)

    def test_init_refuses_the_flag_too(self):
        plant_brain(self.d, "qwen")
        p = self._engine("init", "--tier", "cpu")
        self.assertIn("WEIGHTS_MISMATCH", p.stdout + p.stderr)
        self.assertFalse((self.d / "embedder.lock.json").exists())

    def test_a_read_refuses_rather_than_search_another_models_table(self):
        plant_brain(self.d, "jina")
        code = ("import sys; sys.path.insert(0, sys.argv[1]); import claude_memory as c\n"
                "c.DB_PATH = c.Path(sys.argv[2])\n"
                "try:\n    print(c._active_embed_model())\n"
                "except Exception as e:\n    print(getattr(e, 'code', repr(e)))\n")
        env = {**os.environ, "WIZARD_LIBRARY_DB": str(self.d / "claude_memory.db"), "WIZARD_EMBED_MODEL": "nomic"}
        p = subprocess.run([sys.executable, "-c", code, str(ROOT / "organs"), str(self.d / "claude_memory.db")], env=env,
                           capture_output=True, text=True)
        self.assertEqual(p.stdout.strip(), "WEIGHTS_MISMATCH", p.stderr)

    def test_control_a_variable_on_an_empty_table_is_the_choice_it_always_was(self):
        plant_brain(self.d, "jina", rows=0)
        code = ("import sys; sys.path.insert(0, sys.argv[1]); import claude_memory as c\n"
                "c.DB_PATH = c.Path(sys.argv[2]); print(c._active_embed_model())")
        env = {**os.environ, "WIZARD_LIBRARY_DB": str(self.d / "claude_memory.db"), "WIZARD_EMBED_MODEL": "nomic"}
        p = subprocess.run([sys.executable, "-c", code, str(ROOT / "organs"), str(self.d / "claude_memory.db")], env=env,
                           capture_output=True, text=True)
        self.assertEqual(p.stdout.strip(), "nomic", p.stderr)

    def test_doctors_model_read_follows_the_brain_not_the_variable(self):
        from voidloom import _bootstrap
        db = plant_brain(self.d, "jina")
        tiers = {t: (TABLES[t][0], t) for t in TABLES}
        keep = os.environ.get("WIZARD_EMBED_MODEL")
        os.environ["WIZARD_EMBED_MODEL"] = "nomic"
        try:
            self.assertEqual(_bootstrap._brain_tier(db, tiers), ("jina", "metadata.active_embedding_table"))
        finally:
            os.environ.pop("WIZARD_EMBED_MODEL") if keep is None else os.environ.__setitem__("WIZARD_EMBED_MODEL", keep)


class DoctorOnALockOverAnotherTiersBrain(unittest.TestCase):
    def test_the_disagreement_is_a_fault_and_the_cause_is_not_asserted(self):
        from voidloom import cli
        msg = cli._brain_fault({"metadata_disagrees": "x"})
        self.assertEqual(msg, "x")
        src = (ROOT / "voidloom" / "cli.py").read_text(encoding="utf-8")
        self.assertIn("or a lock was written over another", src)


if __name__ == "__main__":
    unittest.main()
