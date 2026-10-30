"""install/install.ps1 pre-downloads the embedder AT THE INSTALL LOCK'S PINS, never by name.

PowerShell is not run here (the suite runs on Linux). What is run is the Python the
installer hands its venv: the `$predlPy` here-string is extracted from install.ps1
byte for byte and executed, with the repository's own organs/ as the brain's organs.
`sentence_transformers` is replaced by a tripwire module that records any import, so
"instantiates nothing" is observed, not assumed.

  control   a brain with NO lock (a tier the shipped catalogue does not carry) keeps
            the by-name path -- the tripwire fires, as it must;
  saboteur  a LOCKED brain against an empty, offline model cache: the pre-download
            refuses typed (SNAPSHOT_ABSENT, naming organs/model_fetch.py), exit 4, and
            the by-name path is never reached;
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
        self.script = self.dir / "predl.py"
        self.script.write_text(predl_python())
        self.brain = self.dir / "brain"
        self.brain.mkdir()
        self.db = self.brain / "claude_memory.db"
        self.db.write_bytes(b"")

    def tearDown(self):
        self._tmp.cleanup()

    def run_predl(self):
        hf = self.dir / "empty_hf"
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(self.dir),
               "PYTHONPATH": str(self.dir / "trip"), "HF_HOME": str(hf), "HF_HUB_CACHE": str(hf / "hub"),
               "HF_HUB_OFFLINE": "1", "WIZARD_LIBRARY_HOME": str(ROOT), "WIZARD_LIBRARY_DB": str(self.db),
               "EMBED_MODEL_REPO": "nomic-ai/nomic-embed-text-v1.5", "RERANKER_MODEL": ""}
        if os.environ.get("CUDA_VISIBLE_DEVICES") is not None:
            env["CUDA_VISIBLE_DEVICES"] = os.environ["CUDA_VISIBLE_DEVICES"]
        return subprocess.run([sys.executable, "-B", str(self.script)], env=env, capture_output=True,
                              text=True, timeout=120)

    def test_control_a_lockless_brain_keeps_the_by_name_path(self):
        r = self.run_predl()
        self.assertTrue(self.marker.exists(), r.stdout + r.stderr)
        self.assertEqual(r.returncode, 4, r.stdout + r.stderr)

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
