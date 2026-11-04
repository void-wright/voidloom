# -*- coding: utf-8 -*-
"""The README states the lock's rules where a user meets them, and each statement is tied to the code that makes it true.

A refusal that carries its own hint does not strand a user, but one nobody can look up is one they cannot prepare for.
Each case reads the sentence and the thing it describes, so the text cannot outlive the behaviour.
"""
from __future__ import annotations

import re
import subprocess
import sys
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


class TheDocumentedRouteIsTheRouteTheRefusalPrints(unittest.TestCase):

    def _documented(self):
        m = re.search(r"venv/bin/python3 (organs/embedding_upgrade\.py --db-path \S+ re-embed)", README)
        self.assertIsNotNone(m, "the README names no re-embed command")
        return m.group(1)

    def test_it_matches_the_command_the_engine_prints(self):
        import memory_write
        self.assertEqual(self._documented(), memory_write.re_embed_command("claude_memory.db").replace("python3 ", "", 1))

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
