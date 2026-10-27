# -*- coding: utf-8 -*-
"""R2 -- `install.sh --attach-db <path>` attaches an existing brain IN PLACE.

Council #12 (2026-09-18): a second machine already holds a brain.
`--brain-db` COPIES a snapshot into ${BRAIN_DIR}; attaching means laying the
machinery, pointing the env file at the brain where it is, and migrating it
behind a pre-image. Every refusal is rc 2 and names its reason:
  * the path is missing, or relative;
  * it has no `memories` table;
  * `--brain-db` or `--init-blank` was also given.
`--dry-run` prints the pre-image path it would write and writes nothing.
The wet run (Tier 0 only here) writes WIZARD_LIBRARY_DB='<path>' into the env
file, writes the pre-image BESIDE the brain with sqlite3's backup API (never cp)
and reads it back, migrates the brain in place, and lays NO copy of it under
the brain directory.

Every run goes into a scratch $HOME under ~/.crucible_scratch (F4). The brain
attached is the package's own schema-only blank DB (seed/claude_memory_blank.db,
36 objects, 0 memories) copied into the scratch, so the migration has a visible
delta and nothing real is ever touched.

At the parent commit `--attach-db` is an unknown flag: rc 2, "Unknown arg" --
red on every test below.

These tests bind the CHECKOUT (install.sh is root=null: it ships, it is not
laid). On an installed brain they SKIP with the reason -- _require_checkout().
"""
from __future__ import annotations

import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(os.environ.get("VOIDLOOM_ROOT") or Path(__file__).resolve().parents[2]).resolve()
SH = ROOT / "install.sh"
BLANK = ROOT / "seed" / "claude_memory_blank.db"
SCRATCH = Path(os.environ.get("VOIDLOOM_SCRATCH") or (Path.home() / ".crucible_scratch")).resolve()
ENV = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}


def _require_checkout():
    """These tests bind the CHECKOUT (git index + the null-root front-door files). On an installed
    brain neither exists; a skip with the reason is the honest verdict (UNMEASURABLE, not PASS)."""
    if not (ROOT / "install.sh").is_file() or not (ROOT / ".git").exists():
        raise unittest.SkipTest("NOT A CHECKOUT: %s has no install.sh/.git (install.sh is root=null: ships, not laid). "
                                "Run from the package checkout or set VOIDLOOM_ROOT=<checkout>." % ROOT)


def _objects(db: Path) -> int:
    c = sqlite3.connect("file:%s?immutable=1" % db, uri=True)
    try:
        return c.execute("SELECT COUNT(*) FROM sqlite_master WHERE type IN ('table','view')").fetchone()[0]
    finally:
        c.close()


def _memories(db: Path) -> int:
    c = sqlite3.connect("file:%s?immutable=1" % db, uri=True)
    try:
        return c.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
    finally:
        c.close()


