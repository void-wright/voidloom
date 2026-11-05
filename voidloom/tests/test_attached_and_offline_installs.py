"""Two install paths the happy case never walks: a brain attached from elsewhere, and a hub that is down."""
from __future__ import annotations

import os
import re
import sqlite3
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "organs"))

from voidloom.tests.test_install_stops_and_says_why import HAVE_VEC, _Home  # noqa: E402

SH = (ROOT / "install.sh").read_text(encoding="utf-8")


@unittest.skipUnless(HAVE_VEC, "needs an interpreter with sqlite_vec")
class AttachingABrain(_Home):
    def setUp(self):
        super().setUp()
        self.path = "%s:%s" % (Path(sys.executable).parent, self.path)
        self.away = Path(self.home) / "elsewhere"
        self.away.mkdir()
        self.db = self.away / "brain.db"
        import schema_migrator
        schema_migrator.apply(str(self.db), ROOT / "migrations")
        c = schema_migrator.connect(str(self.db))
        c.execute("INSERT INTO memories (content, summary, category, importance) VALUES ('mine', 's', 'insight', 0.5)")
        c.commit()
        c.close()
        os.chmod(self.db, 0o600)

    def test_the_engine_finds_the_attached_file_through_a_link(self):
        p = self._run("--attach-db", str(self.db), "--tier0-only")
        link = self.brain / "claude_memory.db"
        self.assertTrue(link.is_symlink(), (p.stdout + p.stderr)[-800:])
        self.assertEqual(link.resolve(), self.db.resolve())

    def test_the_pre_image_is_as_private_as_the_brain(self):
        self._run("--attach-db", str(self.db), "--tier0-only")
        pre = [q for q in self.away.iterdir() if q.name.startswith("brain.db.pre-attach")]
        self.assertTrue(pre)
        self.assertEqual(stat.S_IMODE(pre[0].stat().st_mode), 0o600)

    def test_a_different_brain_in_the_way_is_never_replaced(self):
        self.brain.mkdir(parents=True, exist_ok=True)
        other = self.brain / "claude_memory.db"
        c = sqlite3.connect(other)
        c.execute("CREATE TABLE memories (id INTEGER PRIMARY KEY)")
        c.commit(); c.close()
        p = self._run("--attach-db", str(self.db), "--tier0-only")
        self.assertNotEqual(p.returncode, 0, p.stdout[-500:])
        self.assertIn("is not the attached brain", p.stdout + p.stderr)
        self.assertFalse(other.is_symlink())


class TheCodeIsFoundAwayFromTheBrain(unittest.TestCase):
    def test_organ_dir_falls_back_to_the_install(self):
        from voidloom import _paths
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "brain.db"
            db.write_bytes(b"")
            with mock.patch.dict(os.environ, {"WIZARD_LIBRARY_HOME": str(ROOT)}):
                self.assertEqual(_paths.organ_dir(str(db)), ROOT / "organs")

    def test_a_flat_tree_still_resolves_to_itself(self):
        from voidloom import _paths
        with tempfile.TemporaryDirectory() as td:
            d = Path(td).resolve()
            (d / "claude_memory.py").write_text("")
            self.assertEqual(_paths.organ_dir(str(d / "claude_memory.db")), d)


class AHubOutage(unittest.TestCase):
    def test_the_failure_is_named_and_not_blamed_on_the_machine(self):
        self.assertIn('T1_MODELS_ABSENT=1; warn "MODELS_ABSENT: the models could not be fetched. Nothing is wrong with this machine.', SH)
        self.assertNotIn("lazy-download", SH)

    def _precache(self, d: Path, **env):
        """The installer's own pre-cache step, cut from install.sh and run: its environment, its heredoc, its verdict."""
        i = SH.index('WIZARD_EMBED_MODEL="$EMBED_MODEL" EMBED_REPO="$EMBED_REPO" \\\n            WIZARD_LIBRARY_DB="$DB_PATH" BRAIN_DIR')
        step = SH[i:SH.index("\nPYEOF\n", i) + 7]
        script = ('warn() { echo "WARN: $*"; }\nT1_MODELS_ABSENT=0\n'
                  f'EMBED_MODEL=nomic EMBED_REPO=nomic-ai/nomic-embed-text-v1.5 DB_PATH="{d}/claude_memory.db" BRAIN_DIR="{ROOT}" '
                  f'_pc_rr= RERANKER_FALLBACK_REPO= _pc_fb=0 VENV_PY="{sys.executable}"\n{step}\necho "ABSENT=$T1_MODELS_ABSENT"\n')
        return subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=120,
                              env={**os.environ, "HOME": str(d), "HF_HOME": str(d / "hf"), **env})

    def test_the_step_gets_its_environment_and_a_failed_fetch_is_absent_models(self):
        import embedder_lock as el
        with tempfile.TemporaryDirectory() as td:
            d = Path(td).resolve()
            el.write_lock(d, "cpu")
            p = self._precache(d, HF_HUB_OFFLINE="1")
        self.assertIn("ABSENT=1", p.stdout, p.stdout[-600:] + p.stderr[-300:])
        self.assertIn("MODELS_ABSENT", p.stdout)
        self.assertNotIn("'BRAIN_DIR'", p.stdout, "the step ran without its environment")
        self.assertIn("SNAPSHOT_ABSENT", p.stdout)

    def test_no_device_is_proven_or_recorded_on_absent_models(self):
        i = SH.index("T1_DEVICE_OK=1")
        self.assertIn('[ "$T1_MODELS_ABSENT" -eq 0 ]', SH[i:i + 300])

    def test_the_hint_gives_the_remedy_that_fits(self):
        i = SH.index("tier1_upgrade_hint() {")
        func = SH[i:SH.index("\n}\n", i) + 3]
        script = f'EMBED_MODEL=nomic EMBED_DIM=768 PY_SYS=python3 VENV_DIR=v VENV_PY=p REQ_FILE=r DB_PATH=d\n{func}\ntier1_upgrade_hint "$1"\n'
        off = subprocess.run(["bash", "-c", script, "x", "the models could not be fetched (MODELS_ABSENT)"], capture_output=True, text=True).stdout
        other = subprocess.run(["bash", "-c", script, "x", "no venv"], capture_output=True, text=True).stdout
        self.assertIn("with the network up", off)
        self.assertNotIn("Python >= 3.10", off)
        self.assertIn("Python >= 3.10", other)

    def test_doctor_reads_tier_0_as_tier_0_under_an_interpreter_without_vec0(self):
        from voidloom import cli
        out = {"coverage": {"ok": False, "error": "PROVENANCE_UNREADABLE", "message": "no such module: vec0"}, "has_stamps": False}
        self.assertIsNone(cli._coverage_refusal(out))
        self.assertEqual(cli._coverage_refusal({**out, "has_stamps": True}), "PROVENANCE_UNREADABLE")


class TheWindowsLauncher(unittest.TestCase):
    def test_no_call_treats_a_two_word_launcher_as_one_program(self):
        text = (ROOT / "install" / "install.ps1").read_text(encoding="utf-8")
        self.assertEqual(re.findall(r"& \$PySys -B \$envOrgan", text), [])
        self.assertIn('Invoke-PyOut $PySys @("-B", $envOrgan, "--claim", $envDir, $BrainDir)', text)


if __name__ == "__main__":
    unittest.main()
