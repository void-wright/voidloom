# -*- coding: utf-8 -*-
"""The README states the lock's rules where a user meets them, and each statement is tied to the code that makes it true.

A refusal that carries its own hint does not strand a user, but one nobody can look up is one they cannot prepare for.
Each case reads the sentence and the thing it describes, so the text cannot outlive the behaviour.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
README = (ROOT / "README.md").read_text(encoding="utf-8")
sys.path.insert(0, str(ROOT / "organs"))


class TheReadmeNamesTheRefusals(unittest.TestCase):

    def test_no_rows_for_lock_is_explained_and_is_a_real_refusal(self):
        self.assertIn("NO_ROWS_FOR_LOCK", README)
        import embedder_lock
        self.assertIn("NO_ROWS_FOR_LOCK", embedder_lock.REFUSALS)

    def test_the_env_variable_may_only_agree_with_the_lock(self):
        self.assertRegex(README, r"`WIZARD_EMBED_MODEL` chooses the model at install only")
        self.assertRegex(README, r"may only agree\s+with `embedder\.lock\.json`")
        self.assertIn("WEIGHTS_MISMATCH", README)
        src = (ROOT / "organs" / "claude_memory.py").read_text(encoding="utf-8")
        self.assertRegex(src, r'"WEIGHTS_MISMATCH",\s+f"WIZARD_EMBED_MODEL asks for')


class _VenvBox:
    """A laid brain with a venv, and a stack-free `python3` first on PATH: the state a stranger's shell is in."""

    def __init__(self, root: Path):
        self.brain = root / "brain"
        self.brain.mkdir()
        shutil.copytree(ROOT / "organs", self.brain / "organs", ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copytree(ROOT / "migrations", self.brain / "migrations")
        vbin = self.brain / "venv" / "bin"
        vbin.mkdir(parents=True)
        (vbin / "python3").write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n')
        (vbin / "python3").chmod(0o755)
        self.path_bin = root / "bin"
        self.path_bin.mkdir()
        (self.path_bin / "python3").write_text('#!/bin/sh\necho STUB-PYTHON >&2\nexit 97\n')
        (self.path_bin / "python3").chmod(0o755)
        self.db = self.brain / "claude_memory.db"
        self.env = {"PATH": f"{self.path_bin}:/usr/bin:/bin", "HOME": str(root), "HF_HUB_OFFLINE": "1",
                    "HF_HOME": str(root / "hf"), "WIZARD_LIBRARY_DB": str(self.db), "LANG": "C.UTF-8"}
        init = subprocess.run([str(vbin / "python3"), str(self.brain / "organs" / "claude_memory.py"), "init", "--tier", "cpu"],
                              env=self.env, capture_output=True, text=True, timeout=120)
        assert self.db.exists(), init.stdout + init.stderr
        (self.brain / "embedder.lock.json").unlink()
        (self.brain / "embedder.lock.history.jsonl").unlink()

    def printed(self):
        code = ("import json, sys; sys.path.insert(0, sys.argv[1]); import embedder_lock as el, memory_write as mw\n"
                "print(json.dumps({'re_embed': mw.re_embed_command(sys.argv[2]), 'fetch': el.fetch_command(sys.argv[2]),\n"
                "                  'init': el.organ_command('claude_memory.py', 'init', '--tier', 'cpu')}))")
        p = subprocess.run([sys.executable, "-c", code, str(self.brain / "organs"), str(self.db)], env=self.env,
                           capture_output=True, text=True, timeout=120)
        return json.loads(p.stdout)

    def run_shell(self, cmd):
        return subprocess.run(["bash", "-c", cmd], env=self.env, cwd="/", capture_output=True, text=True, timeout=300)


class TheDocumentedRouteIsTheRouteTheRefusalPrints(unittest.TestCase):

    def _documented(self):
        m = re.search(r"venv/bin/python3 (organs/embedding_upgrade\.py --db-path \S+ re-embed)", README)
        self.assertIsNotNone(m, "the README names no re-embed command")
        return m.group(1)

    def test_it_matches_the_command_the_engine_prints(self):
        """Every repair command runs where it is printed: on a venv install, from any directory, with a `python3` on PATH
        that has none of the stack. Run for real. The answer may be a typed refusal (no model here); never a traceback."""
        with tempfile.TemporaryDirectory() as td:
            box = _VenvBox(Path(td))
            cmds = box.printed()
            self.assertIn(self._documented().split(" --db-path")[0].split("/")[-1], cmds["re_embed"])
            self.assertTrue(cmds["re_embed"].startswith(str(box.brain / "venv" / "bin" / "python3")), cmds)
            for name, cmd in cmds.items():
                p = box.run_shell(cmd)
                self.assertNotIn("Traceback", p.stderr, (name, cmd, p.stderr[-600:]))
                self.assertNotIn("No module named", p.stdout + p.stderr, (name, cmd))
                self.assertNotIn("STUB-PYTHON", p.stdout + p.stderr, (name, cmd))
            p = box.run_shell(f"cd {box.brain} && {self._documented_with_venv()}")
            self.assertNotIn("Traceback", p.stderr, p.stderr[-600:])

    def _documented_with_venv(self):
        return "venv/bin/python3 " + self._documented()

    def test_the_lost_lock_fix_writes_the_lock_where_it_is_printed(self):
        with tempfile.TemporaryDirectory() as td:
            box = _VenvBox(Path(td))
            cmd = box.printed()["init"]
            p = box.run_shell(cmd)
            self.assertTrue((box.brain / "embedder.lock.json").exists(), (cmd, p.stdout[-400:], p.stderr[-400:]))

    def test_the_verb_and_the_flag_exist(self):
        p = subprocess.run([sys.executable, str(ROOT / "organs" / "embedding_upgrade.py"), "--help"],
                           capture_output=True, text=True, timeout=120)
        self.assertEqual(p.returncode, 0, p.stderr[-400:])
        self.assertIn("--db-path", p.stdout)
        self.assertIn("re-embed", p.stdout)

    def test_the_restore_guide_does_not_warn_of_a_call_the_reranker_no_longer_makes(self):
        text = (ROOT / "docs" / "RESTORE_MANIFEST.md").read_text(encoding="utf-8")
        self.assertNotIn("A recall makes a network call", text)
        self.assertIn("SNAPSHOT_ABSENT", text)

    def test_the_restore_guide_names_no_organ_that_does_not_ship(self):
        text = (ROOT / "docs" / "RESTORE_MANIFEST.md").read_text(encoding="utf-8")
        for name in set(re.findall(r"`([a-z_]+_[a-z_]+)` loads on every recall", text)):
            self.assertTrue((ROOT / "organs" / (name + ".py")).is_file(), name)
        self.assertNotIn("cross_tier_bridges", text)


if __name__ == "__main__":
    unittest.main()