class TestAttachDb(unittest.TestCase):

    def setUp(self):
        _require_checkout()
        self.assertTrue(SH.is_file(), "no install.sh at %s" % ROOT)
        self.assertTrue(BLANK.is_file(), "seed/claude_memory_blank.db absent")
        SCRATCH.mkdir(parents=True, exist_ok=True)
        self.tmp = Path(tempfile.mkdtemp(prefix="attach_", dir=str(SCRATCH)))
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.brain_dir = self.tmp / "elsewhere"
        self.brain_dir.mkdir()
        self.brain = self.brain_dir / "claude_memory.db"
        shutil.copyfile(str(BLANK), str(self.brain))
        self.env = {**ENV, "HOME": str(self.home), "WIZARD_CLAUDE_HOME": str(self.home / ".claude"),
                    "WIZARD_ENV_FILE": str(self.home / ".voidloom" / "wizard-library-env"),
                    "WIZARD_EMBED_MODEL": "nomic", "TMPDIR": str(self.home), "PATH": os.environ.get("PATH", "")}

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, *args, timeout=600):
        # --force-cpu: no GPU is resolved, pinned or written into the scratch env
        # file, whatever cards this host has (the suite never imports torch).
        return subprocess.run(["bash", str(SH), "--force-cpu"] + list(args), capture_output=True, text=True,
                              env=self.env, timeout=timeout)

    def _home_written(self):
        return sorted(str(p.relative_to(self.home)) for p in self.home.rglob("*"))

    def test_flag_is_accepted_and_documented(self):
        sh = SH.read_text(encoding="utf-8")
        self.assertIsNotNone(re.search(r"^\s*--attach-db\)", sh, re.M), "install.sh does not parse --attach-db")
        self.assertIn("--attach-db", subprocess.run(["bash", str(SH), "--help"], capture_output=True,
                                                    text=True, env=self.env).stdout)

    def test_refuses_a_missing_path_rc2_and_touches_nothing(self):
        p = self._run("--attach-db", str(self.brain_dir / "nope.db"), "--tier0-only")
        self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
        self.assertIn("nope.db", p.stderr + p.stdout)
        self.assertEqual(self._home_written(), [])

    def test_refuses_a_relative_path_rc2(self):
        p = self._run("--attach-db", "relative/brain.db", "--tier0-only")
        self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
        self.assertIn("ABSOLUTE", p.stderr + p.stdout)

    def test_refuses_a_file_without_a_memories_table_rc2(self):
        not_a_brain = self.brain_dir / "not_a_brain.db"
        c = sqlite3.connect(str(not_a_brain)); c.execute("CREATE TABLE t(x)"); c.commit(); c.close()
        p = self._run("--attach-db", str(not_a_brain), "--tier0-only")
        self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
        self.assertIn("memories", p.stderr + p.stdout)
        self.assertEqual(self._home_written(), [])
        self.assertEqual(sorted(x.name for x in self.brain_dir.iterdir()),
                         ["claude_memory.db", "not_a_brain.db"], "the refusal left something beside the brain")

    def test_refuses_attach_with_brain_db_or_init_blank_rc2(self):
        for extra in (["--brain-db", str(BLANK)], ["--init-blank"]):
            p = self._run("--attach-db", str(self.brain), "--tier0-only", *extra)
            self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
            self.assertIn("one brain, one source", p.stderr + p.stdout)
        self.assertEqual(self._home_written(), [])

    def test_dry_run_prints_the_preimage_path_and_writes_nothing(self):
        before = sorted(x.name for x in self.brain_dir.iterdir())
        p = self._run("--attach-db", str(self.brain), "--tier0-only", "--dry-run")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        m = re.search(r"pre-image .*?(%s\.pre-attach-\d{8}T\d{6}Z\.db)" % re.escape(str(self.brain)), p.stdout)
        self.assertIsNotNone(m, "the dry run did not print the pre-image path it would write:\n" + p.stdout[-1500:])
        self.assertEqual(sorted(x.name for x in self.brain_dir.iterdir()), before, "a dry run wrote beside the brain")
        self.assertEqual(self._home_written(), [], "a dry run wrote into the scratch home")

    def test_wet_attach_migrates_in_place_behind_a_verified_preimage(self):
        objects_before, memories_before = _objects(self.brain), _memories(self.brain)
        p = self._run("--attach-db", str(self.brain), "--tier0-only")
        self.assertEqual(p.returncode, 0, (p.stdout + p.stderr)[-4000:])
        # 1. the env file names the attached brain, not a copy
        env_file = self.home / ".voidloom" / "wizard-library-env"
        self.assertTrue(env_file.is_file())
        self.assertIn("WIZARD_LIBRARY_DB='%s'" % self.brain, env_file.read_text(encoding="utf-8"))
        # 2. no copy of the brain under the brain directory
        self.assertFalse((self.home / ".claude" / "memory" / "claude_memory.db").exists(),
                         "the attach COPIED the brain into the brain directory")
        # 3. the pre-image sits beside the brain, opens, and holds the pre-migration state
        pre = sorted(self.brain_dir.glob("claude_memory.db.pre-attach-*.db"))
        self.assertEqual(len(pre), 1, "expected exactly one pre-image beside the brain: %s"
                         % sorted(x.name for x in self.brain_dir.iterdir()))
        self.assertEqual(_objects(pre[0]), objects_before, "the pre-image is not the pre-migration schema")
        self.assertEqual(_memories(pre[0]), memories_before)
        self.assertIn("pre-image verified by read-back", p.stdout)
        # 4. the brain itself was migrated in place: more objects than before, and seeded
        self.assertGreater(_objects(self.brain), objects_before, "the migrator did not touch the attached brain")
        self.assertGreater(_memories(self.brain), memories_before, "the agnostic seed pack was not loaded once")
        self.assertIn("attaching the existing brain", p.stdout)
        self.assertIn("self_check PASSED", p.stdout)

    def test_parent_shape_would_reject_the_flag(self):
        """Documents the red-at-parent: an installer that does not know the flag says so."""
        p = self._run("--attach-db")            # --force-cpu is prepended by _run; --attach-db is last, bare
        self.assertEqual(p.returncode, 2)
        self.assertIn("requires a path", p.stdout + p.stderr,
                      "a bare --attach-db must complain about the path, not about the flag")


if __name__ == "__main__":
    unittest.main()
