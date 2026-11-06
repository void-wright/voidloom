"""The installers pre-download the embedder AT THE INSTALL LOCK'S PINS, never by name, and fetch nothing a person did not select.

PowerShell is not run here (test_ps1_lays_a_brain.py runs it). What is run is the Python each installer hands its venv: the
block is extracted from the file byte for byte and executed, with the repository's own organs/ as the brain's organs.
`sentence_transformers` is a tripwire module that records any import, and `huggingface_hub` a stand-in that records every
download, so "instantiates nothing" and "fetches nothing" are observed, not assumed.

  saboteur  a lockless brain that selected nomic: `init` did not finish, so nothing is fetched and the run says so;
  control   a lockless brain that selected jina is fetched at the engine's pin, and that is the only tier that is;
  saboteur  a LOCKED brain against an empty, offline model cache: the pre-download refuses typed (SNAPSHOT_ABSENT, naming
            organs/model_fetch.py), exit 4, and nothing is loaded;
  order     `init` (the one lock writer) runs before the pre-download, as in install.sh.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PS1 = ROOT / "install" / "install.ps1"
sys.path.insert(0, str(ROOT / "organs"))


def predl_python() -> str:
    text = PS1.read_text(encoding="utf-8").replace("\r\n", "\n")
    start = text.index("$predlPy = @'\n") + len("$predlPy = @'\n")
    end = text.index("\n'@", start)
    return text[start:end] + "\n"


def sh_python() -> str:
    """install.sh's pre-cache block, from the line that opens its heredoc to the line that closes it."""
    text = (ROOT / "install.sh").read_text(encoding="utf-8")
    anchor = text.index('"$VENV_PY" - <<\'PYEOF\' || { T1_MODELS_ABSENT=1;')
    start = text.index("\n", anchor) + 1
    return text[start:text.index("\nPYEOF\n", start)] + "\n"


class Ps1PrecacheByPin(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        trip = self.dir / "trip" / "sentence_transformers"
        trip.mkdir(parents=True)
        self.marker = self.dir / "tripwire_fired"
        (trip / "__init__.py").write_text(
            "open(%r, 'w').write('imported')\n"
            "raise ImportError('tripwire: sentence_transformers imported')\n" % str(self.marker))
        hub = self.dir / "trip" / "huggingface_hub"
        hub.mkdir()
        self.fetched = self.dir / "fetched"
        (hub / "__init__.py").write_text(
            "def snapshot_download(repo, revision=None, **kw):\n"
            "    open(%r, 'a').write('%%s@%%s\\n' %% (repo, revision))\n"
            "    return ''\n" % str(self.fetched))
        self.script = self.dir / "predl.py"
        self.script.write_text(predl_python())
        self.brain = self.dir / "brain"
        self.brain.mkdir()
        self.db = self.brain / "claude_memory.db"
        self.db.write_bytes(b"")

    def tearDown(self):
        self._tmp.cleanup()

    def run_predl(self, repo="nomic-ai/nomic-embed-text-v1.5", script=None, **extra):
        hf = self.dir / "empty_hf"
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(self.dir),
               "PYTHONPATH": str(self.dir / "trip"), "HF_HOME": str(hf), "HF_HUB_CACHE": str(hf / "hub"),
               "HF_HUB_OFFLINE": "1", "WIZARD_LIBRARY_HOME": str(ROOT), "WIZARD_LIBRARY_DB": str(self.db),
               "EMBED_MODEL_REPO": repo, "RERANKER_MODEL": "", **extra}
        if os.environ.get("CUDA_VISIBLE_DEVICES") is not None:
            env["CUDA_VISIBLE_DEVICES"] = os.environ["CUDA_VISIBLE_DEVICES"]
        return subprocess.run([sys.executable, "-B", str(script or self.script)], env=env, capture_output=True,
                              text=True, timeout=120)

    def fetches(self):
        return self.fetched.read_text().splitlines() if self.fetched.exists() else []

    def test_saboteur_a_lockless_brain_that_selected_nomic_fetches_nothing_and_says_so(self):
        r = self.run_predl()
        self.assertEqual(r.returncode, 4, r.stdout + r.stderr)
        self.assertEqual(self.fetches(), [], "a person who selected nomic was sent the encumbered model")
        self.assertIn("`init` did not finish", r.stderr)
        self.assertFalse(self.marker.exists(), "nothing is loaded by name")

    def test_control_a_lockless_brain_that_selected_jina_is_fetched_at_the_engines_pin(self):
        import embedding_upgrade as eu
        r = self.run_predl(repo=eu.JINA_MODEL_NAME)
        self.assertEqual(self.fetches(), [f"{eu.JINA_MODEL_NAME}@{eu.JINA_REVISION}"], r.stdout + r.stderr)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse(self.marker.exists())

    def run_sh(self, tier, repo):
        script = self.dir / "precache_sh.py"
        script.write_text(sh_python())
        return self.run_predl(script=script, BRAIN_DIR=str(ROOT), WIZARD_EMBED_MODEL=tier, EMBED_REPO=repo, RERANKER_REPO="")

    def test_saboteur_install_sh_reaches_its_engine_pin_branch_for_jina_alone(self):
        import embedding_upgrade as eu
        r = self.run_sh("jina", eu.JINA_MODEL_NAME)
        self.assertEqual(self.fetches(), [f"{eu.JINA_MODEL_NAME}@{eu.JINA_REVISION}"], r.stdout + r.stderr)
        self.fetched.unlink()
        for tier, repo in (("nomic", "nomic-ai/nomic-embed-text-v1.5"), ("qwen", "Qwen/Qwen3-Embedding-0.6B")):
            r = self.run_sh(tier, repo)
            self.assertEqual(self.fetches(), [], (tier, r.stdout + r.stderr))
            self.assertNotEqual(r.returncode, 0, (tier, r.stdout + r.stderr))

    def test_saboteur_install_sh_refuses_a_tier_with_no_pin_that_is_not_jina(self):
        r = self.run_sh("not-a-tier", "someone/else")
        self.assertEqual(self.fetches(), [], r.stdout + r.stderr)
        self.assertIn("`init` did not finish", r.stdout + r.stderr)

    def test_saboteur_a_locked_brain_on_an_empty_offline_cache_refuses_typed_and_never_loads_by_name(self):
        import embedder_lock
        embedder_lock.write_lock(self.brain, "cpu")
        r = self.run_predl()
        self.assertEqual(r.returncode, 4, r.stdout + r.stderr)
        self.assertIn("SNAPSHOT_ABSENT", r.stderr)
        self.assertIn("model_fetch.py", r.stderr)
        self.assertFalse(self.marker.exists(), "the by-name path ran on a locked brain")

    def test_order_init_writes_the_lock_before_the_pre_download(self):
        text = PS1.read_text(encoding="utf-8")
        first_init = text.index('organs\\claude_memory.py") init')
        self.assertLess(first_init, text.index("$predlPy = @'"))


if __name__ == "__main__":
    unittest.main()
