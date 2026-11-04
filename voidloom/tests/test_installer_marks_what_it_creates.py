"""The installer marks the sidecar directory it creates, and no other, so a stranger never marks anything by hand.

THE CLAIM UNDER TEST
  Two installs can share one $HOME. The ownership marker (organs/voidloom_env.py) keeps each out of the other's sidecars,
  and it only helps if it is there. Ownership comes from creating the directory:
  control   a fresh install creates the directory; the marker names the BRAIN directory, not the package it ran from;
            the laid code, with the installer's claim of identity gone, reads the directory as its own;
  control   a re-run from the package over its own marked directory succeeds, and the marker is untouched;
  saboteur  a directory that was already here stays unmarked, and the install goes on;
  saboteur  a directory marked for another install refuses before anything is written;
  saboteur  a dry run creates and marks nothing;
  control   uninstall releases this install's marker and keeps the directory, so a later install elsewhere is not refused;
  saboteur  uninstall leaves another install's marker alone;
  saboteur  the identity the installer claims never reaches the env file, and is still checked: naming the wrong root
            passes nothing.

The installs are Tier 0 (stdlib; no model, no network).
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

from voidloom.tests.test_tier1_ambient import _AmbientHome, ROOT, SH  # noqa: E402

sys.path.insert(0, str(ROOT / "organs"))
import voidloom_env as ve  # noqa: E402

ARGS = ["--tier0-only", "--force-cpu", "--thinker", "off", "--no-wire-hooks"]
MARKER = ".voidloom-owner"
FOREIGN = json.dumps({"schema": "voidloom.env-owner/1", "install_root": "/nonexistent/other-install"}) + "\n"


class TheInstallerMarksWhatItCreates(_AmbientHome):
    def setUp(self):
        super().setUp()
        self.side = self.env_file.parent
        self.marker = self.side / MARKER

    def _run(self, *extra, **env):
        return subprocess.run(["bash", str(SH)] + list(extra) + ARGS, env=self._env(**env), cwd=str(self.tmp),
                              stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=900,
                              start_new_session=True)

    def _ok(self, r):
        self.assertEqual(r.returncode, 0, (r.stdout[-1500:], r.stderr[-1500:]))

    def _laid_state(self):
        env = self._env()
        env.pop("VOIDLOOM_INSTALL_ROOT", None)
        p = subprocess.run([sys.executable, "-B", str(self.brain / "organs" / "voidloom_env.py"), "--owner-state", str(self.side)],
                           env=env, capture_output=True, text=True, timeout=60)
        self.assertEqual(p.returncode, 0, p.stderr)
        return p.stdout.strip()

    def test_control_a_created_directory_is_marked_for_the_brain_and_rerun_keeps_it(self):
        self._ok(self._run("--init-blank"))
        doc = json.loads(self.marker.read_text(encoding="utf-8"))
        self.assertEqual(Path(doc["install_root"]), self.brain.resolve())
        self.assertNotEqual(Path(doc["install_root"]), ROOT)
        self.assertEqual(self._laid_state(), "own")
        self.assertNotIn("VOIDLOOM_INSTALL_ROOT", self.env_file.read_text(encoding="utf-8"))
        before = self.marker.read_bytes()
        r = self._run()                                     # the package's helpers meet a directory marked for the brain
        self._ok(r)
        self.assertNotIn("ENV_DIR_FOREIGN", r.stdout + r.stderr)
        self.assertEqual(self.marker.read_bytes(), before)

    def test_saboteur_a_directory_already_here_is_never_marked(self):
        self.side.mkdir(parents=True)
        self._ok(self._run("--init-blank"))
        self.assertFalse(self.marker.exists(), "the installer marked a directory it did not create")
        self.assertEqual(self._laid_state(), "unmarked")

    def test_saboteur_another_installs_directory_refuses_before_any_write(self):
        self.side.mkdir(parents=True)
        self.marker.write_text(FOREIGN, encoding="utf-8")
        r = self._run("--init-blank")
        self.assertEqual(r.returncode, 2, (r.stdout[-800:], r.stderr[-800:]))
        self.assertIn("ENV_DIR_FOREIGN", r.stderr)
        self.assertIn("WIZARD_ENV_FILE", r.stderr)
        self.assertEqual(self.marker.read_text(encoding="utf-8"), FOREIGN)
        self.assertEqual([p.name for p in self.side.iterdir()], [MARKER])
        self.assertFalse(self.brain.exists(), "a refused install laid something")

    def test_saboteur_a_dry_run_creates_and_marks_nothing(self):
        self._ok(self._run("--init-blank", "--dry-run"))
        self.assertFalse(self.side.exists())

    def test_control_uninstall_releases_this_installs_marker_and_keeps_the_directory(self):
        self._ok(self._run("--init-blank"))
        r = subprocess.run(["bash", str(ROOT / "uninstall.sh"), "--yes"], env=self._env(), cwd=str(self.tmp),
                           stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=300)
        self._ok(r)
        self.assertFalse(self.marker.exists())
        self.assertTrue(self.env_file.exists())
        self.assertIn("released", r.stdout)

    def test_saboteur_uninstall_leaves_another_installs_marker(self):
        self.side.mkdir(parents=True)
        self.marker.write_text(FOREIGN, encoding="utf-8")
        r = subprocess.run(["bash", str(ROOT / "uninstall.sh"), "--yes"], env=self._env(), cwd=str(self.tmp),
                           stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=300)
        self._ok(r)
        self.assertEqual(self.marker.read_text(encoding="utf-8"), FOREIGN)


class ClaimReleaseAndActingRoot(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory(prefix="vl_claim_")
        self.addCleanup(self.td.cleanup)
        self.d = Path(self.td.name)
        env = {k: v for k, v in os.environ.items() if k != "VOIDLOOM_INSTALL_ROOT"}
        p = mock.patch.dict(os.environ, env, clear=True)
        p.start()
        self.addCleanup(p.stop)

    def test_control_claim_creates_marks_and_names_the_given_root(self):
        side, brain = self.d / "a" / "side", self.d / "brain"
        self.assertTrue(ve.claim_env_dir(side, brain))
        self.assertEqual(json.loads((side / MARKER).read_text(encoding="utf-8"))["install_root"], str(brain.resolve()))

    def test_saboteur_claim_of_an_existing_directory_writes_nothing(self):
        side = self.d / "side"
        side.mkdir()
        self.assertFalse(ve.claim_env_dir(side, self.d / "brain"))
        self.assertEqual(list(side.iterdir()), [])

    def test_control_the_claimed_root_is_what_the_check_speaks_for(self):
        side, brain = self.d / "side", self.d / "brain"
        ve.claim_env_dir(side, brain)
        self.assertEqual(ve.owner_state(side), "foreign", "this checkout is not that install")
        with mock.patch.dict(os.environ, {"VOIDLOOM_INSTALL_ROOT": str(brain)}):
            self.assertEqual(ve.owner_state(side), "own")
            self.assertEqual(ve.install_root(), ROOT, "where the code IS must not move")

    def test_saboteur_the_claim_is_still_checked(self):
        side = self.d / "side"
        ve.claim_env_dir(side, self.d / "brain")
        with mock.patch.dict(os.environ, {"VOIDLOOM_INSTALL_ROOT": str(self.d / "another")}):
            self.assertEqual(ve.owner_state(side), "foreign")
        with mock.patch.dict(os.environ, {"VOIDLOOM_INSTALL_ROOT": "relative/root"}):
            with self.assertRaises(ValueError):
                ve.acting_root()

    def test_release_is_match_only(self):
        side, brain = self.d / "side", self.d / "gone"
        ve.claim_env_dir(side, brain)
        self.assertFalse(ve.release_owner_marker(side, self.d / "other"))
        self.assertTrue((side / MARKER).exists())
        self.assertTrue(ve.release_owner_marker(side, brain), "the named root need not exist any more")
        self.assertFalse((side / MARKER).exists())

    def test_states(self):
        self.assertEqual(ve.owner_state(self.d / "nope"), "absent")
        side = self.d / "side"
        side.mkdir()
        self.assertEqual(ve.owner_state(side), "unmarked")
        ve.write_owner_marker(side)
        self.assertEqual(ve.owner_state(side), "own")
        (side / MARKER).write_text("not json", encoding="utf-8")
        self.assertEqual(ve.owner_state(side), "foreign")


if __name__ == "__main__":
    unittest.main()
