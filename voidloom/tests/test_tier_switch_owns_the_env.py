"""`tier set` owns the whole switch: nothing the installer generated may disagree with the lock afterwards.

The env file's managed block names the embedder, and every scheduled job loads that file. A switch that moved the lock
and left that line would leave every job refusing WEIGHTS_MISMATCH, so the line moves with the flip, comes back on a
rollback, and `doctor` does not read healthy while the two disagree. Each check is run against the engine's own reader
of the variable, under the file as a job loads it.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "organs"))

from voidloom.tests import test_tier_set as tt  # noqa: E402

BEGIN, END = "# >>> wizard-library managed >>>", "# <<< wizard-library managed <<<"


def env_text(db: Path, model: str, *, after: str = "") -> str:
    return (f"# owner's first line\nFOO=keep\n{BEGIN}\nWIZARD_LIBRARY_DB='{db}'\nWIZARD_EMBED_MODEL={model}\n{END}\n{after}")


def file_env(path: Path) -> dict:
    """The file's assignments, as an EnvironmentFile= hands them to a unit."""
    import voidloom_device
    res = voidloom_device._systemd_reader().read_file(str(path), BEGIN, END)
    return {k: v[0] for k, v in res["effective"].items()}


@unittest.skipUnless(tt._vec_available(), "needs sqlite_vec")
class TierSwitchOwnsTheEnv(unittest.TestCase):
    def setUp(self):
        import tier_set
        self.ts = tier_set
        self.td = tempfile.TemporaryDirectory()
        self.d = Path(self.td.name).resolve()
        self.db = tt.make_brain(self.d, "nomic")
        self.envf = self.d / "wizard-library-env"
        self.envf.write_text(env_text(self.db, "nomic"))
        os.chmod(self.envf, 0o600)
        for patcher in (mock.patch.object(tier_set, "_load", tt.fake_load),
                        mock.patch.object(tier_set, "_encode", tt.fake_encode),
                        mock.patch.dict(os.environ, {"WIZARD_ENV_FILE": str(self.envf)})):
            patcher.start()
            self.addCleanup(patcher.stop)
        os.environ.pop("WIZARD_EMBED_MODEL", None)
        os.environ.pop("VOIDLOOM_ENV_DIR", None)
        self.addCleanup(self.td.cleanup)

    def _engine_tier_under_the_file(self):
        """What the engine says, in a process that loaded the env file as a job does."""
        code = ("import sys; sys.path.insert(0, sys.argv[1]); import claude_memory as c\n"
                "c.DB_PATH = c.Path(sys.argv[2]); print(c._active_embed_model())")
        env = {k: v for k, v in os.environ.items() if k != "WIZARD_EMBED_MODEL"}
        env.update(file_env(self.envf))
        return subprocess.run([sys.executable, "-c", code, str(ROOT / "organs"), str(self.db)], env=env,
                              capture_output=True, text=True)

    def test_the_line_moves_with_the_flip_and_a_job_under_the_file_works(self):
        out = self.ts.set_tier(self.db, "qwen")
        self.assertEqual(out["receipt"]["env_file"], {"path": str(self.envf), "from": "nomic", "to": "qwen"})
        self.assertEqual(file_env(self.envf)["WIZARD_EMBED_MODEL"], "qwen")
        self.assertEqual(self.envf.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.envf.read_text(), env_text(self.db, "qwen"))
        run = self._engine_tier_under_the_file()
        self.assertEqual((run.returncode, run.stdout.strip()), (0, "qwen"), run.stderr)

    def test_rollback_puts_the_line_back(self):
        self.ts.set_tier(self.db, "qwen")
        out = self.ts.rollback(self.db)
        self.assertEqual(out["receipt"]["env_file"]["to"], "nomic")
        self.assertEqual(self.envf.read_text(), env_text(self.db, "nomic"))
        run = self._engine_tier_under_the_file()
        self.assertEqual((run.returncode, run.stdout.strip()), (0, "nomic"), run.stderr)

    def test_a_shell_that_sourced_the_block_is_not_refused_and_is_told(self):
        with mock.patch.dict(os.environ, {"WIZARD_EMBED_MODEL": "nomic"}):
            out = self.ts.set_tier(self.db, "qwen")
        self.assertEqual(out["status"], "ok")
        self.assertIn("unset WIZARD_EMBED_MODEL", out["this_shell"])

    def test_a_variable_from_elsewhere_still_refuses(self):
        self.envf.write_text(env_text(self.db, "qwen"))
        with mock.patch.dict(os.environ, {"WIZARD_EMBED_MODEL": "nomic"}):
            with self.assertRaises(self.ts.TierRefused) as cm:
                self.ts.set_tier(self.db, "qwen")
        self.assertEqual(cm.exception.code, "LOCK_ENV_PINNED")

    def test_an_alias_of_the_target_is_agreement(self):
        with mock.patch.dict(os.environ, {"WIZARD_EMBED_MODEL": "gpu"}):
            self.assertEqual(self.ts.set_tier(self.db, "qwen")["status"], "ok")

    def test_an_owners_line_outside_the_block_refuses_and_is_left_alone(self):
        text = env_text(self.db, "nomic", after="WIZARD_EMBED_MODEL=nomic\n")
        self.envf.write_text(text)
        lock_before = (self.d / "embedder.lock.json").read_bytes()
        with self.assertRaises(self.ts.TierRefused) as cm:
            self.ts.set_tier(self.db, "qwen")
        self.assertEqual(cm.exception.code, "LOCK_ENV_PINNED")
        self.assertIn("line 7", cm.exception.message)
        self.assertEqual(self.envf.read_text(), text)
        self.assertEqual((self.d / "embedder.lock.json").read_bytes(), lock_before)

    def test_another_brains_env_file_is_never_touched(self):
        text = env_text(self.d / "elsewhere.db", "nomic")
        self.envf.write_text(text)
        out = self.ts.set_tier(self.db, "qwen")
        self.assertIsNone(out["receipt"]["env_file"])
        self.assertEqual(self.envf.read_text(), text)

    def test_an_env_file_that_cannot_be_written_stops_the_switch_whole(self):
        import voidloom_env
        lock_before = (self.d / "embedder.lock.json").read_bytes()
        with mock.patch.object(voidloom_env, "set_embed_pin", side_effect=OSError("read-only")):
            with self.assertRaises(self.ts.TierRefused) as cm:
                self.ts.set_tier(self.db, "qwen")
        self.assertEqual(cm.exception.code, "ENV_FILE_UNWRITABLE")
        self.assertEqual((self.d / "embedder.lock.json").read_bytes(), lock_before)
        c = tt._connect(self.db)
        self.assertEqual(self.ts._metadata(c)["active_embedding_table"], "memory_embeddings")
        c.close()
        self.assertEqual(self.envf.read_text(), env_text(self.db, "nomic"))

    def test_a_lock_that_cannot_follow_puts_the_line_back(self):
        import embedder_lock
        with mock.patch.object(embedder_lock, "write_lock", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.ts.set_tier(self.db, "qwen")
        self.assertEqual(self.envf.read_text(), env_text(self.db, "nomic"))

    def test_re_running_the_verb_heals_a_stale_line(self):
        self.ts.set_tier(self.db, "qwen")
        self.envf.write_text(env_text(self.db, "nomic"))
        out = self.ts.set_tier(self.db, "qwen")
        self.assertEqual(out["receipt"]["env_file"]["to"], "qwen")
        self.assertEqual(self.envf.read_text(), env_text(self.db, "qwen"))

    def test_doctor_does_not_read_healthy_while_they_disagree(self):
        from voidloom import cli
        self.ts.set_tier(self.db, "qwen")
        with mock.patch.object(cli, "_provenance_defects", return_value={}, create=True):
            clean = cli._tier_coherence(str(self.db))
            self.assertNotIn("refusal", clean, clean)
            self.envf.write_text(env_text(self.db, "nomic"))
            stale = cli._tier_coherence(str(self.db))
            self.assertEqual(stale["refusal"]["error"], "WEIGHTS_MISMATCH")
            self.assertIn("voidloom tier set qwen", stale["refusal"]["message"])
            self.assertIn("embedder lock: WEIGHTS_MISMATCH", cli._brain_fault(stale))
            self.envf.write_text(env_text(self.db, "qwen"))
            with mock.patch.dict(os.environ, {"WIZARD_EMBED_MODEL": "nomic"}):
                self.assertEqual(cli._tier_coherence(str(self.db))["refusal"]["error"], "WEIGHTS_MISMATCH")

    def test_the_block_writer_leaves_every_other_byte(self):
        import voidloom_device
        ef = voidloom_device._systemd_reader()
        data = env_text(self.db, "nomic", after="BAR='x y'\n").encode()
        image = ef.rewrite_block_key(data, "WIZARD_EMBED_MODEL", "qwen", BEGIN, END)
        self.assertEqual(image, data.replace(b"WIZARD_EMBED_MODEL=nomic", b"WIZARD_EMBED_MODEL=qwen"))
        with self.assertRaises(ef.Refusal):
            ef.rewrite_block_key(b"A=1\n", "WIZARD_EMBED_MODEL", "qwen", BEGIN, END)


if __name__ == "__main__":
    unittest.main()
