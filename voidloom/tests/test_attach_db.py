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
        here = self.home / ".claude" / "memory" / "claude_memory.db"
        self.assertTrue(here.is_symlink() or not here.exists(), "the attach COPIED the brain into the brain directory")
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

    def _attached(self):
        p = self._run("--attach-db", str(self.brain), "--tier0-only")
        self.assertEqual(p.returncode, 0, (p.stdout + p.stderr)[-4000:])
        c = sqlite3.connect(str(self.brain))
        c.execute("INSERT INTO memories (content, summary, category, importance) VALUES ('kept across the upgrade', 's', 'insight', 0.5)")
        c.commit(); c.close()
        return _memories(self.brain)

    def assert_still_attached(self, p, held):
        self.assertEqual(p.returncode, 0, (p.stdout + p.stderr)[-4000:])
        named = re.search(r"^WIZARD_LIBRARY_DB='([^']+)'", (self.home / ".voidloom" / "wizard-library-env")
                          .read_text(encoding="utf-8"), re.M).group(1)
        self.assertEqual(os.path.realpath(named), os.path.realpath(str(self.brain)), "the env file reaches the attached brain")
        here = self.home / ".claude" / "memory" / "claude_memory.db"
        self.assertTrue(here.is_symlink(), "a blank brain was built in its place")
        self.assertEqual(os.path.realpath(str(here)), os.path.realpath(str(self.brain)))
        self.assertEqual(_memories(self.brain), held)

    def test_saboteur_a_plain_re_run_keeps_the_attached_brain(self):
        held = self._attached()
        self.assert_still_attached(self._run("--tier0-only"), held)

    def test_saboteur_two_re_runs_then_a_missing_brain_still_stop_the_run(self):
        """The first re-run used to rewrite the record to the link's own path, and the second then built a blank brain."""
        held = self._attached()
        here = self.home / ".claude" / "memory" / "claude_memory.db"
        for _ in range(2):
            self.assert_still_attached(self._run("--tier0-only"), held)
            rec = (self.home / ".claude" / "memory" / "INSTALL_RECORD").read_text(encoding="utf-8")
            self.assertIn("db=%s\n" % os.path.realpath(str(self.brain)), rec)
            self.assertIn("attached=1\n", rec)
        self.brain.rename(self.brain.with_suffix(".moved"))
        p = self._run("--tier0-only")
        self.assertEqual(p.returncode, 2, (p.stdout + p.stderr)[-2000:])
        self.assertIn("is not there", p.stdout + p.stderr)
        self.assertTrue(here.is_symlink(), "a regular file was built where the link was")
        self.assertFalse(here.exists(), "nothing was built behind the link")

    def snapshot(self, *, memories=0, table=None, lock_row=None):
        snap = self.tmp / "snapshot.db"
        shutil.copyfile(str(BLANK), str(snap))
        c = sqlite3.connect(str(snap))
        for i in range(memories):
            c.execute("INSERT INTO memories (content, summary, category, importance) VALUES (?, 's', 'insight', 0.5)", ("own note %d" % i,))
        if table:
            c.execute("INSERT OR REPLACE INTO metadata (key, value) VALUES ('active_embedding_table', ?)", (table,))
        if lock_row:
            c.execute("INSERT OR REPLACE INTO metadata (key, value) VALUES ('embedder_lock_written', ?)", (lock_row,))
        c.commit(); c.close()
        return snap

    def test_saboteur_a_snapshot_with_memories_of_its_own_reads_as_an_existing_brain(self):
        p = self._run("--dry-run", "--brain-db", str(self.snapshot(memories=2)))
        self.assertEqual(p.returncode, 0, (p.stdout + p.stderr)[-2000:])
        self.assertIn("nothing in it would be embedded", p.stdout)

    def test_control_a_snapshot_of_the_seed_alone_stays_new(self):
        p = self._run("--dry-run", "--brain-db", str(self.snapshot()))
        self.assertNotIn("nothing in it would be embedded", p.stdout)

    def test_saboteur_a_snapshot_keeps_its_own_tier(self):
        snap = self.snapshot(memories=2, table="memory_embeddings_v2")
        c = sqlite3.connect(str(snap))
        c.execute("CREATE TABLE IF NOT EXISTS memory_embeddings_v2_rowids (rowid INTEGER PRIMARY KEY)")
        c.execute("INSERT INTO memory_embeddings_v2_rowids (rowid) VALUES (1)")
        c.commit(); c.close()
        self.env.pop("WIZARD_EMBED_MODEL")
        p = self._run("--dry-run", "--brain-db", str(snap))
        self.assertEqual(p.returncode, 2, (p.stdout + p.stderr)[-2000:])
        self.assertIn("this brain embeds under jina, and --force-cpu does not change", p.stdout + p.stderr)

    def test_saboteur_reading_a_snapshot_leaves_nothing_beside_it(self):
        snap = self.snapshot(memories=1)
        c = sqlite3.connect(str(snap)); c.execute("PRAGMA journal_mode=WAL"); c.commit(); c.close()
        self.assertEqual(self._run("--dry-run", "--brain-db", str(snap)).returncode, 0)
        self.assertEqual(sorted(p.name for p in self.tmp.iterdir() if p.name.startswith("snapshot")), ["snapshot.db"])

    def test_saboteur_a_tier0_run_over_a_locked_brain_says_doctor_will_not_be_healthy(self):
        p = self._run("--tier0-only", "--brain-db", str(self.snapshot(memories=2, lock_row="cpu")))
        self.assertEqual(p.returncode, 5, (p.stdout + p.stderr)[-2000:])       # recall refuses this brain: the gate says so
        self.assertIn("recall gate: the engine admits this brain", p.stdout + p.stderr)
        self.assertIn("this brain was locked and its lock is not beside it", p.stdout + p.stderr)
        self.assertIn("init --tier cpu", p.stdout + p.stderr)

    def test_control_a_tier0_run_on_a_brain_never_locked_says_nothing(self):
        p = self._run("--tier0-only", "--brain-db", str(self.snapshot(memories=2)))
        self.assertEqual(p.returncode, 0, (p.stdout + p.stderr)[-2000:])
        self.assertNotIn("its lock is not beside it", p.stdout + p.stderr)

    def test_saboteur_a_record_that_names_the_link_itself_still_stops_the_run(self):
        """What an earlier re-run left behind: the record names the link, so only the link can say the brain is gone."""
        self._attached()
        here = self.home / ".claude" / "memory"
        rec = here / "INSTALL_RECORD"
        text = re.sub(r"^attached=.*\n", "", rec.read_text(encoding="utf-8"), flags=re.M)
        rec.write_text(re.sub(r"^db=.*$", "db=%s" % (here / "claude_memory.db"), text, flags=re.M), encoding="utf-8")
        self.brain.rename(self.brain.with_suffix(".moved"))
        p = self._run("--tier0-only")
        self.assertEqual(p.returncode, 2, (p.stdout + p.stderr)[-2000:])
        self.assertIn("is not there", p.stdout + p.stderr)
        self.assertTrue((here / "claude_memory.db").is_symlink(), "a regular file was built where the link was")

    def test_control_a_folder_copied_to_a_new_home_is_not_called_attached(self):
        p = self._run("--tier0-only", "--init-blank")
        self.assertEqual(p.returncode, 0, (p.stdout + p.stderr)[-2000:])
        old = self.home / ".claude"
        self.assertIn("attached=0\n", (old / "memory" / "INSTALL_RECORD").read_text(encoding="utf-8"))
        moved = self.tmp / "newhome"
        moved.mkdir()
        shutil.copytree(str(old), str(moved / ".claude"), symlinks=True)
        self.env.update(HOME=str(moved), WIZARD_CLAUDE_HOME=str(moved / ".claude"),
                        WIZARD_ENV_FILE=str(moved / ".voidloom" / "wizard-library-env"), TMPDIR=str(moved))
        p = self._run("--tier0-only")
        self.assertNotIn("keeping the attached brain", p.stdout + p.stderr)
        self.assertEqual(p.returncode, 0, (p.stdout + p.stderr)[-2000:])

    def test_saboteur_a_record_older_than_the_key_with_no_brain_here_is_attached(self):
        held = self._attached()
        here = self.home / ".claude" / "memory"
        rec = here / "INSTALL_RECORD"
        rec.write_text(re.sub(r"^attached=.*\n", "", rec.read_text(encoding="utf-8"), flags=re.M), encoding="utf-8")
        (here / "claude_memory.db").unlink()
        p = self._run("--tier0-only")
        self.assertIn("keeping the attached brain", p.stdout + p.stderr)
        self.assert_still_attached(p, held)

    def test_saboteur_no_install_step_needs_readlink_f(self):
        """Absent before macOS 12.3: the keep-attached branch compared two empty strings there and skipped itself."""
        stub = self.tmp / "bin"
        stub.mkdir()
        (stub / "readlink").write_text('#!/bin/sh\ncase "$1" in -f) echo "readlink: illegal option -- f" >&2; exit 1 ;; esac\n'
                                       'exec /usr/bin/readlink "$@"\n')
        (stub / "readlink").chmod(0o755)
        held = self._attached()
        self.env["PATH"] = str(stub) + os.pathsep + self.env["PATH"]
        (self.home / ".claude" / "memory" / "claude_memory.db").unlink()
        p = self._run("--tier0-only")
        self.assertIn("keeping the attached brain", p.stdout + p.stderr)
        self.assert_still_attached(p, held)
        p = subprocess.run(["bash", str(ROOT / "uninstall.sh"), "--yes", "--remove-brain"], capture_output=True, text=True,
                           env={**self.env, "VOIDLOOM_CONFIRM_REMOVE_BRAIN": "delete my memories"}, timeout=300)
        self.assertEqual(p.returncode, 0, (p.stdout + p.stderr)[-2000:])
        self.assertIn("KEPT     %s" % os.path.realpath(str(self.brain)), p.stdout)

    def test_saboteur_a_record_with_no_link_keeps_the_attached_brain(self):
        """The shape a v1.0 attached install leaves: the record names the brain, and nothing lies beside the organs."""
        held = self._attached()
        (self.home / ".claude" / "memory" / "claude_memory.db").unlink()
        p = self._run("--tier0-only")
        self.assertIn("keeping the attached brain", p.stdout + p.stderr)
        self.assert_still_attached(p, held)

    def test_saboteur_an_attached_brain_that_is_not_there_stops_the_run(self):
        self._attached()
        self.brain.rename(self.brain.with_suffix(".moved"))
        p = self._run("--tier0-only")
        self.assertEqual(p.returncode, 2, (p.stdout + p.stderr)[-2000:])
        self.assertIn("is not there", p.stdout + p.stderr)
        self.assertTrue((self.home / ".claude" / "memory" / "claude_memory.db").is_symlink(), "nothing was built")

    def test_saboteur_a_malformed_lock_stops_the_installer_with_a_way_out_that_works(self):
        self._attached()
        lock = self.brain_dir / "embedder.lock.json"
        lock.write_text("{ not json")
        p = self._run("--tier0-only")
        self.assertEqual(p.returncode, 2, (p.stdout + p.stderr)[-2000:])
        fix = re.search(r"mv '[^']+' '[^']+'", p.stderr)
        self.assertIsNotNone(fix, p.stderr[-800:])
        self.assertEqual(subprocess.run(fix.group(0), shell=True, timeout=30).returncode, 0)
        p = self._run("--tier0-only")
        self.assertEqual(p.returncode, 0, (p.stdout + p.stderr)[-2000:])

    def test_control_a_stale_record_beside_a_real_brain_changes_nothing(self):
        """A home that moved: the record names a path that is gone, and the brain sits where it always did."""
        p = self._run("--tier0-only", "--init-blank")
        self.assertEqual(p.returncode, 0, (p.stdout + p.stderr)[-2000:])
        rec = self.home / ".claude" / "memory" / "INSTALL_RECORD"
        if rec.exists():
            rec.write_text(re.sub(r"^db=.*$", "db=/nonexistent/old/place/claude_memory.db", rec.read_text(encoding="utf-8"), flags=re.M),
                           encoding="utf-8")
        p = self._run("--tier0-only")
        self.assertEqual(p.returncode, 0, (p.stdout + p.stderr)[-2000:])

    def test_control_init_blank_is_the_way_to_start_again(self):
        self._attached()
        self.assertNotIn("keeping the attached brain", self._dry("--init-blank"))

    def _dry(self, *args):
        p = self._run("--dry-run", "--tier0-only", *args)
        return p.stdout + p.stderr

    def test_saboteur_uninstall_says_an_attached_brain_stays_and_leaves_it(self):
        self._attached()
        p = subprocess.run(["bash", str(ROOT / "uninstall.sh"), "--yes", "--remove-brain"], capture_output=True, text=True,
                           env={**self.env, "VOIDLOOM_CONFIRM_REMOVE_BRAIN": "delete my memories"}, timeout=300)
        self.assertEqual(p.returncode, 0, (p.stdout + p.stderr)[-2000:])
        self.assertTrue(self.brain.is_file(), "the attached file is the owner's")
        self.assertIn("KEPT", p.stdout)
        self.assertIn(str(self.brain), p.stdout)

    def test_parent_shape_would_reject_the_flag(self):
        """Documents the red-at-parent: an installer that does not know the flag says so."""
        p = self._run("--attach-db")            # --force-cpu is prepended by _run; --attach-db is last, bare
        self.assertEqual(p.returncode, 2)
        self.assertIn("requires a path", p.stdout + p.stderr,
                      "a bare --attach-db must complain about the path, not about the flag")


if __name__ == "__main__":
    unittest.main()
